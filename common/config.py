# -*- coding: utf-8 -*-
"""全サブシステムで共有する設定値。ここ1箇所だけを直せばよい状態にする。

★なぜこのファイルがあるか
  同じ UART_BAUD が main.py / gesture_control.py / voice_chat.py / jetson_send_uart.py の
  4箇所に散らばっていた。値が食い違っても「なぜか通信できない」としか表に出ず、
  エラーメッセージの出ないバグになる。定数を1箇所に集約して構造で防ぐ。

⚠️ここで消せない重複が1つだけ残る: firmware/pico/pico_uart_servo.py の BAUD。
  あちらは別のマシン(RP2350)の別言語(MicroPython)なので import できない。
  UART_BAUD を変えるときは、必ずあちらも一緒に変えること。
"""
import os

# --- UART (Jetson 40ピン → Pico) ---
UART_PORT = "/dev/ttyTHS1"   # Jetson 40ピンUART(ピン8/10)。環境で違えば変更。
UART_BAUD = 115200           # ⚠️firmware/pico/pico_uart_servo.py と必ず同じ値

# --- ブリッジ(WSL2側のROS2ノードが読むファイル。Jetsonへ統合したら不要になる) ---
BRIDGE_FILE = os.path.join(os.path.expanduser("~"), "osouji_cmd.txt")

# --- 記憶(エピソード記憶。1行1件JSON) ---
LOG_FILE = os.path.join(os.path.expanduser("~"), "osouji_log.jsonl")

# --- Ollama ---
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = "gemma2:2b"

# --- モデル置き場(リポジトリ直下。.gitignore の models/ で除外済み) ---
MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")
