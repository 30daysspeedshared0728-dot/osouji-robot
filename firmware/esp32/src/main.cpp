/*
 * おそうじくん ウェイクワード検出（XIAO ESP32S3 Sense）★連続推論版
 * Edge Impulse の推論を「窓を重ねながら」連続実行する（run_classifier_continuous）。
 *
 * ★なぜ連続版か：
 *   前の版は2秒ごとにブツ切りで一発判定＝窓が重ならないので、「おそうじくん」が
 *   窓の境目で分断されると丸ごと入らず osoujikun=0.00 になりがちだった。
 *   連続版は窓を SLICE ずつスライドさせて重ねるので、発話を捉えやすい。
 *
 * ★役割分担（この設計が正）：
 *    XIAO = 耳（検出だけ）→ 検出したらUSBシリアルに "WAKE" を1行送る
 *    Jetson = 脳 / Pico = 手足（サーボ）。サーボはXIAOでは動かさない。
 *
 * ★音量ゲインは録音スケッチ(record_wav.cpp, VOLUME_GAIN=2=×4)と揃えて ×4。
 *
 * ★arduino-esp32 core 2.0.x 版（I2S.h / esp_i2s を使用）
 * ベース: Edge Impulse 連続音声推論サンプルを XIAO 用に改変
 */

// メモリ節約(必要なら)
#define EIDSP_QUANTIZE_FILTERBANK   0
// ★窓を何枚に分けてスライドさせるか（多いほど細かく重なる。3〜4が定番）
#define EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW 3

#include <osouji-wake-word_inferencing.h>
#include <I2S.h>

#define SAMPLE_RATE 16000U
#define SAMPLE_BITS 16

#define LED_BUILT_IN 21
#define DETECT_LABEL "osoujikun"
#define DETECT_THRESHOLD 0.70f          // 反応弱ければ0.60に下げる
#define DETECT_COOLDOWN_MS 2000UL       // 検出後この時間はWAKEを撃たない（連発防止）

/** ダブルバッファ方式の音声取り込み */
typedef struct {
    signed short *buffers[2];
    unsigned char buf_select;
    unsigned char buf_ready;
    unsigned int  buf_count;
    unsigned int  n_samples;
} inference_t;

static inference_t inference;
static bool record_status = true;
static unsigned long last_detect_ms = 0;

// I2Sから一度に読む一時バッファ
static const uint32_t i2s_chunk_samples = 1024;
static signed short  i2s_tmp[i2s_chunk_samples];

/* 関数プロトタイプ */
static bool microphone_inference_start(uint32_t n_samples);
static bool microphone_inference_record(void);
static int  microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr);
static void capture_samples(void* arg);

void setup() {
    Serial.begin(115200);
    unsigned long t0 = millis();
    while (!Serial && millis() - t0 < 3000) { delay(10); }
    Serial.println("Edge Impulse KWS(連続版): おそうじくん検出（検出時は WAKE を送信）");

    pinMode(LED_BUILT_IN, OUTPUT);
    digitalWrite(LED_BUILT_IN, HIGH); // HIGH=消灯

    // PDMマイク
    I2S.setAllPins(-1, 42, 41, -1, -1);
    if (!I2S.begin(PDM_MONO_MODE, SAMPLE_RATE, SAMPLE_BITS)) {
        Serial.println("I2S初期化失敗");
        while (1) ;
    }

    ei_printf("推論設定: %d ms window, %d slices, %d classes\n",
              (int)(EI_CLASSIFIER_RAW_SAMPLE_COUNT / 16),
              (int)EI_CLASSIFIER_SLICES_PER_MODEL_WINDOW,
              (int)(sizeof(ei_classifier_inferencing_categories) /
                    sizeof(ei_classifier_inferencing_categories[0])));

    // 連続推論の初期化（内部のスライド窓バッファを用意）
    run_classifier_init();

    if (microphone_inference_start(EI_CLASSIFIER_SLICE_SIZE) == false) {
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
    signal.total_length = EI_CLASSIFIER_SLICE_SIZE;
    signal.get_data = &microphone_audio_signal_get_data;
    ei_impulse_result_t result = { 0 };

    // ★連続推論：1スライスごとに窓をスライドさせて判定
    if (run_classifier_continuous(&signal, &result, false) != EI_IMPULSE_OK) {
        ei_printf("ERR: 推論失敗\n");
        return;
    }

    // スコア表示＋おそうじくん取り出し
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

    // 検出判定（クールダウン中は撃たない）
    if (osouji_score > DETECT_THRESHOLD &&
        millis() - last_detect_ms > DETECT_COOLDOWN_MS) {
        last_detect_ms = millis();

        Serial.println("WAKE");                       // ★Jetsonが読む合図
        ei_printf(">>> おそうじくん 検出! (%.2f)\n", osouji_score);

        digitalWrite(LED_BUILT_IN, LOW);              // 点灯
        delay(600);
        digitalWrite(LED_BUILT_IN, HIGH);             // 消灯
    }
}

// ===== マイク取り込み（ダブルバッファ＋連続スライス） =====

static void capture_samples(void* arg) {
    (void)arg;
    while (record_status) {
        size_t bytes_read = 0;
        esp_i2s::i2s_read(esp_i2s::I2S_NUM_0, (void*)i2s_tmp,
                          i2s_chunk_samples * 2, &bytes_read, 100);
        int got = bytes_read / 2;
        for (int i = 0; i < got; i++) {
            // 音量を録音時と同じ ×4 に揃える
            signed short v = (signed short)((int16_t)i2s_tmp[i] * 4);
            inference.buffers[inference.buf_select][inference.buf_count++] = v;

            if (inference.buf_count >= inference.n_samples) {
                inference.buf_select ^= 1;   // バッファ入れ替え
                inference.buf_count = 0;
                inference.buf_ready = 1;
            }
        }
        if (!record_status) break;
    }
    vTaskDelete(NULL);
}

static bool microphone_inference_start(uint32_t n_samples) {
    inference.buffers[0] = (signed short *)malloc(n_samples * sizeof(signed short));
    if (inference.buffers[0] == NULL) return false;
    inference.buffers[1] = (signed short *)malloc(n_samples * sizeof(signed short));
    if (inference.buffers[1] == NULL) { free(inference.buffers[0]); return false; }

    inference.buf_select = 0;
    inference.buf_count  = 0;
    inference.n_samples  = n_samples;
    inference.buf_ready  = 0;

    ei_sleep(100);
    record_status = true;
    xTaskCreate(capture_samples, "CaptureSamples", 1024 * 32, NULL, 10, NULL);
    return true;
}

static bool microphone_inference_record(void) {
    while (inference.buf_ready == 0) {
        delay(1);
    }
    inference.buf_ready = 0;
    return true;
}

// 直前に書き終わった側のバッファ（buf_select と逆）を読む
static int microphone_audio_signal_get_data(size_t offset, size_t length, float *out_ptr) {
    numpy::int16_to_float(&inference.buffers[inference.buf_select ^ 1][offset], out_ptr, length);
    return 0;
}

#if !defined(EI_CLASSIFIER_SENSOR) || EI_CLASSIFIER_SENSOR != EI_CLASSIFIER_SENSOR_MICROPHONE
#error "Invalid model for current sensor."
#endif
