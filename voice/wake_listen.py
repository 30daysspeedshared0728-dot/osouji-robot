#!/usr/bin/env python3
"""
wake_listen.py  ―― レンガ順3「音声トリガ通し」の本体
======================================================
XIAO ESP32-S3(wakenet_hiesp)が「Hi ESP」を聞くと USB シリアルに "WAKE" を1行吐く。
このスクリプトは Jetson 側で常駐し:

  /dev/ttyACM0 を監視  --("WAKE"を受信)-->  「聞くモード」起動
      -> 無音になるまで自動で録音(VAD)         ← Enterを押さなくていい
      -> faster-whisper で日本語認識
      -> 移動コマンド(進め/止まれ/…)を抽出 -> UART(/dev/ttyTHS1)でPicoへ -> サーボ
      -> Gemma(Ollama)が短く返事
      -> また寝る(次の "WAKE" を待つ)

つまり voice_chat.py の「Enterで録音」を「ウェイクワードで録音」に置き換えたもの。
voice_chat.py はそのまま残す(手動テスト用)。こちらが本線。

前提(Jetson):
  - XIAO(wakenet_hiesp を焼いたもの)を USB で Jetson に挿す => /dev/ttyACM0
  - Pico(pico_uart_servo.py 待機)が UART /dev/ttyTHS1 に接続済み(声→サーボを見るなら)
  - faster-whisper / sounddevice / pyserial が入っている
  - 雑談返事も見るなら Ollama 起動 + gemma2:2b を pull 済み

使い方:
    python3 voice/wake_listen.py
    -> 「Hi ESP」と言う -> ピッと聞くモード -> 喋る -> 認識&実行
    Ctrl+C で終了

★調整ポイントは下の「== チューニング定数 ==」に集約した。
  マイクや部屋に合わせて、まず SILENCE_THRESH を画面の rms 値を見ながら詰めるのが早い。
"""
import os
import sys
import glob
import time
import queue
import re

import numpy as np
import sounddevice as sd
import requests
from faster_whisper import WhisperModel

try:
    import serial  # pyserial。無くても認識だけは動くようにする。
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None

# TTS(返事を声に出す)用。espeak-ngは外部コマンド、pyopenjtalkは任意。
import subprocess
import tempfile
import wave
try:
    import pyopenjtalk  # 日本語を正しく読むTTS。無ければespeakにフォールバック。
except Exception:
    pyopenjtalk = None


# ============================================================
# == チューニング定数(ここだけ触れば挙動を調整できる) ==
# ============================================================
SAMPLE_RATE   = 16000     # Whisper は 16kHz。XIAO/Pico とは無関係(こっちはマイク)。
BLOCK_SEC     = 0.05      # 録音を刻む単位(50ms)。RMS 判定の粒度。

# --- 無音自動停止(VAD)の調整 ---
# 音の大きさ(RMS, 0.0〜1.0)がこの値を超えたら「喋ってる」とみなす。
# 起動時に周囲の静音レベルを測って自動で少し上げるが、下限がこれ。
# 画面に出る rms 値を見て、静音時より確実に上・声で超える値にする。
SILENCE_THRESH   = 0.015
NOISE_FACTOR     = 3.0    # 静音フロア x この倍率 と SILENCE_THRESH の大きい方を閾値に。
START_TIMEOUT    = 4.0    # WAKE後この秒数だけ声を待つ。無音ならキャンセルして寝る。
SILENCE_HOLD     = 0.8    # 喋り始めた後、この秒数ぶん静かなら録音終了。
MAX_RECORD       = 8.0    # 保険。何秒喋っても必ずここで打ち切る。
MIN_SPEECH_SEC   = 0.3    # これ未満の音は「短すぎ」で捨てる。

# --- シリアルポート ---
WAKE_PORT_HINTS  = ["/dev/ttyACM0", "/dev/ttyACM1"]  # XIAO(ウェイクワード入力)候補
WAKE_BAUD        = 115200                              # wakenet_hiesp.ino の Serial.begin と一致
WAKE_TOKEN       = "WAKE"                              # XIAO が吐く合図の1行
TRIGGER          = os.environ.get("TRIGGER", "wake")  # "wake"=XIAOの「Hi ESP」/ "enter"=Enterで話す(XIAO無しでテスト)。実行時 TRIGGER=enter で切替

