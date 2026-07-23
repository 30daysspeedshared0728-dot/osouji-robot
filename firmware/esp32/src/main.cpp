// XIAO ESP32S3 Sense マイク 音量メーター版
// PDMマイクを読んで「音量レベル」を数字＋バーで出す。喋ると跳ねる。
//
// ★ポイント: PDMマイクの生値には一定のゲタ(DCオフセット, 1300付近)が乗ってる。
//   そのまま絶対値を見ると音の変化がゲタに埋もれるので、
//   まず平均(DC)を出して各サンプルから引き、"音だけ"の振幅を見る。
//   → 静かな時はほぼ0、喋ると跳ねる、が綺麗に出る。
//
// PDM CLK = GPIO42 / PDM DATA = GPIO41 (Seeed公式)

#include <I2S.h>

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  delay(300);
  Serial.println("=== マイク音量メーター(喋ると跳ねる) ===");

  I2S.setAllPins(-1, 42, 41, -1, -1);   // fs=CLK(42) / sd=DATA(41)
  if (!I2S.begin(PDM_MONO_MODE, 16000, 16)) {
    Serial.println("I2S(PDM)初期化に失敗");
    while (1) { delay(1000); }
  }
  Serial.println("マイク初期化OK。");
}

void loop() {
  const int N = 512;
  static int16_t buf[N];
  int count = 0;
  long sum = 0;

  // まず N 回読んで、有効サンプルを buf に貯める＆合計(DC推定用)
  for (int i = 0; i < N; i++) {
    int s = I2S.read();
    if (s == 0 || s == -1 || s == 1) continue;  // 無効値は捨てる
    int16_t v = (int16_t)s;
    buf[count++] = v;
    sum += v;
  }

  int dc = count ? (int)(sum / count) : 0;   // DCオフセット(平均のゲタ)

  // DCを引いた"音だけ"の振幅ピークを求める
  long peak = 0;
  for (int i = 0; i < count; i++) {
    long a = buf[i] - dc;
    if (a < 0) a = -a;
    if (a > peak) peak = a;
  }

  // バーグラフ表示(1文字=約100。うるさすぎ/敏感すぎたら100を上下)
  int bars = (int)(peak / 100);
  int n = bars > 40 ? 40 : bars;
  char bar[41];
  for (int i = 0; i < n; i++) bar[i] = '#';
  bar[n] = '\0';

  Serial.printf("level:%5ld  |%s\n", peak, bar);
  delay(100);
}
