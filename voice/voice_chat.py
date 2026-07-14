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


def on_command(command):
    """print + ブリッジ用ファイルに書き出す(gestureと同じファイル)。"""
    print(f"[COMMAND] {command}")
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

    try:
        while True:
            audio = record_until_enter()
            if audio.size < SAMPLE_RATE * 0.3:
                print("(短すぎ。もう一度どうぞ)\n")
                continue

            segments, _ = model.transcribe(audio, language="ja", beam_size=1)
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


if __name__ == "__main__":
    main()