UART_PORT        = "/dev/ttyTHS1"   # Jetson40ピンUART(ピン8/10)→Pico。声→サーボの出力。
UART_BAUD        = 115200           # pico_uart_servo.py と必ず同じ。

# --- 認識/返事 ---
WHISPER_SIZE     = "small"          # Jetson が重ければ "base" に落とす。
OLLAMA_URL       = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL     = "gemma2:2b"
USE_GEMMA        = True             # JetsonにOllama導入済み＝雑談ON。Falseにするとコマンドのみ。

# --- TTS(返事をスピーカーで喋る) ---
TTS_ENGINE       = os.environ.get("TTS_ENGINE", "espeak")  # "espeak"(即・粗い)/"openjtalk"(要pyopenjtalk・日本語正しく読む)。実行時 TTS_ENGINE=openjtalk で切替
ESPEAK_VOICE     = "ja"             # espeak-ngの声。"ja+m3"などで声色変更可。

SYSTEM_PROMPT = (
    "あなたは四輪のお掃除ロボット『オソウジくん』です。"
    "親しみやすく、短く、日本語で答えます。"
    "移動指示(進め/止まれ/戻れ/右/左)を受けたら、元気に復唱して従います。"
    "返事は音声で読み上げられます。絵文字・記号・箇条書き・マークダウンは一切使わず、"
    "話し言葉で1〜2文の短さで答えてください。"
)

COMMAND_KEYWORDS = {
    "進め": "GO", "すすめ": "GO", "前": "GO",
    "止まれ": "STOP", "とまれ": "STOP", "ストップ": "STOP",
    "戻れ": "BACK", "もどれ": "BACK", "バック": "BACK",
    "右": "RIGHT", "みぎ": "RIGHT",
    "左": "LEFT", "ひだり": "LEFT",
}

BRIDGE_FILE = os.path.join(os.path.expanduser("~"), "osouji_cmd.txt")  # WSL2 ROS2用(害なし)


# ============================================================
# == UART: 確定コマンドを Pico へ送ってサーボを動かす ==
# ============================================================
_uart = None

def init_uart():
    """Picoへの送信用UARTを開く。失敗しても止めない(認識だけでも動くように)。"""
    global _uart
    if serial is None:
        print("[UART] pyserial 未導入のため送信オフ (pip install pyserial)")
        return
    try:
        _uart = serial.Serial(UART_PORT, UART_BAUD, timeout=0.1)
        print(f"[UART] {UART_PORT} を開いた -> Pico へ命令を送ります")
    except Exception as e:
        _uart = None
        print(f"[UART] {UART_PORT} を開けず: {e} (UART送信なしで続行)")


def on_command(command):
    """print + UART送信 + ブリッジ用ファイル書き出し(gesture/voice_chatと同じ)。"""
    print(f"[COMMAND] {command}")
    if _uart is not None:
        try:
            _uart.write((command + "\n").encode())
            # ★Picoの返事を読む=命令が届いた確証。
            #   返事あり → UART/サーボ側はOK。動かないなら電源/機構を疑う。
            #   返事なし → UART未達(配線/GNDゆるみ/TX-RX交差/Pico未起動)。
            reply = _uart.readline().decode(errors="replace").strip()
            if reply:
                print(f"[UART] <- pico: {reply}")
            else:
                print("[UART] <- (返事なし=命令が届いてないかも。配線/Pico起動を確認)")
        except Exception as e:
            print(f"[UART] 送信失敗: {e}")
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


# ============================================================
# == ウェイクワード入力(XIAO)ポートを開く ==
# ============================================================
def open_wake_serial():
    """XIAO(wakenet)の "WAKE" を読むシリアルを開く。見つかるまで待つ。"""
    if serial is None:
        print("[WAKE] pyserial 未導入では WAKE を受信できません (pip install pyserial)")
        sys.exit(1)

    while True:
        # 候補ポート + いま挿さっている ttyACM* を総当り
        candidates = list(WAKE_PORT_HINTS)
        candidates += sorted(glob.glob("/dev/ttyACM*"))
        seen = set()
        for port in candidates:
            if port in seen:
                continue
            seen.add(port)
            try:
                s = serial.Serial(port, WAKE_BAUD, timeout=0.2)
                print(f"[WAKE] {port} を開いた -> 「Hi ESP」を待ちます")
                return s
            except Exception:
                continue
        print("[WAKE] XIAO(ttyACM*)が見つからない。USBで挿さってる? 3秒後に再探索…")
        time.sleep(3)


