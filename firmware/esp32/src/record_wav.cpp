/*
 * おそうじくん 学習データ録音スケッチ（XIAO ESP32S3 Sense）
 * XIAO本体のPDMマイクで録音し、SDカードに .wav で保存する。
 * → この .wav を Edge Impulse に「Upload existing data」で上げて学習する。
 *
 * ★これが今回のリベンジの肝：本番と同じ「XIAO実機マイク」で録る。
 *   さらに クーラー/テレビ等の生活音を入れて、色んな距離・トーンで録ること。
 *
 * ★arduino-esp32 core 2.0.x 版（I2S.h / esp_i2s を使用。今のPlatformIO環境に合わせた）
 *   ベース: Seeed / MJRoBot の KWS 録音サンプルを改変。
 *
 * 【必要なもの】
 *   - Senseの拡張ボードが正しく装着されてること（逆挿し厳禁！生値が固定＆発熱する）
 *   - microSDカード（32GB以下・FAT32でフォーマット済み）を挿しておく
 *
 * 【使い方（シリアルモニタ 115200）】
 *   1) ラベル名を入力してEnter（例: osoujikun / noise / unknown）
 *   2) rec と入力してEnter → 10秒録音。その10秒の中で「おそうじくん」を6〜8回くり返す
 *   3) また rec でもう1本。ファイルは osoujikun.1.wav, osoujikun.2.wav ... と増える
 *   4) 別クラスを録るときは新しいラベル名を入力してEnter → また rec
 *   ※ noise / unknown は自分で少し録ってもいいが、Edge Impulseの無料の
 *     ノイズ/その他ワードの詰め合わせを取り込むのが楽。
 */

#include <I2S.h>
#include "FS.h"
#include "SD.h"
#include "SPI.h"

// ---- 必要なら変える ----
#define RECORD_TIME   10   // 秒（最大240）。10秒の中でキーワードを何回もくり返す
// ------------------------

// ここは基本さわらない
#define SAMPLE_RATE     16000U
#define SAMPLE_BITS     16
#define WAV_HEADER_SIZE 44
#define VOLUME_GAIN     2   // 録音が小さいとき上げる（2〜4くらい）

int    fileNumber   = 1;
String baseFileName = "";
bool   isRecording  = false;

void generate_wav_header(uint8_t *wav_header, uint32_t wav_size, uint32_t sample_rate);
void record_wav(String fileName);

void setup() {
  Serial.begin(115200);
  while (!Serial) ;

  // XIAO ESP32S3 Sense の PDMマイク: CLK=GPIO42 / DATA=GPIO41
  I2S.setAllPins(-1, 42, 41, -1, -1);
  if (!I2S.begin(PDM_MONO_MODE, SAMPLE_RATE, SAMPLE_BITS)) {
    Serial.println("I2S(マイク)の初期化に失敗。拡張ボードの装着向きを確認して。");
    while (1) ;
  }
  if (!SD.begin(21)) {
    Serial.println("SDカードが見つからへん。32GB以下/FAT32で挿さってるか確認して。");
    while (1) ;
  }

  Serial.println("=== おそうじくん 録音モード ===");
  Serial.println("まずラベル名を入力してEnter（例: osoujikun / noise / unknown）");
}

void loop() {
  if (Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();
    if (command == "rec") {
      if (baseFileName == "") {
        Serial.println("先にラベル名を入力してな（例: osoujikun）");
      } else {
        isRecording = true;
      }
    } else {
      baseFileName = command;
      fileNumber = 1; // 新ラベルごとに番号リセット
      Serial.printf("ラベル [%s] をセット。rec と入力すると10秒録音するで\n", baseFileName.c_str());
    }
  }

  if (isRecording && baseFileName != "") {
    String fileName = "/" + baseFileName + "." + String(fileNumber) + ".wav";
    fileNumber++;
    record_wav(fileName);
    delay(1000);
    isRecording = false;
  }
}

void record_wav(String fileName) {
  uint32_t sample_size = 0;
  uint32_t record_size = (SAMPLE_RATE * SAMPLE_BITS / 8) * RECORD_TIME;
  uint8_t *rec_buffer = NULL;
  Serial.printf("録音開始 (%d秒) ... いま喋って！\n", RECORD_TIME);

  File file = SD.open(fileName.c_str(), FILE_WRITE);
  uint8_t wav_header[WAV_HEADER_SIZE];
  generate_wav_header(wav_header, record_size, SAMPLE_RATE);
  file.write(wav_header, WAV_HEADER_SIZE);

  // 録音バッファは PSRAM に確保
  rec_buffer = (uint8_t *)ps_malloc(record_size);
  if (rec_buffer == NULL) {
    Serial.println("メモリ確保に失敗（PSRAM設定を確認）");
    file.close();
    return;
  }

  esp_i2s::i2s_read(esp_i2s::I2S_NUM_0, rec_buffer, record_size, &sample_size, portMAX_DELAY);
  if (sample_size == 0) {
    Serial.println("録音失敗");
    free(rec_buffer);
    file.close();
    return;
  }

  // 音量を少し持ち上げる
  for (uint32_t i = 0; i < sample_size; i += SAMPLE_BITS / 8) {
    (*(uint16_t *)(rec_buffer + i)) <<= VOLUME_GAIN;
  }

  file.write(rec_buffer, record_size);
  free(rec_buffer);
  file.close();

  Serial.printf("保存完了: %s\n", fileName.c_str());
  Serial.println("→ もう1本なら rec / 別クラスなら新しいラベル名を入力\n");
}

void generate_wav_header(uint8_t *wav_header, uint32_t wav_size, uint32_t sample_rate) {
  uint32_t file_size = wav_size + WAV_HEADER_SIZE - 8;
  uint32_t byte_rate = SAMPLE_RATE * SAMPLE_BITS / 8;
  const uint8_t set_wav_header[] = {
    'R', 'I', 'F', 'F',
    (uint8_t)file_size, (uint8_t)(file_size >> 8), (uint8_t)(file_size >> 16), (uint8_t)(file_size >> 24),
    'W', 'A', 'V', 'E',
    'f', 'm', 't', ' ',
    0x10, 0x00, 0x00, 0x00,
    0x01, 0x00,
    0x01, 0x00,
    (uint8_t)sample_rate, (uint8_t)(sample_rate >> 8), (uint8_t)(sample_rate >> 16), (uint8_t)(sample_rate >> 24),
    (uint8_t)byte_rate, (uint8_t)(byte_rate >> 8), (uint8_t)(byte_rate >> 16), (uint8_t)(byte_rate >> 24),
    0x02, 0x00,
    0x10, 0x00,
    'd', 'a', 't', 'a',
    (uint8_t)wav_size, (uint8_t)(wav_size >> 8), (uint8_t)(wav_size >> 16), (uint8_t)(wav_size >> 24),
  };
  memcpy(wav_header, set_wav_header, sizeof(set_wav_header));
}
