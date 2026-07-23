#!/usr/bin/env python3
"""
voice_chat.py
Enter を押して話す(プッシュトゥトーク) ->
  faster-whisper で音声認識(日本語) ->
  Ollama(Gemma)にお掃除ロボとして応答させる ->
  端末に表示。

さらに「進め/止まれ/戻れ/右/左」を簡易コマンド抽出して on_command() に流す。
将来ここを ROS2 パブリッシャに差し替えて実際に走らせる。

前提:
  - Ollama が起動していて gemma2:2b が pull 済み
      ollama pull gemma2:2b
  - マイクが使える(sounddevice)

使い方:
    python voice/voice_chat.py
    Enter で録音開始 -> もう一度 Enter で停止 -> 認識&応答
    Ctrl+C で終了
"""
import os
import sys
import threading
import queue

import numpy as np
import sounddevice as sd
import requests
from faster_whisper import WhisperModel

SAMPLE_RATE = 16000
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = "gemma2:2b"

SYSTEM_PROMPT = (
    "あなたは四輪のお掃除ロボット『オソウジくん』です。"
    "親しみやすく、短く、日本語で答えます。"
    "移動指示(進め/止まれ/戻れ/右/左)を受けたら、元気に復唱して従います。"
)

# 音声から拾う簡易コマンド(キーワード -> 内部コマンド)
COMMAND_KEYWORDS = {
    "進め": "GO", "すすめ": "GO", "前": "GO",
    "止まれ": "STOP", "とまれ": "STOP", "ストップ": "STOP",
    "戻れ": "BACK", "もどれ": "BACK", "バック": "BACK",
    "右": "RIGHT", "みぎ": "RIGHT",
    "左": "LEFT", "ひだり": "LEFT",
}


# gesture_control.py と同じ橋渡し用ファイル。WSL2側のROS2ノードが読む。
BRIDGE_FILE = os.path.join(os.path.expanduser("~"), "osouji_cmd.txt")


# --- UART: 確定コマンドをPicoへ送り、サーボを動かす ---
# gesture_control.py と全く同じ仕組み。声で出したコマンドをPicoへ送る本線。
try:
    import serial  # pyserial (無くても音声認識だけは動くようにする)
except ImportError:
    serial = None

UART_PORT = "/dev/ttyTHS1"   # Jetson 40ピンのUART(ピン8/10)。環境で違えば変更。
UART_BAUD = 115200           # Pico側(pico_uart_servo.py)と必ず同じ値。
_uart = None                 # 実際の接続。main() の init_uart() で開く。


def init_uart():
    """Picoへの送信用UARTを開く。失敗しても止めない(音声認識だけでも動くように)。"""
    global _uart
    if serial is None:
        print("[UART] pyserial 未導入のため送信オフ (pip install pyserial)")
        return
    try:
        _uart = serial.Serial(UART_PORT, UART_BAUD, timeout=0.1)
        print(f"[UART] {UART_PORT} を開いた -> Picoへ命令を送ります")
    except Exception as e:
        _uart = None
        print(f"[UART] {UART_PORT} を開けず: {e} (UART送信なしで続行)")


def on_command(command):
    """print + UART送信 + ブリッジ用ファイル書き出し(gestureと同じ)。
    声で出したコマンドが、UART経由でPicoのサーボを動かす本線。"""
    print(f"[COMMAND] {command}")
    # ★Picoへ UART で送る(繋がっていれば)。ここが声→サーボの本線。
    if _uart is not None:
        try:
            _uart.write((command + "\n").encode())
        except Exception as e:
            print(f"[UART] 送信失敗: {e}")
    # 従来のブリッジ用ファイルにも残す(WSL2 ROS2用。害はない)
    try:
        with open(BRIDGE_FILE, "w", encoding="utf-8") as f:
            f.write(command)
    except OSError:
        pass


def extract_command(text):
    for kw, cmd in COMMAND_KEYWORDS.items():
        if kw in text:
            return cmd
    return None


def record_until_enter():
    """Enter で録音開始、もう一度 Enter で停止して波形を返す。"""
    print("▶ Enter で録音開始…", end="", flush=True)
    input()
    print("● 録音中… もう一度 Enter で停止", flush=True)

    q = queue.Queue()
    stop = threading.Event()

    def callback(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    def wait_enter():
        input()
        stop.set()

    threading.Thread(target=wait_enter, daemon=True).start()

    frames = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                        dtype="float32", callback=callback):
        while not stop.is_set():
            try:
                frames.append(q.get(timeout=0.1))
            except queue.Empty:
                pass

    if not frames:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(frames, axis=0).flatten()


def ask_gemma(user_text):
    """Ollama(Gemma)に投げて応答テキストを返す。"""
    prompt = f"{SYSTEM_PROMPT}\n\nユーザー: {user_text}\nオソウジくん:"
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.exceptions.RequestException as e:
        return f"(Ollama に繋がりませんでした: {e})"


def main():
    print("Whisper モデルを読み込み中…(初回はダウンロードあり)")
    # CPU なら compute_type="int8"、GPU があれば device="cuda"
    model = WhisperModel("small", device="cpu", compute_type="int8")
    print("準備完了。Ctrl+C で終了。\n")

    init_uart()      # Picoへの送信路を開く(失敗しても止めずに続行)

    try:
        while True:
            audio = record_until_enter()
            if audio.size < SAMPLE_RATE * 0.3:
                print("(短すぎ。もう一度どうぞ)\n")
                continue

            # vad_filter=True: 無音区間を自動でカット。
            # Whisperが無音/短い音に対して「ご視聴ありがとうございました」等の
            # 幻聴(ハルシネーション)を吐くのを大幅に抑える。
            segments, _ = model.transcribe(audio, language="ja", beam_size=1,
                                           vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
            if not text:
                print("(聞き取れませんでした)\n")
                continue

            print(f"🗣  あなた: {text}")

            cmd = extract_command(text)
            if cmd:
                on_command(cmd)

            reply = ask_gemma(text)
            print(f"🤖 オソウジくん: {reply}\n")

    except KeyboardInterrupt:
        print("\n終了します。おつかれさま!")
    finally:
        if _uart is not None:
            _uart.close()


if __name__ == "__main__":
    main()
