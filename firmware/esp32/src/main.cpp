/*
 * おそうじくん ウェイクワード検出 → サーボを回す
 * Edge Impulse(TensorFlow Lite/int8) の推論を XIAO ESP32S3 Sense で連続実行。
 * 「おそうじくん」を検出したら、オンボードLED点灯＋サーボをピクッと振る。
 *
 * ★arduino-esp32 core 2.0.x 版（I2S.h / esp_i2s を使用。今のPlatformIO環境に合わせる）
 *
 * 配線:
 *   マイク(基板): PDM CLK=GPIO42 / DATA=GPIO41（半田不要）
 *   LED: GPIO21（オンボード。SDと共用だが推論中はSD未使用なのでOK）
 *   サーボ: 信号(橙)→GPIO2(D1) / 電源(赤)→5V / GND(茶黒)→GND
 *     ※サーボ未接続でも、LEDとSerialで検出は確認できる
 *
 * ベース: Seeed / MJRoBot の KWSサンプルを改変
 */

// メモリ節約(必要なら)
#define EIDSP_QUANTIZE_FILTERBANK   0

#include <osouji-wake-word_inferencing.h>
#include <I2S.h>

#define SAMPLE_RATE 16000U
#define SAMPLE_BITS 16

#define LED_BUILT_IN 21
#define SERVO_PIN    2       // D1 = GPIO2
#define SERVO_CH     0       // LEDC チャンネル
#define DETECT_LABEL "osoujikun"
#define DETECT_THRESHOLD 0.70f   // このスコアを超えたら「検出」

/** 推論用バッファ */
typedef struct {
    int16_t *buffer;
    uint8_t  buf_ready;
    uint32_t buf_count;
    uint32_t n_samples;
} inference_t;

static inference_t inference;
static const uint32_t sample_buffer_size = 2048;
static signed short sampleBuffer[sample_buffer_size];
static bool debug_nn = false;
static bool record_status = true;

// --- サーボ制御 (50Hz / 16bit PWM) ---
// 0.5ms→duty 1638 / 2.5ms→duty 8192 （周期20ms=65536）
void setServoAngle(int deg) {
    int duty = map(deg, 0, 180, 1638, 8192);
    ledcWrite(SERVO_CH, duty);
}

void wiggleServo() {           // 検出時の動き: 90→180→90
    setServoAngle(180);
    delay(400);
    setServoAngle(90);
    delay(200);
}

/* 関数プロトタイプ */
static bool microphone_inference_start(uint32_t n_samples);
static bool microphone_inference_record(void);
static int  microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr);
static void capture_samples(void* arg);
static void audio_inference_callback(uint32_t n_bytes);

void setup() {
    Serial.begin(115200);
    // XIAOのネイティブUSB。Serial待ちは最大3秒でタイムアウト（モニタ無しでも動く）
    unsigned long t0 = millis();
    while (!Serial && millis() - t0 < 3000) { delay(10); }
    Serial.println("Edge Impulse KWS: おそうじくん検出デモ");

    pinMode(LED_BUILT_IN, OUTPUT);
    digitalWrite(LED_BUILT_IN, HIGH); // オンボードLEDはLOWで点灯なのでHIGH=消灯

    // サーボPWM初期化
    ledcSetup(SERVO_CH, 50, 16);
    ledcAttachPin(SERVO_PIN, SERVO_CH);
    setServoAngle(90);                // 起動時は中央

    // PDMマイク
    I2S.setAllPins(-1, 42, 41, -1, -1);
    if (!I2S.begin(PDM_MONO_MODE, SAMPLE_RATE, SAMPLE_BITS)) {
        Serial.println("I2S初期化失敗");
        while (1) ;
    }

    ei_printf("推論設定: %d ms window, %d classes\n",
              (int)(EI_CLASSIFIER_RAW_SAMPLE_COUNT / 16),
              (int)(sizeof(ei_classifier_inferencing_categories) /
                    sizeof(ei_classifier_inferencing_categories[0])));

    if (microphone_inference_start(EI_CLASSIFIER_RAW_SAMPLE_COUNT) == false) {
        ei_printf("ERR: 音声バッファ確保失敗\n");
        while (1) ;
    }
    ei_printf("録音＆推論開始。「おそうじくん」と言ってみて。\n");
}

