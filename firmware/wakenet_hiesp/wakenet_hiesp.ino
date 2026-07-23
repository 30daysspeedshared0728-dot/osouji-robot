/*
 * WakeNet "Hi ESP" 最小テスト（XIAO ESP32S3 Sense）
 * Espressif公式 ESP-SR / WakeNet を使い、「Hi ESP」と言ったら検出する。
 * 検出したら：オンボードLED点灯 ＋ シリアルに "WAKE" を1行。
 *
 * ★これは英語の既定ウェイクワード "Hi ESP"。学習ゼロ・完全オフラインで動く。
 *   まず「実機で1個ウェイクワードが光る」を成立させるのが目的。
 *   日本語「おそうじくん」は別途リベンジ。
 *
 * ★必要環境（Arduino IDE）：
 *   - esp32 core 3.x（ESP_SR同梱）… 既にインストール済み
 *   - Board: XIAO_ESP32S3 / PSRAM: OPI PSRAM
 *   - Partition Scheme: アプリ領域が大きいもの（コンパイルが容量で落ちたら変更）
 *
 * ★XIAO Sense の PDMマイク: CLK=GPIO42 / DATA=GPIO41（基板上・半田不要）
 *   公式例のPDMピンをこの2つに変更しただけ。
 *
 * ベース: arduino-esp32 の ESP_SR/Basic 例を XIAO 用に簡略化
 */

#include <Arduino.h>
#include "ESP_I2S.h"
#include "ESP_SR.h"

// ★XIAO ESP32S3 Sense のPDMマイク・ピン
#define PDM_PIN_CLK  42
#define PDM_PIN_DATA 41

// オンボードLED（XIAOはGPIO21、LOWで点灯）
#ifndef LED_BUILTIN
#define LED_BUILTIN 21
#endif
#define LED_PIN LED_BUILTIN

// ESP_SR は 16bit / 16kHz 固定
#define I2S_SAMPLE_RATE 16000
#define I2S_DATA_WIDTH  I2S_DATA_BIT_WIDTH_16BIT

// モノラルマイク（1ch）
#define SR_INPUT_FORMAT     "M"
#define SR_INPUT_CHANNELS   SR_CHANNELS_MONO
#define I2S_OUTPUT_CHANNELS I2S_SLOT_MODE_MONO

I2SClass i2s;

// コマンド認識は今回使わないが、begin() が配列を要求するのでダミーを1個だけ置く
enum { SR_CMD_DUMMY };
static const sr_cmd_t sr_commands[] = {
  {SR_CMD_DUMMY, "Turn on the light"},
};

void onSrEvent(sr_event_t event, int command_id, int phrase_id) {
  switch (event) {
    case SR_EVENT_WAKEWORD:
      // ★「Hi ESP」検出！
      Serial.println("WAKE");                 // Jetsonが読む合図（この1行）
      Serial.println(">>> Hi ESP 検出!");
      digitalWrite(LED_PIN, LOW);             // LED点灯
      delay(600);
      digitalWrite(LED_PIN, HIGH);            // 消灯
      // コマンドモードには入らず、ウェイクワード検出のまま待機
      ESP_SR.setMode(SR_MODE_WAKEWORD);
      break;
    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);
  unsigned long t0 = millis();
  while (!Serial && millis() - t0 < 3000) { delay(10); }
  Serial.println("WakeNet Hi ESP テスト開始。「Hi ESP」と言ってみて。");

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);  // 消灯（XIAOはHIGH=消灯）

  Serial.println("STEP1: LEDセット完了"); Serial.flush(); delay(50);

  i2s.setTimeout(1000);
  i2s.setPinsPdmRx(PDM_PIN_CLK, PDM_PIN_DATA);
  Serial.println("STEP2: I2Sピン設定完了。これからi2s.begin"); Serial.flush(); delay(50);

  // XIAO Sense の PDMマイクで開始
  bool i2s_ok = i2s.begin(I2S_MODE_PDM_RX, I2S_SAMPLE_RATE, I2S_DATA_WIDTH,
                          I2S_OUTPUT_CHANNELS, I2S_STD_SLOT_RIGHT);
  Serial.printf("STEP3: i2s.begin 戻り値=%d\n", i2s_ok); Serial.flush(); delay(50);

  ESP_SR.onEvent(onSrEvent);
  Serial.println("STEP4: これから ESP_SR.begin（ここで落ちるならモデル/AFE）"); Serial.flush(); delay(50);

  // ★コマンド認識(MultiNet)は使わない＝NULL,0 を渡してウェイクワードのみ。
  //   これでMultiNetモデル未搭載によるヌル参照クラッシュを回避する。
  ESP_SR.begin(i2s, NULL, 0,
               SR_INPUT_CHANNELS, SR_MODE_WAKEWORD, SR_INPUT_FORMAT);

  Serial.println("STEP5: ESP_SR.begin 完了 = 準備OK！「Hi ESP」で光るはず。"); Serial.flush(); delay(50);
}

void loop() {
  delay(100);
}
