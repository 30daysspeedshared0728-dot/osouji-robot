# CLAUDE.md — osouji-robot 作業コンテキスト（Jetson上のClaude Code用）

このファイルはプロジェクトの引継ぎ用。Claude Codeは起動時にこれを自動で読む。
本人（アツシ）の学び方・プロジェクトの状態・今日ハマった罠・次にやることが書いてある。

---

## 本人について（対応方針）
- アツシ。**ロボティクスエンジニアを目指して就活中**。組み込み歴3ヶ月・独学・関西弁。
- **AIに丸投げせず「なぜ動くか」を理解したい派。自分のコードを1行ずつ解説してもらう学び方が合う。** コードを書き換えるときは、何をなぜ変えたかを簡潔に説明する。
- 回答は**簡潔・率直**で。お世辞や空元気は不要。事実で返す。不安を煽らない。
- 開発はXで実況（build-in-public、就活ポートフォリオ戦略）。アカウント `@AtushiRobotics`。

## プロジェクト概要
- **四輪お掃除ロボット**。Jetson + ESP32-S3 + Raspberry Pi Pico 2。最終目標はROS2でSLAM自律走行。
- リポジトリ: https://github.com/30daysspeedshared0728-dot/osouji-robot （Public）
- 構成: `main.py`(★エントリポイント/メインループ), `perception/`(MediaPipe手認識), `voice/`(Whisper+Ollama), `actuator/`, `firmware/`, `ros2_bridge/`, `docker/`
- **動くもの**: `perception/gesture_control.py` … MediaPipe Tasks APIで手認識。**グー=STOP/とまれ、パー=GO/すすめ**。
  - 判定方式は `hand_openness`（手首→指先平均距離÷手のサイズ）。しきい値 `OPEN_STOP_MAX=1.40 / OPEN_GO_MIN=1.80`。デバウンス5フレーム。
  - 確定コマンドは `~/osouji_cmd.txt` に書き出す（Windows開発中の仮設ブリッジ）。将来Jetsonでは**このファイル書き出しをROS2 publishに差し替え**、ブリッジを消す予定。
  - **本人はこのコードを1行ずつ理解済み。**

---

## Jetson の状態（2026-07-17 時点）
- **機種: Yahboom製 Jetson Orin Nano (Super) 開発キット。**
- **★重要: これはmicroSD起動ではなく NVMe SSD起動タイプ。基板にmicroSDスロットは無い（or 使わない）。** システムはSSDに書き込み済みで出荷され、電源ONでデスクトップまで起動する。※本人がSDスロットを探して長時間ハマった。SDカードは使わない。
- **起動は成功済み**（Ubuntuデスクトップ表示OK）。これが今日の最大の達成。
- ネット接続: WiFi（M.2無線カード、ただし**アンテナが1本しか刺さっていない**→電波が不安定で切れやすい）。**有線LANでルーター直結を推奨**（安定＋ネット確保）。
- JetPack/L4Tのバージョンは未確認（`cat /etc/nv_tegra_release` で確認可）。

### ★今日ハマった罠（同じ轍を踏まないため）
1. **microSDスロットは無い**（SSD起動）。→ SDを探さない。
2. **ログイン/sudoパスワードは `yahboom`**（sudoは入力しても画面に表示されないが正常）。
3. **中国語IMEがデフォルト**（Yahboom製のため）。英語入力へは `Ctrl+Space` で切替。日本語入力(Mozc)は未導入。
4. **WiFiアンテナ(u.FL)が1本のみ接続**。1本でも通信可だが不安定。有線推奨。
5. **「ソフトウェアアップデーター」がaptロックを握る**→ apt installが失敗する原因になる。`sudo killall update-manager` で止める。全体アップデートはJetsonでは非推奨（NVIDIA系パッケージ破損リスク）。
6. **ターミナルの貼り付けは `Ctrl+Shift+V`**（Ctrl+Vは効かない）。右クリック→Pasteでも可。

---

## 次にやること（順番）
0. **前提: Jetsonをネット接続**（有線LANでルーター直結が確実）。`ping -c 3 8.8.8.8` で確認。
1. **日本語入力を入れる**: `sudo apt update && sudo apt install -y fcitx-mozc`（失敗時は `fcitx5-mozc`）→ `fcitx-configtool` でMozc追加 → **ログアウト/再ログイン** → `Ctrl+Space`。
2. **コードをclone**: `cd ~ && git clone https://github.com/30daysspeedshared0728-dot/osouji-robot.git`
3. **環境構築**: Jetsonは **Python 3.10**（Windowsの3.13ではない）。`python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`。aarch64でmediapipe/opencvのバージョンが合わずエラーが出たらバージョン調整。
4. **まずUSBウェブカメラでMediaPipe動作確認**（Windowsで使っていたUSBカメラを流用）。`python perception/gesture_control.py`。`open_camera()` は既にLinux分岐済みなので**USBカメラなら書き換え不要**。画面の `open:` の数字を見てしきい値を微調整。
5. **ラズパイ(CSI)カメラ対応は後回し・別ステップ**。理由: CSIカメラは `VideoCapture(0)` では開けず**GStreamerパイプライン**が必要。さらに **pip版opencvはGStreamer非対応**なので、**JetPack付属のシステムOpenCV(GStreamer入り)** を使う必要がある（mediapipeが引っ張るopencv-contribと競合しやすい定番の沼）。まずUSBカメラで動かしてから取り組む。

## インターフェース設計の方針（合意済み）
- 操縦=ジェスチャー / 一発命令=音声 / 確実系=リモコン、で分担。全部ジェスチャーに詰め込まない。
- 命令の種類は少数・区別しやすく（厳しく）、やり方は自然なブレを許す（緩く）。**迷ったら止まる**（安全側）。信頼度の低いフレームは捨てる。

<!-- 本人の経歴・就活・生活状況などの個人情報は、公開リポジトリには書かない。
     必要な文脈はローカルの引継ぎメモ(リポジトリ外)で管理する。 -->

