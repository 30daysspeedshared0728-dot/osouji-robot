#!/usr/bin/env python3
"""
see_raw_data.py  --  MediaPipe の「生データ」を見るための学習用スクリプト

gesture_control.py と同じ手認識をするが、判定はせず、
検出された 21個の関節点(ランドマーク)の生の数値を
ターミナルに流すだけ。まず「素材」を自分の目で見るのが目的。

見るポイント:
  - x, y : 画面上の位置。0.0〜1.0 に正規化されてる(左上が0,0 / 右下が1,1)
  - z    : 奥行き。手首を基準にした相対値。手前がマイナス、奥がプラス寄り
  - 信頼度(score) : 検出の自信度。手を横に傾けると下がるのを観察してみて

使い方:
    python perception/see_raw_data.py
    q キーで終了
"""
import os
import time
import urllib.request

import numpy as np
import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

# --- モデル(gesture_control.py と同じものを使い回す) ---
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
MODEL_PATH = os.path.join(MODEL_DIR, "hand_landmarker.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

# 21個のランドマークの名前(MediaPipe公式の番号どおり)
LANDMARK_NAMES = [
    "WRIST(手首)",
    "THUMB_CMC(親指付根)", "THUMB_MCP(親指第3関節)", "THUMB_IP(親指第2関節)", "THUMB_TIP(親指先)",
    "INDEX_MCP(人差指付根)", "INDEX_PIP(人差指第2関節)", "INDEX_DIP(人差指第1関節)", "INDEX_TIP(人差指先)",
    "MIDDLE_MCP(中指付根)", "MIDDLE_PIP(中指第2関節)", "MIDDLE_DIP(中指第1関節)", "MIDDLE_TIP(中指先)",
    "RING_MCP(薬指付根)", "RING_PIP(薬指第2関節)", "RING_DIP(薬指第1関節)", "RING_TIP(薬指先)",
    "PINKY_MCP(小指付根)", "PINKY_PIP(小指第2関節)", "PINKY_DIP(小指第1関節)", "PINKY_TIP(小指先)",
]

HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),
    (0, 5), (5, 6), (6, 7), (7, 8),
    (5, 9), (9, 10), (10, 11), (11, 12),
    (9, 13), (13, 14), (14, 15), (15, 16),
    (13, 17), (17, 18), (18, 19), (19, 20),
    (0, 17),
]


def ensure_model():
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_DIR, exist_ok=True)
        print("hand_landmarker.task をダウンロード中...(初回のみ)")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("ダウンロード完了")


def draw_hand(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in landmarks]
    for a, b in HAND_CONNECTIONS:
        cv2.line(frame, pts[a], pts[b], (0, 180, 216), 2)
    for i, p in enumerate(pts):
        cv2.circle(frame, p, 4, (118, 185, 0), -1)
        cv2.putText(frame, str(i), (p[0] + 5, p[1]),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)


def dump_raw(landmarks, handedness):
    """検出結果の生データをターミナルに整形して表示する。"""
    label = handedness[0].category_name          # "Left" or "Right"
    score = handedness[0].score                  # 検出の信頼度(0〜1)
    print("\n" + "=" * 64)
    print(f"✋ 手を検出  |  左右: {label}   信頼度(score): {score:.3f}")
    print("-" * 64)
    print(f"{'#':>2}  {'名前':<22} {'x':>7} {'y':>7} {'z':>8}")
    for i, lm in enumerate(landmarks):
        print(f"{i:>2}  {LANDMARK_NAMES[i]:<22} {lm.x:>7.3f} {lm.y:>7.3f} {lm.z:>8.3f}")
    print("=" * 64)


def main():
    ensure_model()
    # 日本語パス対策: モデルはバイト列で渡す
    with open(MODEL_PATH, "rb") as f:
        model_data = f.read()

    options = vision.HandLandmarkerOptions(
        base_options=python.BaseOptions(model_asset_buffer=model_data),
        num_hands=1,
        running_mode=vision.RunningMode.IMAGE,
    )
    detector = vision.HandLandmarker.create_from_options(options)

    cap = cv2.VideoCapture(0, cv2.CAP_DSHOW)
    if not cap.isOpened():
        print("カメラを開けません。他アプリがカメラを使ってないか確認してください。")
        return

    print("起動したで。手をカメラに写すと、生データが下に流れる。")
    print("手を正面→横に傾けて、信頼度(score)がどう変わるか見てみて。q で終了。\n")

    last_print = 0.0     # 読めるように 0.7秒に1回だけ表示する

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.flip(frame, 1)
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB,
                            data=np.ascontiguousarray(rgb))
        result = detector.detect(mp_image)

        if result.hand_landmarks:
            lms = result.hand_landmarks[0]
            draw_hand(frame, lms)
            now = time.time()
            if now - last_print > 0.7:
                dump_raw(lms, result.handedness[0])
                last_print = now

        cv2.imshow("see_raw_data (q=quit)", frame)
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()
    print("\n終了。おつかれ！")


if __name__ == "__main__":
    main()
