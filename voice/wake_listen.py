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
import json
from datetime import datetime

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
USE_LLM_ROUTER   = os.environ.get("ROUTER", "1") == "1"  # Gemmaに「今見る必要ある?」を判定させる。ROUTER=0でキーワードのみ

# --- TTS(返事をスピーカーで喋る) ---
TTS_ENGINE       = os.environ.get("TTS_ENGINE", "espeak")  # "espeak"(即・粗い)/"openjtalk"(要pyopenjtalk・日本語正しく読む)。実行時 TTS_ENGINE=openjtalk で切替
ESPEAK_VOICE     = "ja"             # espeak-ngの声。"ja+m3"などで声色変更可。

SYSTEM_PROMPT = (
    "あなたは四輪のお掃除ロボット『オソウジくん』です。"
    "親しみやすく、短く、日本語で答えます。"
    "移動指示(進め/止まれ/戻れ/右/左)を受けたら、元気に復唱して従います。"
    "返事は音声で読み上げられます。絵文字・記号・箇条書き・マークダウンは一切使わず、"
    "話し言葉で1〜2文の短さで答えてください。"
    "英単語やローマ字は使わず、すべて日本語のかな漢字で答えてください。"
)

COMMAND_KEYWORDS = {
    "進め": "GO", "すすめ": "GO", "前": "GO",
    "止まれ": "STOP", "とまれ": "STOP", "ストップ": "STOP",
    "戻れ": "BACK", "もどれ": "BACK", "バック": "BACK",
    "右": "RIGHT", "みぎ": "RIGHT",
    "左": "LEFT", "ひだり": "LEFT",
}

BRIDGE_FILE = os.path.join(os.path.expanduser("~"), "osouji_cmd.txt")  # WSL2 ROS2用(害なし)

# --- 記憶(ログ) / human-in-the-loop ---
LOG_FILE = os.path.join(os.path.expanduser("~"), "osouji_log.jsonl")  # 会話・命令の記憶(1行1件JSON)。学ぶロボの土台=外付けの記憶。
# --- ティーチング層(全サブシステム共通の設計。今は音声版。将来YOLO/アームも同じ層に差す) ---
TEACH_MODE      = os.environ.get("TEACH_MODE", "uncertain")  # "always"=毎回聞く / "uncertain"=自信が低い時だけ / "off"=聞かない
CONFIRM_LOGPROB = float(os.environ.get("CONFIRM_LOGPROB", "-1.0"))  # これ未満=怪しい＝uncertainで確認。0に近づけるほど確認が増える(例 CONFIRM_LOGPROB=-0.3)

# --- 「これ何?」視覚クエリ(カメラ→YOLO→Gemma) ---
CAM_INDEX       = int(os.environ.get("CAM", "0"))   # 「これ何?」で使うカメラ番号
YOLO_CONF_MIN   = 0.50    # YOLO検出スコアがこれ未満なら"自信ない"＝ティーチング層で確認
VISION_KEYWORDS = ("これなに", "これ何", "なにこれ", "何これ", "何が見え", "見えてる", "なに見え", "なに持っ")
# 「詳しく説明して」系は画像を丸ごとVLM(moondream)に見せる＝カスケードの“高い専門家”の段
VLM_MODEL       = os.environ.get("VLM_MODEL", "moondream")   # 画像を見て情景を語る小型VLM
VLM_KEYWORDS    = ("詳しく", "くわしく", "説明して", "どんな様子", "様子", "何が起き", "なにが起き", "状況", "描写")


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
def _ollama_generate(prompt, timeout=60):
    """OllamaにプロンプトをそのままPOSTして応答テキストを返す(低レベル)。"""
    resp = requests.post(
        OLLAMA_URL,
        json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=timeout,
    )
    resp.raise_for_status()
    return resp.json().get("response", "").strip()


def ask_gemma(user_text):
    """オソウジくんの人格で返事を作る。"""
    prompt = f"{SYSTEM_PROMPT}\n\nユーザー: {user_text}\nオソウジくん:"
    try:
        return _ollama_generate(prompt)
    except requests.exceptions.RequestException as e:
        return f"(Ollama に繋がりませんでした: {e})"


