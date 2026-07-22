# pico_uart_servo.py  (MicroPython / Raspberry Pi Pico 2)
# JetsonからUARTで届いたコマンドで、SG90サーボを動かす。
# VS Code の MicroPico で「Run current file on Pico」で実行。止めるのは Ctrl+C。
#
# 配線(サーボ):  橙/黄→GP15(信号) / 赤→VBUS(5V) / 茶黒→GND
# 配線(UART):   Jetsonピン8(TX)→Pico GP1(RX,ピン2)
#               Jetsonピン10(RX)→Pico GP0(TX,ピン1)
#               Jetsonピン6(GND)→Pico GND(ピン3)
#
# コマンド(改行区切り): GO=180度 / STOP=0度 / CENTER=90度

from machine import Pin, PWM, UART
import time

# --- サーボ設定(前と同じ) ---
servo = PWM(Pin(15))
servo.freq(50)


def set_angle(deg):
    min_ns = 500000            # 0度=0.5ms
    max_ns = 2500000           # 180度=2.5ms
    ns = min_ns + (max_ns - min_ns) * deg // 180
    servo.duty_ns(ns)


# --- UART設定 ---
# UART0 は TX=GP0 / RX=GP1。Jetsonの送信(TX)をPicoのRX(GP1)で受ける。
# baudrate(通信速度)はJetson側と必ず同じ値にする(115200)。
uart = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1))

set_angle(90)                  # 起動時はまず中央(90度)へ
print("UART待受け開始。GO / STOP / CENTER を待ってます")

buf = b""                      # 受信データを溜めるバッファ
while True:
    if uart.any():             # 届いたバイトがあれば
        buf += uart.read()     # 読んで溜める
        while b"\n" in buf:    # 改行が来たら=1コマンド分たまった
            line, buf = buf.split(b"\n", 1)
            cmd = line.strip().decode().upper()   # 文字に直して大文字化
            if cmd == "GO":
                set_angle(180)
                print("recv GO -> 180")
            elif cmd == "STOP":
                set_angle(0)
                print("recv STOP -> 0")
            elif cmd == "CENTER":
                set_angle(90)
                print("recv CENTER -> 90")
            elif cmd:
                print("unknown cmd:", cmd)
    time.sleep(0.01)           # CPUを回しすぎない小休止
