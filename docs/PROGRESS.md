# 進捗＆引き継ぎメモ（次のチャットはまずこれを読む）

## このプロジェクトは何
四輪お掃除ロボット。ジェスチャー・音声・ローカルLLMで指示、ROS2でSLAM/自律走行を目指す。
作者は組み込み歴3ヶ月・独学。再就職ポートフォリオ用。開発はTwitterで実況。

## 環境
- 開発PC: Windows。ジェスチャー/音声(カメラ・マイク)は **Windows側で** 動かす（Python venv）。
- ROS2: **WSL2 (Ubuntu 24.04) の ROS2 Jazzy**。Gazebo Harmonic 8.11 導入済み。
- Jetson: 未着（8GBモデル、マーケット品）。到着後はJetsonがUbuntuなので開発の主戦場を移す。
  → 到着後の手順は docs/jetson_setup.md。

## いま動くもの（Day1完了）
1. `perception/gesture_control.py`（Windows）: MediaPipe Tasks API。パー=GO / グー=STOP。
   判定を `C:\Users\30day\osouji_cmd.txt` に書き出す。
2. `voice/voice_chat.py`（Windows）: faster-whisper で音声認識 → Ollama(Gemma)で会話。
   「進め/止まれ/戻れ/右/左」を抽出して同じファイルに書き出す。
3. `ros2_bridge/turtle_gesture_bridge.py`（WSL2）: 上のファイルを読んで cmd_vel を publish。
   - `python3 ..._bridge.py`      → turtlesim (Twist, /turtle1/cmd_vel)
   - `python3 ..._bridge.py tb3`  → TurtleBot3 Gazebo (TwistStamped, /cmd_vel)
   → **ジェスチャーでも音声でも、Gazeboの TurtleBot3 を動かせるところまで完成。**

## 動かし方（Gazeboで手/声運転）
WSL側:
  1) export TURTLEBOT3_MODEL=burger
     ros2 launch turtlebot3_gazebo turtlebot3_world.launch.py
  2) python3 "/mnt/c/Users/30day/OneDrive/デスクトップ/RobotProject/osouji-robot/ros2_bridge/turtle_gesture_bridge.py" tb3
Windows側（どちらか片方。同時に動かすとファイルを取り合うのでNG）:
  3a) python perception\gesture_control.py    （venv有効化: .venv\Scripts\activate）
  3b) python voice\voice_chat.py

## ハマった所＆学び（面接ネタにもなる）
- mediapipe 0.10.35 は旧 `mp.solutions` 廃止 → Tasks API(HandLandmarker)に移行。
- MediaPipeは日本語パスからモデルを開けない → Pythonでバイト読み込みして渡す。
- 新Gazebo(Jazzy)の /cmd_vel は **TwistStamped**（従来のTwistではない）。turtlesimはTwistのまま。
- ロボが「動かない」→実はodometryは増加、13m先へ爆走して画面外。Entity Tree→右クリック→Follow で追える。
- /cmd_vel に publisher が2つ（テストpub + 橋）いると喧嘩して回転する。

## 次にやること（候補）
- [ ] Ctrl+Cで綺麗に終了する処理（gesture_control.pyのトレースバック抑制）※任意
- [ ] Gazebo格上げの安定化 / RViz可視化
- [ ] Jetson到着 → セットアップ（docs/jetson_setup.md）→ 実機へ移植
- [ ] Pico 2 でモーター制御 + エンコーダ → micro-ROS
- [ ] SLAM(slam_toolbox / cartographer)、自律走行、YOLO物体検出
- [ ] ロボットアーム(MaxArm)※予算次第

## 次のチャットでの再開の仕方
新しいチャットで RobotProject フォルダをつないだ状態にして、こう言うだけ:
「osouji-robot の続きをやりたい。docs/PROGRESS.md を読んで状況把握して」