def decide_intent(text):
    """LLMルーター＝Gemmaに『今カメラで見る必要があるか』を判定させる(2階建ての“技能選択”)。
    出力は1語に固定(LOOK/CHAT)＋外れたらCHATに倒す(安全側)＝小型モデルでも堅い。
    返り値: 'look' か 'chat'。"""
    prompt = (
        "あなたはロボットの意図判定器です。次の発話が、いま目の前をカメラで見る必要があるものなら "
        "LOOK、そうでない普通の会話や移動指示なら CHAT と、1語だけ返してください。\n"
        "『これ何?』→LOOK\n『そこにあるやつ取って』→LOOK\n『何か落ちてない?』→LOOK\n"
        "『目の前に何がある?』→LOOK\n『今日の天気は?』→CHAT\n『こんにちは』→CHAT\n『進め』→CHAT\n"
        f"『{text}』→"
    )
    try:
        ans = _ollama_generate(prompt, timeout=30)
    except requests.exceptions.RequestException:
        return "chat"   # Ollama不通なら見に行かない(安全側)
    return "look" if "LOOK" in ans.upper() else "chat"


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
# == 記憶(ログ) と human-in-the-loop ==
# ============================================================
def log_event(user_text, command, reply, evaluation=None):
    """1回のやり取りを記憶(JSONL)に1行追記する。
    これが“学ぶロボ”の土台＝オットーのノート(外付けの記憶)。
    後で「赤いの取れ→過去ログ参照」や評価からの学習は、全部ここから生える。"""
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "user": user_text,       # 聞き取った言葉
        "command": command,      # 抽出した移動コマンド(無ければNone)
        "reply": reply,          # Gemmaの返事(無ければNone)
        "eval": evaluation,      # 人間の評価(HITL。無ければNone)
    }
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        print(f"[LOG] 記録できず: {e}")


def teach_confirm(source, guess, low_conf):
    """ティーチング層＝全サブシステム共通の“人間に教わる”入口。
    source: 'asr'/'yolo'/'arm' 等。guess: 予測。low_conf: そのサブシステムが『自信ない』か(bool)。
    ★自信度のスケールは各サブシステムで違う(ASR=logprob / YOLO=スコア)ので、判定は呼ぶ側でやり、
      この層には bool だけ渡す＝層は共通のまま使い回せる。
    TEACH_MODE に従って確認し、訂正されたら記憶に“教えた正解”として残す。
      always    … 毎回聞く(教育セッション)
      uncertain … 自信が低い時だけ聞く(通常運転＝能動学習)
      off       … 聞かない(自律)
    戻り値: (確定した内容, 続行するか)。x取消なら (None, False)。
    ※今はキーボード確認。将来ジェスチャー(👍/👎)や画面(?)でも受ける＝マルチモーダル。"""
    ask = (TEACH_MODE == "always") or (TEACH_MODE == "uncertain" and low_conf)
    if not ask:
        return guess, True
    try:
        ans = input(f"❓[{source}]『{guess}』でええ？ [Enter=はい / 正しい内容を入力 / x=取消]: ").strip()
    except EOFError:
        return guess, True
    if ans == "":
        return guess, True
    if ans.lower() == "x":
        return None, False
    log_event(guess, None, None, evaluation=f"teach:{source}:{ans}")   # 人間が教えた訂正を記憶
    return ans, True


# ============================================================
# == 「これ何?」= カメラ→YOLO→Gemma (視覚クエリ) ==
# ============================================================
_yolo = None   # YOLOモデルは初回呼び出し時だけ読み込む(起動を軽く保つ)

def is_vision_query(text):
    """「これ何?」系の問いかけかどうか。"""
    return any(k in text for k in VISION_KEYWORDS)

def look_objects():
    """カメラを1枚撮ってYOLO(CPU)で物体認識。(ラベル,自信度)を信頼度降順で返す。失敗はNone。
    ★GPUは使わない＝Gemmaと統合メモリを取り合わない＆JetsonのNVMLバグを回避。"""
    global _yolo
    try:
        import cv2
        if _yolo is None:
            from ultralytics import YOLO
            print("[YOLO] 初回ロード中…(少し待つ)")
            _yolo = YOLO("yolov8n.pt")
        cap = cv2.VideoCapture(CAM_INDEX)
        if not cap.isOpened():
            print(f"[YOLO] カメラ({CAM_INDEX})が開けない")
            return None
        ok, frame = cap.read()
        cap.release()
        if not ok:
            print("[YOLO] フレームが取れなかった")
            return None
        H, W = frame.shape[:2]
        res = _yolo(frame, device="cpu", verbose=False)[0]
        items = []
        for c, s, box in zip(res.boxes.cls, res.boxes.conf, res.boxes.xyxy):
            x1, y1, x2, y2 = [float(v) for v in box]
            cx   = (x1 + x2) / 2 / W                      # 横位置(0=左, 1=右)
            area = (x2 - x1) * (y2 - y1) / (W * H)        # 画面に占める割合=近さ
            horiz = "左" if cx < 0.34 else ("右" if cx > 0.66 else "中央")
            depth = "手前" if area > 0.15 else ("奥" if area < 0.05 else "")
            pos   = (depth + horiz) if depth else horiz   # 例: "手前右" / "中央"
            items.append((_yolo.names[int(c)], float(s), pos))
        items.sort(key=lambda x: -x[1])
        return items
    except Exception as e:
        print(f"[YOLO] 認識できず: {e} (ultralytics未導入かも)")
        return None


