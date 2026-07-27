#!/usr/bin/env python3
# jetson_send_uart.py  (Jetson側 / Python3 + pyserial)
# JetsonのUART(40ピン ピン8/10)から、Picoへコマンドを送るテスト。
# 実行: python3 jetson_send_uart.py
# 事前: pip install pyserial  (無ければ)
#
# ★デバイス名(PORT)は環境で違う。まず  ls -l /dev/ttyTHS*  で確認し、
#   出てきた名前に合わせてPORTを直すこと(Orin Nanoは /dev/ttyTHS1 が多い)。

import serial
import time

PORT = "/dev/ttyTHS1"          # ← 要確認。ls -l /dev/ttyTHS* の結果に合わせる
BAUD = 115200                  # Pico側と同じ値にする

ser = serial.Serial(PORT, BAUD, timeout=1)
print(f"open {PORT} @ {BAUD}")

# 3秒おきに GO→STOP→CENTER を送り、Picoからの返事を読む
for cmd in ["GO", "STOP", "CENTER", "GO", "CENTER"]:
    ser.write((cmd + "\n").encode())   # 文字列＋改行 を送信
    print("sent:", cmd)

    # ★Picoからの返事を待って読む(timeout=1秒までブロック)
    reply = ser.readline().decode(errors="replace").strip()
    if reply:
        print("   <- pico:", reply)    # 返事あり=Picoが受け取った確証
    else:
        print("   <- (返事なし。配線/TX-RX交差/Pico未起動を疑う)")

    time.sleep(2.5)                     # サーボが動くのを見る間

ser.close()
print("done")
