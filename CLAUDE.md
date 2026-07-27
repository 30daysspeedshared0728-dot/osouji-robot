# CLAUDE.md — osouji-robot 作業コンテキスト

> ## ⚠️ このファイルの書き方のルール
> **「変わらないこと」だけを書く。**
> 進捗・状態・次にやること・今日ハマった罠・しきい値などの具体的な数値は**書かない**。
>
> - **状態と進捗** → リポジトリ**外**の `引継ぎメモ.md`（非公開）が唯一の正
> - **具体的な数値** → **コード本体**が唯一の正（ここに書き写すと必ず食い違う）
>
> 過去に `OPEN_GO_MIN` の値をここに書き写し、コード側だけ更新されて**このファイルが嘘をつく**状態になった。
> 重複の害は「2箇所にある」ことではなく「**片方だけ直る**」こと。

---

## 本人について（対応方針）

- アツシ。**ロボティクスエンジニアを目指して就活中**。組み込み歴3ヶ月・独学・関西弁。
- ★**AIに丸投げせず「なぜ動くか」を理解したい派。** コードを書き換えるときは、**何をなぜ変えたか**を簡潔に説明する。
- ★**学び方＝既に自分で作った物に括り付けると通る。定義から積むと通らない。** 抽象語で説明せず、**実物を表示するコマンド**を1つ添える。
- 回答は**簡潔・率直**に。お世辞や空元気は不要。事実で返す。不安を煽らない。
- 開発はXで実況（build-in-public・就活ポートフォリオ戦略）。アカウント `@AtushiRobotics`。

## プロジェクト概要

**四輪お掃除ロボット。** Jetson Orin Nano + XIAO ESP32-S3 + Raspberry Pi Pico 2。
音声・視覚・ジェスチャーで指示を出し、**すべてローカル・完全オフライン**で動く。最終目標は ROS2 で SLAM 自律走行。

リポジトリ: https://github.com/30daysspeedshared0728-dot/osouji-robot （**Public**）

```
main.py                      ★エントリポイント（音声→判断→行動→記憶）
common/config.py             ★設定値の集約（UART・パス・モデル名）
actuator/robot_io.py         ★Picoへの送信口（送信＋受信確証＋ブリッジ書き出し）
perception/gesture_control.py  MediaPipe 手認識
firmware/                    wakenet_hiesp(XIAO) / pico / esp32
ros2_bridge/                 ジェスチャー → ROS2 /cmd_vel
tools/                       手で動かす確認用スクリプト（自動テストではない）
docker/                      ⚠️未検証（x86前提のまま。Jetsonはarm64）
models/                      自動ダウンロード（Git管理外）
```

## コードを触るときの約束事

1. ★**設定値は `common/config.py` にだけ書く。** 各ファイルに定数を再定義しない。
   - 過去に `UART_BAUD` が4ファイルに散り、食い違っても「なぜか通信できない」としか表に出ない状態だった。
2. ★**Pico への送信は `actuator/robot_io.py` を通す。** 各ファイルで `serial.Serial()` を開き直さない。
   - `send_command()` は送信後に**Picoの返事を読む＝受信確証**。返事なし＝UART未達（配線/GND/TX-RX交差/Pico未起動）。
3. ⚠️**`firmware/pico/pico_uart_servo.py` のボーレートだけは集約できない**（別マシン・別言語）。
   `config.UART_BAUD` を変えるときは**必ず一緒に変える**。
4. **`tools/` は本番コードではない。** 本番（`main.py` / `perception/`）から `tools/` を import しない。
5. **公開リポジトリなので個人情報を書かない**（経歴・就活・生活状況・ローカルの絶対パス）。

## 環境の前提

- **Jetson: Python 3.10 のシステム Python をそのまま使う。venv を作らない。**
  - 理由＝CUDA 系のホイールがシステム Python にリンクされており、venv に隔離すると読めない。
  - よって `pip3 install ... --break-system-packages` を使う（apt の縄張りに入る宣言）。
  - ⚠️同じ理由で **ROS2 の `rclpy` も venv では動かない**（コンパイル済み `.so` がビルド時の Python に縛られる）。
- **Windows 側**: `.venv`（MediaPipe のため）。`gesture_control.py` は `open_camera()` が OS 分岐済みで両方動く。
- **UART の権限**: `/dev/ttyTHS1` は再起動で権限が戻る（`/dev` は RAM 上）。
  応急＝`sudo chmod 666`、本式＝`sudo usermod -aG dialout jetson`（保存先が `/etc/group`＝ディスク）。

## 設計の方針（合意済み）

- **3層アーキテクチャ**：熟慮(LLM) / 実行 / ★**反応(LLM非経由)**。停止系は LLM を通さず即 UART。
- **カスケード推論**：安い段で受け、必要なときだけ重い段へ（ウェイクワード→Whisper→キーワード or LLMルーター→YOLO or VLM）。
- **プランナー × プリミティブ**：LLM は計画だけ。関節角や具体的な数値を LLM に出させない（接地がないため）。
- **インターフェースの分担**：操縦=ジェスチャー / 一発命令=音声 / 確実系=リモコン。全部をジェスチャーに詰め込まない。
- **命令は少数・区別しやすく（厳しく）、やり方は自然なブレを許す（緩く）。迷ったら止まる**（安全側）。
- **リソース制約**：8GB なのでモデルは時分割（`OLLAMA_MAX_LOADED_MODELS=1`）。YOLO は CPU / LLM は GPU で棲み分け。