void loop() {
    if (!microphone_inference_record()) {
        ei_printf("ERR: 録音失敗\n");
        return;
    }

    signal_t signal;
    signal.total_length = EI_CLASSIFIER_RAW_SAMPLE_COUNT;
    signal.get_data = &microphone_audio_signal_get_data;
    ei_impulse_result_t result = { 0 };

    if (run_classifier(&signal, &result, debug_nn) != EI_IMPULSE_OK) {
        ei_printf("ERR: 推論失敗\n");
        return;
    }

    // 全クラスのスコアを出しつつ、おそうじくんのスコアを取り出す
    float osouji_score = 0.0f;
    ei_printf("予測: ");
    for (size_t ix = 0; ix < EI_CLASSIFIER_LABEL_COUNT; ix++) {
        ei_printf("%s=%.2f ", result.classification[ix].label,
                              result.classification[ix].value);
        if (strcmp(result.classification[ix].label, DETECT_LABEL) == 0) {
            osouji_score = result.classification[ix].value;
        }
    }
    ei_printf("\n");

    // 検出判定
    if (osouji_score > DETECT_THRESHOLD) {
        ei_printf(">>> おそうじくん 検出! (%.2f)\n", osouji_score);
        digitalWrite(LED_BUILT_IN, LOW);   // LED点灯
        wiggleServo();                     // サーボを振る
        digitalWrite(LED_BUILT_IN, HIGH);  // 消灯
    }
}

// ===== 以下、Edge Impulse KWSサンプルのマイク取り込み処理（ほぼ定型） =====

static void audio_inference_callback(uint32_t n_bytes) {
    for (int i = 0; i < n_bytes >> 1; i++) {
        inference.buffer[inference.buf_count++] = sampleBuffer[i];
        if (inference.buf_count >= inference.n_samples) {
            inference.buf_count = 0;
            inference.buf_ready = 1;
        }
    }
}

static void capture_samples(void* arg) {
    const int32_t i2s_bytes_to_read = (uint32_t)arg;
    size_t bytes_read = i2s_bytes_to_read;

    while (record_status) {
        esp_i2s::i2s_read(esp_i2s::I2S_NUM_0, (void*)sampleBuffer,
                          i2s_bytes_to_read, &bytes_read, 100);
        if (bytes_read <= 0) {
            ei_printf("I2S readエラー: %d\n", (int)bytes_read);
        } else {
            // 音量が小さいので増幅
            for (int x = 0; x < i2s_bytes_to_read / 2; x++) {
                sampleBuffer[x] = (int16_t)(sampleBuffer[x]) * 8;
            }
            if (record_status) {
                audio_inference_callback(i2s_bytes_to_read);
            } else {
                break;
            }
        }
    }
    vTaskDelete(NULL);
}

static bool microphone_inference_start(uint32_t n_samples) {
    inference.buffer = (int16_t *)malloc(n_samples * sizeof(int16_t));
    if (inference.buffer == NULL) return false;
    inference.buf_count = 0;
    inference.n_samples = n_samples;
    inference.buf_ready = 0;
    ei_sleep(100);
    record_status = true;
    xTaskCreate(capture_samples, "CaptureSamples", 1024 * 32,
                (void*)sample_buffer_size, 10, NULL);
    return true;
}

static bool microphone_inference_record(void) {
    while (inference.buf_ready == 0) {
        delay(10);
    }
    inference.buf_ready = 0;
    return true;
}

static int microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr) {
    numpy::int16_to_float(&inference.buffer[offset], out_ptr, length);
    return 0;
}

#if !defined(EI_CLASSIFIER_SENSOR) || EI_CLASSIFIER_SENSOR != EI_CLASSIFIER_SENSOR_MICROPHONE
#error "Invalid model for current sensor."
#endif