def describe_with_vlm():
    """カスケードの重い段＝画像そのものをVLM(moondream)に見せて情景を日本語で説明させる。
    YOLOがラベルだけなのに対し、これは“状況”を語れる(散らかった机にカップとリモコン…等)。
    失敗時はNone(ollama pull moondream 済みか/メモリを確認)。"""
    try:
        import cv2
        import base64
        cap = cv2.VideoCapture(CAM_INDEX)
        if not cap.isOpened():
            print(f"[VLM] カメラ({CAM_INDEX})が開けない")
            return None
        ok, frame = cap.read()
        cap.release()
        if not ok:
            print("[VLM] フレームが取れなかった")
            return None
        ok2, buf = cv2.imencode(".jpg", frame)
        if not ok2:
            return None
        b64 = base64.b64encode(buf.tobytes()).decode()
        # ★moondreamは英語ベースの小型VLM。日本語で聞くとゴミ("スキップ"等)を返すので
        #   英語で説明させる→次にGemmaで日本語の話し言葉へ言い換える(=言語カスケードの2段目)。
        resp = requests.post(
            OLLAMA_URL,
            json={"model": VLM_MODEL,
                  "prompt": "Describe what is in this image in one or two short, plain sentences.",
                  "images": [b64], "stream": False},
            timeout=120,   # モデル切替(スワップ)で遅いことがある
        )
        resp.raise_for_status()
        en = resp.json().get("response", "").strip()
        if not en:
            return None
        print(f"[VLM] moondream(英語): {en}")
        # 英語→日本語へ言い換え(Gemma)。Ollama不通ならせめて英語をそのまま返す。
        try:
            ja = _ollama_generate(
                "次の英語を、親しみやすい日本語の話し言葉で短く言い換えて。"
                "記号や英語は使わない。\n\n" + en + "\n\n日本語:",
                timeout=60,
            ).strip()
            return ja or en
        except requests.exceptions.RequestException:
            return en
    except requests.exceptions.RequestException as e:
        print(f"[VLM] moondream呼べず: {e} (ollama pull moondream 済み?)")
        return None
    except Exception as e:
        print(f"[VLM] 失敗: {e}")
        return None


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

            # --- 3. 認識(＋自信度) ---
            segments, _ = model.transcribe(audio, language="ja", beam_size=1,
                                           vad_filter=True)
            seg_list = list(segments)
            text = "".join(s.text for s in seg_list).strip()
            if not text:
                print("(聞き取れませんでした)\n")
                continue
            # 自信度＝各セグメントの avg_logprob の平均(0に近いほど自信あり)
            conf = sum(s.avg_logprob for s in seg_list) / len(seg_list) if seg_list else -9.9
            print(f"🗣  あなた: {text}   [{reason} / 自信度={conf:.2f}]")

            # --- 3b. ティーチング層: 怪しい時(or alwaysモード)は人間に確認 ---
            text, go = teach_confirm("asr", text, conf < CONFIRM_LOGPROB)
            if not go:
                print("(取消。待機に戻ります)\n")
                continue

            # --- 4. 移動コマンド(キーワード高速パス=反応層。LLM非経由で即サーボ) ---
            cmd = extract_command(text)
            if cmd:
                on_command(cmd)

            # --- 5. 見るべきか判定: キーワード or Gemmaルーター(2階建ての“技能選択”) ---
            #   「これ何?」等=即LOOK。「詳しく説明して」等=VLMへエスカレーション。それ以外はGemma判定。
            want_detail = any(k in text for k in VLM_KEYWORDS)   # 情景を"描写"してほしい系
            want_look = is_vision_query(text) or want_detail
            if (not want_look) and USE_LLM_ROUTER and USE_GEMMA and (not cmd):
                want_look = (decide_intent(text) == "look")

            # --- 6. 返事: 見る(カメラ→YOLO→Gemma) or 会話(Gemma) ---
            reply = None
            if want_look and want_detail:
                # ★カスケードの重い段: 画像そのものをVLM(moondream)に見せて情景を語らせる
                print("[VLM] moondreamで情景説明…(モデル切替で数秒待つことあり)")
                reply = describe_with_vlm() or "うまく見れんかったわ"
            elif want_look:
                items = look_objects()
                if items:
                    print("[YOLO] 検出: " + "、".join(f"{p}{n}({c:.2f})" for n, c, p in items[:5]))
                    top_name, top_conf, top_pos = items[0]
                    fixed, go = teach_confirm("yolo", top_name, top_conf < YOLO_CONF_MIN)  # ティーチング層に差す
                    if not go:
                        print("(取消。待機に戻ります)\n")
                        continue
                    # 位置つきで説明用テキストを作る(例:「手前右にコップ、中央に人」)＝Gemmaが空間説明できる
                    parts = [f"{top_pos}に{fixed}"] + [f"{p}に{n}" for n, _, p in items[1:4]]
                    seen = "、".join(parts)
                else:
                    seen = "何も見当たりません"
                reply = ask_gemma(f"(あなたはカメラで今こう見えています: {seen}) 目の前の様子を短く説明して")
            elif USE_GEMMA:
                reply = ask_gemma(text)

            if reply is not None:
                print(f"🤖 オソウジくん: {reply}")
                speak(reply)

            # --- 7. 記憶(ログ): 毎回のやり取りを記憶に残す ---
            log_event(text, cmd or ("LOOK" if want_look else None), reply)

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