def wait_for_wake(wake_ser):
    """WAKE の1行が来るまでブロックして待つ。来たら True。"""
    wake_ser.reset_input_buffer()  # 溜まった古い WAKE を捨ててから待つ
    while True:
        try:
            raw = wake_ser.readline()
        except Exception as e:
            print(f"[WAKE] 読み取りエラー: {e} 再接続を試みます")
            return False
        if not raw:
            continue  # timeout。待ち続ける。
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line:
            continue
        # XIAOは "WAKE" 以外にデバッグ行も出す。トークンを含む行だけ拾う。
        if WAKE_TOKEN in line:
            return True


# ============================================================
# == 無音になるまで自動録音(VAD) ==
# ============================================================
def record_until_silence():
    """WAKE後に呼ぶ。声を待って録り、無音が続いたら止めて波形を返す。
    戻り値: (audio ndarray, 理由文字列)。声が来なければ空配列。"""
    block = int(SAMPLE_RATE * BLOCK_SEC)
    q = queue.Queue()

    def callback(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        q.put(indata.copy())

    # 1) 起動直後に静音フロアをざっと測って閾値を決める
    floor_samples = []
    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=block, callback=callback):
        t0 = time.time()
        while time.time() - t0 < 0.4:
            try:
                floor_samples.append(q.get(timeout=0.1))
            except queue.Empty:
                pass
        noise_floor = float(np.sqrt(np.mean(np.square(
            np.concatenate(floor_samples))))) if floor_samples else 0.0
        thresh = max(SILENCE_THRESH, noise_floor * NOISE_FACTOR)
        print(f"[LISTEN] 🎤 どうぞ… (無音フロア={noise_floor:.4f} 閾値={thresh:.4f})")

        # 2) 声の立ち上がりを待つ(START_TIMEOUT まで)
        frames = []
        started = False
        t_start_wait = time.time()
        while not started:
            if time.time() - t_start_wait > START_TIMEOUT:
                return np.zeros(0, dtype=np.float32), "声が来なかった"
            try:
                b = q.get(timeout=0.1)
            except queue.Empty:
                continue
            rms = float(np.sqrt(np.mean(np.square(b))))
            if rms >= thresh:
                started = True
                frames.append(b)

        # 3) 喋り出したら、無音が SILENCE_HOLD 続くまで録り続ける
        t_rec_start = time.time()
        last_voice = time.time()
        while True:
            try:
                b = q.get(timeout=0.1)
            except queue.Empty:
                b = None
            if b is not None:
                frames.append(b)
                rms = float(np.sqrt(np.mean(np.square(b))))
                if rms >= thresh:
                    last_voice = time.time()
            now = time.time()
            if now - last_voice >= SILENCE_HOLD:
                reason = "無音で自動停止"
                break
            if now - t_rec_start >= MAX_RECORD:
                reason = "最大長で打ち切り"
                break

    audio = np.concatenate(frames, axis=0).flatten() if frames else np.zeros(0, dtype=np.float32)
    return audio, reason


# ============================================================
# == Gemma(Ollama)返事 ==
# ============================================================
def ask_gemma(user_text):
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


# ============================================================
# == TTS: 返事をスピーカーで喋る ==
# ============================================================
# 絵文字・記号・マークダウンを読み上げると意味不明になるので、喋る前に掃除する。
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U0000FE00-\U0000FE0F\U00002190-\U000021FF]",
    flags=re.UNICODE)

def clean_for_tts(text):
    """絵文字・記号・括弧・マークダウンを除去。日本語の長音符ーや句読点(、。！？)は残す。"""
    t = text or ""
    t = _EMOJI_RE.sub("", t)                                          # 絵文字を消す
    t = re.sub(r"[\*\_#`>~|\^=\[\]{}<>()（）「」『』【】・•◆■□●▶►\-]", " ", t)  # 記号・括弧を空白へ
    t = t.replace("\n", "、")                                         # 改行は読点(ポーズ)に
    t = re.sub(r"[ \t]+", " ", t)                                     # 連続空白を1つに
    t = re.sub(r"、{2,}", "、", t)                                    # 読点の連続を1つに
    return t.strip("、 ")

