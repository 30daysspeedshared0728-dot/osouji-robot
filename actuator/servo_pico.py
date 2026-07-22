# servo_pico.py  (MicroPython / Raspberry Pi Pico 2 用)
# SG90サーボを 0→90→180度 で首振りさせる最小テスト。
# VS Code の MicroPico で「Run current file on Pico」で実行する。
# 止めるときは MicroPico の Stop ボタン、または vREPL で Ctrl+C。
#
# 配線(Pico → サーボ3本):
#   サーボ 橙/黄(信号) → GP15   (物理ピン20)
#   サーボ 赤(電源)    → VBUS   (物理ピン40 / USBからの5V)
#   サーボ 茶/黒(GND)  → GND    (物理ピン38 など GNDならどれでも可)

from machine import Pin, PWM   # Picoのピン制御ライブラリ
import time                    # 待ち時間に使う

servo = PWM(Pin(15))           # GP15をPWM出力に。ここにサーボ信号線を挿す。
servo.freq(50)                 # サーボの決まり=50Hz(20ms周期)


def set_angle(deg):
    # 角度(0〜180度)を「パルスの長さ(ナノ秒)」に変換して送る。
    #   0度  → 0.5ms(500000ns)
    #   180度 → 2.5ms(2500000ns)
    # duty_ns はパルス幅を直接ナノ秒で指定できる。サーボ制御に一番素直。
    min_ns = 500000
    max_ns = 2500000
    ns = min_ns + (max_ns - min_ns) * deg // 180
    servo.duty_ns(ns)          # その角度へ動け、と指示


while True:                    # Stop するまで 0→90→180 を繰り返す
    set_angle(0)
    time.sleep(1)              # 1秒待つ(動き切るまで)
    set_angle(90)
    time.sleep(1)
    set_angle(180)
    time.sleep(1)
