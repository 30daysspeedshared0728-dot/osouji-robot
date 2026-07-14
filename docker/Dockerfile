# 開発PC(x86_64)用。Jetson移行時は arm64 の l4t ベースに差し替える。
FROM python:3.11-slim

# OpenCV / MediaPipe / 音声に必要なシステムライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender1 \
    libportaudio2 \
    libgomp1 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# デフォルトはジェスチャー。音声にしたい時は compose の command を上書き。
CMD ["python", "perception/gesture_control.py"]
