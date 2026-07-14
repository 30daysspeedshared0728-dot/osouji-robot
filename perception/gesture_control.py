#!/usr/bin/env python3
"""
gesture_control.py
MediaPipe Hands で手を検出し、
  グー(拳/指0本)  -> STOP  / とまれ
  パー(開手/指5本) -> GO    / すすめ
を判定する。

デバウンス(数フレーム連続で同じ判定が続いたら確定)付き。
確定したコマンドは on_command() で外に出す。
将来ここを ROS2 パブリッシャ差し替えで cmd_vel に繋ぐ。

使い方:
    python perception/gesture_control.py
    q キーで終了
"""
import cv2
import mediapipe as mp

mp_hands = mp.solutions.hands
mp_draw = mp.solutions.drawing_utils
mp_styles = mp.solutions.drawing_styles

# 指先ランドマークと、その第2関節(PIP)のインデックス
# 親指は横方向で判定するので別扱い
FINGER_TIPS = [8, 12, 16, 20]   # 人差し, 中, 薬, 小
FINGER_PIPS = [6, 10, 14, 18]
THUMB_TIP = 4
THUMB_IP = 3
THUMB_MCP = 2


def count_extended_fingers(landmarks, handedness_label):
    """伸びている指の本数を数える(0=グー, 5=パー)。"""
    count = 0

    # 人差し〜小指: 指先が第2関節より上(y が小さい)なら伸びている
    for tip, pip in zip(FINGER_TIPS, FINGER_PIPS):
        if landmarks[tip].y < landmarks[pip].y:
            count += 1

    # 親指: 横方向で判定。手の左右で符号が変わる
    if handedness_label == "Right":
        if landmarks[THUMB_TIP].x < landmarks[THUMB_IP].x:
            count += 1
    else:  # Left
        if landmarks[THUMB_TIP].x > landmarks[THUMB_IP].x:
            count += 1

    return count


def classify(num_fingers):
    """本数からコマンドへ。曖昧な時は None(=判定保留)。"""
    if num_fingers == 0:
        return "STOP"   # グー -> とまれ
    if num_fingers >= 5:
        return "GO"     # パー -> すすめ
    return None


COMMAND_JP = {"STOP": "とまれ", "GO": "すすめ"}
COMMAND_COLOR = {"STOP": (0, 0, 255), "GO": (0, 200, 0)}


def on_command(command):
    """コマンド確定時のフック。今は print。将来 ROS2 publish に差し替える。"""
    print(f"[COMMAND] {command}  ({COMMAND_JP[command]})")


def main():
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("カメラを開けません。/dev/video0 を確認してください。")
        return

    # デバウンス: 同じ判定が DEBOUNCE 回続いたら確定
    DEBOUNCE = 5
    candidate = None
    streak = 0
    current_command = None

    with mp_hands.Hands(
        model_complexity=0,
        max_num_hands=1,
        min_detection_confidence=0.6,
        min_tracking_confidence=0.6,
    ) as hands:
        while cap.isOpened():
            ok, frame = cap.read()
            if not ok:
                break

            frame = cv2.flip(frame, 1)  # 鏡像にして直感的に
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = hands.process(rgb)

            detected = None
            if result.multi_hand_landmarks:
                hand_lms = result.multi_hand_landmarks[0]
                label = result.multi_handedness[0].classification[0].label
                n = count_extended_fingers(hand_lms.landmark, label)
                detected = classify(n)

                mp_draw.draw_landmarks(
                    frame, hand_lms, mp_hands.HAND_CONNECTIONS,
                    mp_styles.get_default_hand_landmarks_style(),
                    mp_styles.get_default_hand_connections_style(),
                )
                cv2.putText(frame, f"fingers: {n}", (10, 70),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 0), 2)

            # デバウンス処理
            if detected == candidate and detected is not None:
                streak += 1
            else:
                candidate = detected
                streak = 1

            if streak == DEBOUNCE and candidate != current_command and candidate is not None:
                current_command = candidate
                on_command(current_command)

            # 画面表示
            if current_command:
                label_txt = f"{current_command} / {COMMAND_JP[current_command]}"
                color = COMMAND_COLOR[current_command]
                cv2.rectangle(frame, (0, 0), (frame.shape[1], 40), color, -1)
                cv2.putText(frame, label_txt, (10, 30),
                            cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)

            cv2.imshow("osouji-robot | gesture (q=quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
