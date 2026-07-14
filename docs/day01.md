# Day 1 開発ログ

## やったこと
- プロジェクト雛形を作成（perception / voice / docker）
- MediaPipe Hands で **グー=とまれ / パー=すすめ** のジェスチャー判定を実装
- faster-whisper（音声認識）→ Ollama/Gemma（会話）の音声チャットを実装
- Ubuntu 向け Docker 環境（カメラ・音声・X11・Ollama）を用意

## ハマりそうな所メモ
- Docker で GUI 表示するには起動前に `xhost +local:docker`
- カメラは `/dev/video0`、音声は `/dev/snd` をコンテナに渡す
- Whisper は CPU なら `compute_type="int8"`、GPU なら `device="cuda"`
- Jetson 移行時は Dockerfile を `l4t` ベース(arm64)に差し替え

## 次にやること
- ネイティブでカメラ/マイクの実機確認
- Pico 2 でモーター制御 → micro-ROS で ROS2 連携
