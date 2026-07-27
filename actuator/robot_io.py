# -*- coding: utf-8 -*-
"""Picoへコマンドを送る共通の口。

★なぜ一本化したか
  main.py と gesture_control.py が同じ init_uart() / on_command() をそれぞれ持っていて、
  main.py にだけ「Picoの返事を読む(受信確証)」改良が入り、gesture 側は送りっぱなしのまま
  取り残されていた。
  → 重複の本当の害は「2箇所にある」ことではなく「片方だけ直る」こと。
  ここに一本化して、どちらから使っても受信確証が取れるようにする。
"""
from common import config

try:
    import serial  # pyserial。無くても認識やカメラだけは動くようにする。
except ImportError:
    serial = None

_uart = None


def init_uart():
    """Picoへの送信用UARTを開く。失敗しても止めない。"""
    global _uart
    if serial is None:
        print("[UART] pyserial 未導入のため送信オフ (pip install pyserial)")
        return
    try:
        _uart = serial.Serial(config.UART_PORT, config.UART_BAUD, timeout=0.1)
        print(f"[UART] {config.UART_PORT} を開いた -> Pico へ命令を送ります")
    except Exception as e:
        _uart = None
        print(f"[UART] {config.UART_PORT} を開けず: {e} (UART送信なしで続行)")


def send_command(command):
    """UARTへ送信 → Picoの返事を読む → ブリッジ用ファイルへ書き出す。"""
    if _uart is not None:
        try:
            _uart.write((command + "\n").encode())
            # ★Picoの返事を読む = 命令が届いた確証。
            #   返事あり → UART/サーボ側はOK。動かないなら電源/機構を疑う。
            #   返事なし → UART未達(配線/GNDゆるみ/TX-RX交差/Pico未起動)。
            reply = _uart.readline().decode(errors="replace").strip()
            if reply:
                print(f"[UART] <- pico: {reply}")
            else:
                print("[UART] <- (返事なし=命令が届いてないかも。配線/Pico起動を確認)")
        except Exception as e:
            print(f"[UART] 送信失敗: {e}")
    # 従来のブリッジ用ファイルにも残す(WSL2 ROS2用。害はない)
    try:
        with open(config.BRIDGE_FILE, "w", encoding="utf-8") as f:
            f.write(command)
    except OSError:
        pass


def close_uart():
    """終了時の後始末。開いていなければ何もしない。"""
    global _uart
    if _uart is not None:
        try:
            _uart.close()
        except Exception as e:
            print(f"[UART] クローズ失敗: {e}")
        _uart = None
