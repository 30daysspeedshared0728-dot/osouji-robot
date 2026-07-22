#!/usr/bin/env python3
# servo_jetson.py
# JetsonのGPIOから直接SG90サーボを首振りさせる最小テスト。
# 配線(Jetson 40ピン → サーボ3本):
#   サーボ 茶/黒(GND)  → Jetson GND   (物理ピン6)
#   サーボ 赤(+電源)   → Jetson 5V    (物理ピン2)
#   サーボ 橙/黄(信号) → Jetson GPIO  (物理ピン33)  ← このコードの SERVO_PIN
# 実行: python3 servo_jetson.py   (Ctrl+C で止まる)

import Jetson.GPIO as GPIO   # JetsonのピンをPythonから叩くライブラリ
import time                  # 待ち時間(sleep)に使う

SERVO_PIN = 33               # 信号線を挿した「物理ピン番号」。BOARDモードの番号。

GPIO.setmode(GPIO.BOARD)     # ピンの数え方を「基板の物理番号」に設定(GPIO.BCMではない)
GPIO.setup(SERVO_PIN, GPIO.OUT)   # そのピンを「出力」にする(こっちから信号を出す)

pwm = GPIO.PWM(SERVO_PIN, 50)     # 50Hz=20ms周期。サーボが理解できる決まりの周波数。
pwm.start(0)                      # まずデューティ0%(信号ほぼ無し)で開始


def set_angle(angle):
    # サーボの角度(0〜180度)を「デューティ比(%)」に変換して送る。
    # 0度  → 2.5% (0.5msのパルス)
    # 180度 → 12.5%(2.5msのパルス)
    duty = 2.5 + (angle / 180.0) * 10.0
    pwm.ChangeDutyCycle(duty)     # その角度へ動け、と指示
    time.sleep(0.5)               # 動き切るのを待つ
    pwm.ChangeDutyCycle(0)        # 信号を一旦切る(ジッタ=プルプル震えを抑える)


try:
    while True:                   # Ctrl+Cするまで、0→90→180を繰り返す
        set_angle(0)
        time.sleep(1)
        set_angle(90)
        time.sleep(1)
        set_angle(180)
        time.sleep(1)
except KeyboardInterrupt:         # Ctrl+Cが押されたら…
    pass
finally:                         # 成功でも中断でも、最後に必ず片付ける
    pwm.stop()                    # PWM信号を止める
    GPIO.cleanup()                # ピンを初期状態に戻す(次回のため)
