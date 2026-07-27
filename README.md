# osouji-robot 🧹🤖

**Jetson Orin Nano + ESP32-S3 + RP2350 で作る、音声・視覚・ジェスチャーで動く自律ロボット。**
ウェイクワード検出から応答生成まで **すべてローカル・完全オフライン**で動作します。

組み込み歴3ヶ月・独学。開発の様子は X ([@AtushiRobotics](https://x.com/AtushiRobotics)) で実況中。

---

## ハードウェア構成

| 役割 | デバイス | 担当 |
|---|---|---|
| 頭脳 | **Jetson Orin Nano (Super) 8GB** | 音声認識・LLM・物体検出・全体制御 |
| 耳 | **XIAO ESP32-S3 Sense** | ウェイクワード検出（常時待受・PDMマイク内蔵） |
| 筋肉 | **Raspberry Pi Pico 2 (RP2350)** | PWM によるサーボ駆動 |

```
XIAO ESP32-S3 ──USB(WAKE)──▶ Jetson Orin Nano ──UART──▶ Pico 2 ──PWM──▶ サーボ
  WakeNet常時待受              Whisper / LLM / YOLO        リアルタイム制御
```

処理の重さに応じて役割を分けています。常時待受は数十mWのマイコン、重い推論だけ Jetson が起きる構成です。

---

## いま動くもの（すべて実機で確認済み）

| 機能 | 実装 | 備考 |
|---|---|---|
| **ウェイクワード** | ESP32-S3 + WakeNet9（"Hi ESP"） | 学習ゼロ・完全オフライン・常時待受 |
| **音声認識** | faster-whisper (ja) + VAD | 無音で自動停止。`avg_logprob` で自信度を算出 |
| **雑談応答** | Ollama + gemma2:2b (GPU) | TTS向けに絵文字・記号・Markdownを除去 |
| **音声合成** | pyopenjtalk | 漢字の読みに対応 |
| **物体認識** | YOLO (ultralytics, CPU実行) | 枠から左右・遠近を算出し「手前右にコップ」等の空間情報をLLMに渡す |
| **情景説明 (VLM)** | moondream → gemma2 で和訳 | 「詳しく」と言われた時だけ起動 |
| **ジェスチャー操作** | MediaPipe Tasks | グー = 停止 / パー = 前進 |
| **アクチュエータ制御** | UART (`/dev/ttyTHS1`) → Pico | 双方向。Pico が受信確証を返す |
| **エピソード記憶** | JSONL 追記 | 毎ターン `{ts, user, command, reply, eval}` を記録 |

メインループは直下の `main.py` の1本にまとまっています（旧 `voice/wake_listen.py`）。

---

## 設計上の要点

### 1. 3層アーキテクチャ — LLM を通さない経路を持つ

```
熟慮層  LLM (gemma2)         雑談・意図判定・言い換えの吸収
実行層  コマンドディスパッチ  UART送信・カメラ起動
反応層  キーワード直結        「止まれ」→ LLMを経由せず即UART
```

「止まれ」に数秒かかるロボットは危険なので、**停止系だけは LLM を通さず直結**しています。

### 2. カスケード推論 — 安い段で受け、必要な時だけ重い段へ

```
常時待受(ESP32, 数十mW) → Whisper → キーワード一致で即応
                                  └─ 曖昧なら LLMルーター判定
                                       ├─ YOLO (軽い・位置情報つき)
                                       └─ VLM moondream (重い・情景説明)
```

「これ何？」は YOLO で即答、「詳しく説明して」で初めて VLM に上げます。

### 3. 8GB という制約への対処

Jetson Orin Nano 8GB では gemma2 と moondream の同時常駐ができません。

- `OLLAMA_MAX_LOADED_MODELS=1` による **モデルの時分割**（systemd の drop-in で設定）
- **GUI を落として運用**（`systemctl isolate multi-user.target`）→ 約2GB 確保
- **YOLO は CPU / LLM は GPU** で棲み分け
  （Jetson で YOLO を GPU 実行すると統合メモリの競合と PyTorch の NVML バグを踏むため）

### 4. 設定値と入出力の一元化

`UART_BAUD` のような値が4ファイルに散っており、食い違っても「なぜか通信できない」としか表に出ない状態でした。`common/config.py` に集約し、Pico への送信は `actuator/robot_io.py` に一本化しています。

実際にこれで一件バグが見つかりました。「Pico の返事を読んで受信確証を取る」処理が `main.py` にしか入っておらず、ジェスチャー経由の送信は届いたか分からないままでした。**重複の害は「2箇所にある」ことではなく「片方だけ直る」こと**だと考えています。

なお `firmware/pico/pico_uart_servo.py` のボーレートだけは別マシン・別言語のため集約できません。ここは `config.py` にコメントで明示しています。

### 5. ティーチング層 — 自信がない時は聞き返して記録する

音声認識の `avg_logprob` や YOLO のスコアが閾値を下回った場合、断定せずユーザーに確認し、訂正内容を記憶に残します。判定は各サブシステム側で bool に変換して渡す共通設計です。

---

## 動かし方

### 必要なもの

- Ollama（`ollama pull gemma2:2b` / `ollama pull moondream`）
- Python 3.10+

```bash
pip install -r requirements.txt
```

### 起動

```bash
# ウェイクワード待機で起動（XIAO ESP32-S3 が必要）
python3 main.py

# XIAO なしで試す（Enterキーで録音開始）
TRIGGER=enter python3 main.py
```

### 主な環境変数

| 変数 | 既定値 | 説明 |
|---|---|---|
| `TRIGGER` | `wake` | `wake` / `enter` |
| `TTS_ENGINE` | `espeak` | `openjtalk` 推奨 |
| `TEACH_MODE` | `uncertain` | `always` / `uncertain` / `off` |
| `ROUTER` | `1` | LLMによる視覚起動判定。`0` で無効 |
| `CAM` | `0` | カメラ番号 |

---

## ディレクトリ構成

```
osouji-robot/
├── main.py                     # ★エントリポイント（音声→判断→行動→記憶）
├── common/
│   └── config.py               # 全体で共有する設定値（UART・パス・モデル名）
├── actuator/
│   └── robot_io.py             # Picoへの送信口（送信＋受信確証＋ブリッジ書き出し）
├── perception/
│   └── gesture_control.py      # MediaPipe: グー/パー → とまれ/すすめ
├── firmware/
│   ├── wakenet_hiesp/          # XIAO ESP32-S3 ウェイクワード（Arduino）
│   ├── pico/                   # Pico 2 サーボ制御（MicroPython）
│   └── esp32/                  # 音声データ収録用（実験）
├── ros2_bridge/
│   └── turtle_gesture_bridge.py  # ジェスチャー → ROS2 /cmd_vel
├── tools/                      # 手で動かす確認用スクリプト（自動テストではない）
│   ├── yolo_test.py            # YOLO 単発推論（ヘッドレス）
│   ├── see_raw_data.py         # 手の生ランドマーク表示
│   ├── voice_chat.py           # 旧世代の簡易ループ（保存用）
│   └── jetson_send_uart.py     # UART 単体の疎通確認
├── models/                     # 自動ダウンロード（Git管理外）
├── docker/                     # ⚠️ 未検証（下記参照）
└── docs/archive/               # 開発初期の記録
```

---

## 既知の制約（正直に）

- **`docker/` は未検証です。** x86 前提で書いたまま、Jetson (arm64) 用に差し替えていません。動かすには `l4t-*` ベースイメージへの書き換えが必要です。
- **ジェスチャー認識のみ Windows 側で動作**しています（MediaPipe の都合）。Windows↔WSL 間はファイル経由の暫定実装で、Jetson に統合した時点で不要になる想定です。
- **サーボ電源が USB 給電だと瞬断することがあります。** 外部5V＋共通GNDが応急策で、本式として配電基板を自作予定です。
- **日本語カスタムウェイクワードは断念しました。** Edge Impulse で学習させたモデルが Studio 上では99%、実機では0点固定。原因はデータの過学習（ドメイン不一致）と判断し、既定モデル "Hi ESP" を採用しています。

---

## 今後

- [ ] ROS2 化（Gazebo Harmonic + TurtleBot3 で SLAM / Nav2 を検証中）
- [ ] Pico 2 にエンコーダ読み取りを追加し micro-ROS で ROS2 連携
- [ ] KiCad でサーボ電源の配電基板を自作
- [ ] ST7789 で顔表示
- [ ] 四輪台車への搭載と自律走行

---

## ライセンス

MIT
