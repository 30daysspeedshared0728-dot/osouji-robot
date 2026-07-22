#!/usr/bin/env python3
"""
gesture_control.py  (MediaPipe Tasks API 版)

新しい mediapipe (0.10.x, solutions 廃止済み) で動く版。
  グー(拳/指0本)  -> STOP  / とまれ
  パー(開手/指5本) -> GO    / すすめ

初回起動時に hand_landmarker.task モデルを自動ダウンロードします。
確定したコマンドは on_command() で外に出す。将来ここを ROS2 publish に差し替え。

使い方:
    python perception/gesture_control.py
    q キーで終了
"""
import os
import platform
import urllib.request

import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision
from PIL import Image, ImageDraw, ImageFont

# Jetson側の処理負荷を抑えるためのカメラ解像度
CAM_WIDTH = 640
CAM_HEIGHT = 480

# cv2.putText は Hershey フォントのみでCJKを描けず文字化けするため、
# 日本語表示はPillow + Noto Sans CJK JP で描画する。
JP_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]
_JP_FONT_PATH = next((p for p in JP_FONT_CANDIDATES if os.path.exists(p)), None)
_jp_font_cache = {}


def _jp_font(size):
    if _JP_FONT_PATH is None:
        return None
    font = _jp_font_cache.get(size)
    if font is None:
        font = ImageFont.truetype(_JP_FONT_PATH, size)
        _jp_font_cache[size] = font
    return font


def draw_texts_jp(frame, items):
    """日本語を含むテキストをまとめて描画する。

    items: (text, (x, y), font_size, color_bgr) のリスト。
    フォントが見つからない場合は cv2.putText にフォールバックする(文字化けするが最低限は表示される)。
    """
    font_size = max((it[2] for it in items), default=20)
    font = _jp_font(font_size)
    if font is None:
        for text, org, size, color in items:
            cv2.putText(frame, text, org, cv2.FONT_HERSHEY_SIMPLEX,
                        size / 30, color, 2)
        return frame

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_img)
    for text, org, size, color in items:
        draw.text(org, text, font=_jp_font(size), fill=(color[2], color[1], color[0]))
    rgb = np.array(pil_img)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

# --- モデル(自動ダウンロード) ---
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)


def ensure_model():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        print("hand_landmarker.task をダウンロード中...(初回のみ)")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("ダウンロード完了")


# 手の骨格を描くための接続(21ランドマーク)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


def _dist(a, b):
    """2点間の距離(画面上の相対値)。"""
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2) ** 0.5


# 手の開き具合のしきい値(自分の手・カメラに合わせてここを調整する)
OPEN_STOP_MAX = 1.40   # これ未満 = グー(とまれ)
OPEN_GO_MIN = 1.80     # これ超   = パー(すすめ)


def hand_openness(landmarks):
    """手の開き具合を1つの数値で表す(指を1本ずつ数えないので傾きに強い)。

    手のサイズ(手首→中指の付け根)で正規化した、4本指の指先の平均距離。
    グー ≈ 1.0前後 / パー ≈ 2.0前後 になる。
    指が1本くらい傾いても、全体の平均なので結果が安定する。
    """
    wrist = landmarks[0]
    scale = _dist(wrist, landmarks[9]) or 1e-6   # 中指の付け根までの距離=手のサイズ
    tips = [8, 12, 16, 20]
    return sum(_dist(wrist, landmarks[t]) for t in tips) / len(tips) / scale


def classify(openness):
    if openness < OPEN_STOP_MAX:
        return "STOP"   # グー(拳) -> とまれ
    if openness > OPEN_GO_MIN:
        return "GO"     # パー(開手) -> すすめ
    return None         # 中間(半開き)は保留


COMMAND_JP = {"STOP": "とまれ", "GO": "すすめ"}
COMMAND_COLOR = {"STOP": (0, 0, 255), "GO": (0, 200, 0)}


# WSL2側のROS2ノードが読み取る「橋渡し用」ファイル(ASCIIパスで両OSが一致)
BRIDGE_FILE = os.path.join(os.path.expanduser("~"), "osouji_cmd.txt")


def on_command(command):
    """コマンド確定時のフック。print + ブリッジ用ファイルに書き出す。
    WSL2側のROS2ノードがこのファイルを読んで turtlesim に流す。
    将来Jetsonでは、ここを直接ROS2 publishに差し替える。"""
    print(f"[COMMAND] {command}  ({COMMAND_JP[command]})")
    try:
        with open(BRIDGE_FILE, "w", encoding="utf-8") as f:
            f.write(command)
    except OSError:
        pass


def draw_hand(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 255, 0), 2)
    for p in pts:
        cv2.circle(frame, p, 4, (0, 0, 255), -1)


def open_camera():
    # Windows は DSHOW を指定すると開きが速く安定する。Linux(Jetson)は通常オープン。
    if platform.system() == "Windows":
        cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(0)
    # Jetsonでの処理負荷軽減のため解像度を落とす(カメラがサポートしていれば有効)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAM_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAM_HEIGHT)
    return cap


def main():
    ensure_model()
    # 日本語を含むパスだと MediaPipe の内部(C++)がファイルを開けないため、
    # Python 側でバイト列として読み込んで渡す(パス問題を回避)。
    with open(MODEL_PATH, "rb") as f:
        model_data = f.read()

    # Tasks API (HandLandmarker) には旧 solutions API の model_complexity 引数は無く、
    # モデルの複雑度はダウンロードするモデルファイル自体で決まる(hand_landmarkerはlite相当の1種類のみ)。
    # 手を1つだけ検出することで負荷を抑える。
    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=model_data),
        num_hands=1,
        running_mode=vision.RunningMode.IMAGE,
    )
    detector = vision.HandLandmarker.create_from_options(options)

    cap = open_camera()
    if not cap.isOpened():
        print("カメラを開けません。別アプリがカメラを使っていないか確認してください。")
        return

    DEBOUNCE = 5
    candidate = None
    streak = 0
    current_command = None

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame = cv2.flip(frame, 1)  # 鏡像
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=np.ascontiguousarray(rgb),
        )
        result = detector.detect(mp_image)

        detected = None
        text_items = []
        if result.hand_landmarks:
            lms = result.hand_landmarks[0]
            openness = hand_openness(lms)
            detected = classify(openness)
            draw_hand(frame, lms)
            # 開き具合を画面表示(この数字を見てしきい値を調整する)
            text_items.append((f"open: {openness:.2f}", (10, 55), 22, (255, 255, 0)))

        # デバウンス
        if detected == candidate and detected is not None:
            streak += 1
        else:
            candidate = detected
            streak = 1

        if streak == DEBOUNCE and candidate != current_command and candidate is not None:
            current_command = candidate
            on_command(current_command)

        if current_command:
            label_txt = f"{current_command} / {COMMAND_JP[current_command]}"
            color = COMMAND_COLOR[current_command]
            cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), color, -1)
            text_items.append((label_txt, (10, 6), 26, (255, 255, 255)))

        if text_items:
            frame = draw_texts_jp(frame, text_items)

        cv2.imshow("osouji-robot | gesture (q=quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
