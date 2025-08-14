// Minimal HW Debug + GPS passthrough
// - LoRa: perpetual RX, print packets
// - Light sensor: print once per second
// - Relay/Buzzer: toggle every 5 seconds
// - GPS: raw passthrough from GPS UART -> USB Serial

#include "Arduino.h"
#include "LoRaWan_APP.h"

// ================== Pins (adjust if needed) ==================
#define LIGHTPIN   3      // Analog light sensor pin
#define RELAYPIN   2      // Relay pin
#define BUZZERPIN  5      // Buzzer pin

// GPS UART on ESP32/Heltec WiFi LoRa boards (UART1)
#define GPS_RX_PIN 33     // GPS TX -> Board RX
#define GPS_TX_PIN 34     // GPS RX -> Board TX
#define GPS_BAUD   9600

// ================== LoRa (EU868 minimal RX) ==================
#define RF_FREQUENCY           868100000   // Hz
#define TX_OUTPUT_POWER        5           // dBm
#define LORA_BANDWIDTH         0           // 125 kHz
#define LORA_SPREADING_FACTOR  7
#define LORA_CODINGRATE        1           // 4/5
#define LORA_PREAMBLE_LENGTH   8
#define LORA_SYMBOL_TIMEOUT    0
#define LORA_FIX_LENGTH_ON     false
#define LORA_IQ_INVERSION_ON   false

static RadioEvents_t RadioEvents;
static volatile bool lora_idle = true;

static const uint16_t BUFFER_SIZE = 256;
static char rxBuf[BUFFER_SIZE];

// ================== Timers ==================
uint32_t lastLightPrintMs = 0;
uint32_t lastToggleMs     = 0;
bool     outputsOn        = false;

// ================== GPS UART ==================
HardwareSerial GPSSerial(1);  // UART1 on ESP32-class boards

// Forward decls
void OnTxDone(void);
void OnTxTimeout(void);
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr);
void OnRxTimeout(void);

// ============== Setup =================
void setup() {
  Serial.begin(115200);
  while (!Serial) { ; }

  // IO
  pinMode(LIGHTPIN,  INPUT);
  pinMode(RELAYPIN,  OUTPUT);
  pinMode(BUZZERPIN, OUTPUT);
  digitalWrite(RELAYPIN,  LOW);
  digitalWrite(BUZZERPIN, LOW);

  // GPS UART
  GPSSerial.begin(GPS_BAUD, SERIAL_8N1, GPS_RX_PIN, GPS_TX_PIN);

  // Heltec/Radio init
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);

  RadioEvents.TxDone    = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxDone    = OnRxDone;
  RadioEvents.RxTimeout = OnRxTimeout;

  Radio.Init(&RadioEvents);

  Radio.SetChannel(RF_FREQUENCY);
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000);

  Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                    LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                    LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_ON,
                    0, true, 0, 0, LORA_IQ_INVERSION_ON, true);

  // Start perpetual RX
  lora_idle = true;
  Radio.Rx(0);

  Serial.println("=== Minimal HW Debug: LoRa RX, Light print, Relay/Buzzer 5s toggle, GPS passthrough ===");
}

// ============== Loop =================
void loop() {
  // Process radio IRQs
  Radio.IrqProcess();

  const uint32_t now = millis();

  // 1) GPS passthrough: dump raw NMEA from GPS UART to USB Serial
  while (GPSSerial.available()) {
    int b = GPSSerial.read();
    if (b >= 0) Serial.write((uint8_t)b);
  }

  // 2) Print light sensor once per second
  if ((now - lastLightPrintMs) >= 1000) {
    int light = analogRead(LIGHTPIN);
    Serial.print("\nLight: ");
    Serial.println(light);
    lastLightPrintMs = now;
  }

  // 3) Toggle relay & buzzer every 5 seconds
  if ((now - lastToggleMs) >= 5000) {
    outputsOn = !outputsOn;
    digitalWrite(RELAYPIN,  outputsOn ? HIGH : LOW);
    digitalWrite(BUZZERPIN, outputsOn ? HIGH : LOW);

    Serial.print("Relay/Buzzer: ");
    Serial.println(outputsOn ? "ON" : "OFF");

    lastToggleMs = now;
  }

  // Defensive: keep radio in RX
  if (lora_idle) {
    Radio.Rx(0);
  }

  delay(5);
}

// ================== Radio callbacks ==================
void OnTxDone(void) {
  lora_idle = true;
  Radio.Rx(0);
}

void OnTxTimeout(void) {
  lora_idle = true;
  Radio.Rx(0);
}

void OnRxTimeout(void) {
  lora_idle = true;
  Radio.Rx(0);
}

static void printHex(const uint8_t* buf, uint16_t len) {
  const char* hex = "0123456789ABCDEF";
  for (uint16_t i = 0; i < len; ++i) {
    uint8_t b = buf[i];
    char hi = hex[(b >> 4) & 0x0F];
    char lo = hex[b & 0x0F];
    Serial.print(hi); Serial.print(lo);
    if (i + 1 < len) Serial.print(' ');
  }
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  if (size > BUFFER_SIZE) size = BUFFER_SIZE;
  memcpy(rxBuf, payload, size);

  Serial.print("\nLoRa RX: size="); Serial.print(size);
  Serial.print(" RSSI=");          Serial.print(rssi);
  Serial.print(" dBm SNR=");       Serial.print(snr);
  Serial.println(" dB");

  Serial.print("  ASCII: ");
  for (uint16_t i = 0; i < size; ++i) {
    char c = (char)rxBuf[i];
    Serial.print((c >= 32 && c <= 126) ? c : '.');
  }
  Serial.println();

  Serial.print("  HEX:   ");
  printHex((uint8_t*)rxBuf, size);
  Serial.println();

  lora_idle = true;
  Radio.Rx(0);
}
