# osouji-robot 🧹🤖

Jetson + ESP32-S3 + Raspberry Pi Pico 2 で作る、四輪お掃除ロボット。
ジェスチャー・音声・ローカルLLMで指示を出し、ROS2でSLAM/自律走行を目指す。

組み込み歴3ヶ月、独学、開発の様子はTwitterで毎日実況中。

---

## いま動くもの (Day 1)

| 機能 | 中身 | 状態 |
|------|------|------|
| ジェスチャー操作 | MediaPipe Hands。**グー(拳)= とまれ / パー(開手)= すすめ** | ✅ 動く |
| 音声会話 | faster-whisper で音声認識 → Ollama(Gemma)で応答 | ✅ 動く |
| Docker開発環境 | Jetson到着前にPC上のDockerで先行開発(カメラ/音声/X11対応) | ✅ 動く |

## これから (Roadmap / Future Work)

- [ ] ESP32-S3 でウェイクワード検出（「オッケーロボ」等）
- [ ] Pico 2 でモーター制御 + エンコーダ読み取り → micro-ROS で ROS2 連携
- [ ] ROS2 + slam_toolbox で地図作成 (SLAM)
- [ ] オドメトリ + 自律走行でお掃除ルート
- [ ] YOLO で障害物・ゴミ検出
- [ ] ロボットアーム搭載（MaxArm / 吸引ノズル）※予算次第

---

## セットアップ (Ubuntu / Linux)

### 方法A: Docker（推奨・Jetson環境に近い）

\`\`\`bash
# X11 をコンテナに許可
xhost +local:docker

# 起動（初回は Gemma を自動 pull）
cd docker
docker compose up --build
\`\`\`

### 方法B: ネイティブ venv（今日の高速イテレーション向け）

\`\`\`bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# ジェスチャー操作
python perception/gesture_control.py

# 音声会話（別途 Ollama を起動し gemma2:2b を pull しておく）
ollama pull gemma2:2b
python voice/voice_chat.py
\`\`\`

> 💡 **今日のおすすめ**: まず方法Bのネイティブでカメラ/マイクの動作を確認 → 安定したらDockerに載せ替える。
> Dockerはハマると原因切り分けが増えるので、動作確認はネイティブが早い。

---

## ディレクトリ構成

\`\`\`
osouji-robot/
├── README.md
├── requirements.txt
├── .gitignore
├── perception/
│   └── gesture_control.py     # MediaPipe: グー/パー → とまれ/すすめ
├── voice/
│   └── voice_chat.py          # Whisper → Gemma 会話ループ
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── docs/
    └── day01.md               # 開発ログ（Twitter用メモ）
\`\`\`

## ライセンス

MIT