def speak(text):
    """返事を声に出す。TTS_ENGINEで方式を切替。失敗しても止めない(会話は続ける)。
      espeak   : 外部コマンド espeak-ng。即動くが日本語は粗い(漢字が苦手)。
      openjtalk: pyopenjtalk。日本語を正しく読む(やや機械声)。wav化→aplayで再生。"""
    text = clean_for_tts(text)   # ★絵文字・記号を先に掃除(意味不明な読み上げ防止)
    if not text:
        return
    try:
        if TTS_ENGINE == "openjtalk" and pyopenjtalk is not None:
            wav, sr = pyopenjtalk.tts(text)                 # float波形, サンプルレート
            pcm = np.clip(wav, -32768, 32767).astype(np.int16)
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                path = f.name
            with wave.open(path, "wb") as w:
                w.setnchannels(1); w.setsampwidth(2); w.setframerate(int(sr))
                w.writeframes(pcm.tobytes())
            subprocess.run(["aplay", "-q", path], check=False)   # espeakと同じALSA経路で鳴らす
            os.remove(path)
        else:
            # espeak-ng(日本語ボイス。粗いが即動く)。1のテストで音が出た経路。
            subprocess.run(["espeak-ng", "-v", ESPEAK_VOICE, text], check=False)
    except Exception as e:
        print(f"[TTS] 喋れず: {e} (会話は続行)")


# ============================================================
# == メインループ ==
# ============================================================
def main():
    print("Whisper モデルを読み込み中…(初回はダウンロードあり)")
    model = WhisperModel(WHISPER_SIZE, device="cpu", compute_type="int8")
    print("準備完了。\n")

    init_uart()                      # Pico への送信路(声→サーボ)
    wake_ser = open_wake_serial() if TRIGGER == "wake" else None  # enterモードならXIAO不要

    if TRIGGER == "wake":
        print("=== 待機中。「Hi ESP」と言うと聞くモードになります。Ctrl+C で終了。 ===\n")
    else:
        print("=== Enterで話すテストモード(XIAO不要)。Ctrl+C で終了。 ===\n")
    try:
        while True:
            # --- 1. ウェイクワードを待つ ---
            if TRIGGER == "wake":
                ok = wait_for_wake(wake_ser)
                if not ok:
                    # 読み取り不調 -> 開き直す
                    try:
                        wake_ser.close()
                    except Exception:
                        pass
                    wake_ser = open_wake_serial()
                    continue
                print("🔔 WAKE 受信! 聞くモード起動。")
            else:
                input("▶ Enterで話す…")   # XIAO無しのテスト。Enterがウェイクの代わり。
                print("🎙️ どうぞ。")

            # --- 2. 無音まで自動録音 ---
            audio, reason = record_until_silence()
            if audio.size < SAMPLE_RATE * MIN_SPEECH_SEC:
                print(f"({reason}。何も認識せず待機に戻ります)\n")
                continue

            # --- 3. 認識 ---
            segments, _ = model.transcribe(audio, language="ja", beam_size=1,
                                           vad_filter=True)
            text = "".join(seg.text for seg in segments).strip()
            if not text:
                print("(聞き取れませんでした)\n")
                continue
            print(f"🗣  あなた: {text}   [{reason}]")

            # --- 4. コマンド抽出 -> UART -> サーボ ---
            cmd = extract_command(text)
            if cmd:
                on_command(cmd)

            # --- 5. Gemma 返事 -> スピーカーで喋る ---
            if USE_GEMMA:
                reply = ask_gemma(text)
                print(f"🤖 オソウジくん: {reply}")
                speak(reply)          # ★ここで声に出す(会話ループ完成)

            print("--- 待機に戻ります(「Hi ESP」でまた起こして) ---\n")

    except KeyboardInterrupt:
        print("\n終了します。おつかれさま!")
    finally:
        if _uart is not None:
            _uart.close()
        try:
            wake_ser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
