#include "LoRaWan_APP.h"
#include "Arduino.h"
#include "HT_TinyGPS++.h"
#include "mbedtls/aes.h"

// Each bike of the fleet is associated with an ID
#define BIKE_ID 1
#define DEBUG_SENSORS 1
// ========================== Debug (precompiler-controlled) ===================
// Set to 1 to enable periodic compact sensor/state debug line; 0 to compile out
#define DEBUG_SENSORS 1
#define DEBUG_SENSORS_INTERVAL_MS 5000UL  // print every 5 seconds

// ========================== LoRa Configuration ==========================
#define RF_FREQUENCY 868100000   // LoRa frequency in Hz (EU868 band)
#define TX_OUTPUT_POWER 5        // Transmission power in dBm
#define LORA_BANDWIDTH 0         // Bandwidth: 0 = 125 kHz
#define LORA_SPREADING_FACTOR 7  // Spreading factor (SF7-SF12, lower = faster but less range)
#define LORA_CODINGRATE 1        // Coding rate: 1 = 4/5 (error correction level)
#define LORA_PREAMBLE_LENGTH 8   // Preamble length
#define LORA_SYMBOL_TIMEOUT 0    // Symbol timeout (0 = no timeout)
#define LORA_FIX_LENGTH_PAYLOAD_ON false // Variable-length payload
#define LORA_IQ_INVERSION_ON false        // No IQ inversion
#define RX_TIMEOUT_VALUE 1000    // Timeout for RX in ms
#define RX_REPEAT_NUMBER 5       // Number of RX repeats
#define BUFFER_SIZE 256          // Packet buffer size

// Buffers for sending/receiving LoRa data
char txpacket[BUFFER_SIZE];
char rxpacket[BUFFER_SIZE];
bool lora_idle = true;                 // Indicates if LoRa is ready for a new transmission
static RadioEvents_t RadioEvents;      // Radio event handler structure

// ========================== LoRa Message Configuration =================
#define LoRa_GPS_Interval 60000 // Interval to send GPS data in ms
#define LoRa_Lock_Status_EventDriven true // Send lock status only when changed
#define LoRa_Lock_Status_Interval 300000  // Interval to send lock status in ms ONLY USED IF LoRa_Lock_Status_EventDriven IS FALSE
// Associated var
unsigned long lastLoRaGpsTime = 0;           // Last time GPS data was sent via LoRa
unsigned long lastLoRaLockStatusTime = 0;    // Last time lock status was sent

// ========================== GPS Configuration ==========================
#define BAUD 9600                // Baud rate for GPS communication
#define RXPIN 33                 // GPS RX pin (data from GPS module)
#define TXPIN 34                 // GPS TX pin (data to GPS module)
#define GPS_CHECK_INTERVAL 5000  // Interval to check GPS data in ms
TinyGPSPlus gps;                // GPS object from TinyGPS++ library
bool gpsDataAvailable = false;  // Flag to indicate GPS fix availability
bool gpsDataReceived = false;   // Flag to indicate if GPS data was received
static unsigned long lastGpsDataTime = 0; // Last time GPS data was received
static unsigned long lastGpsLineTime = 0;  // Last time had a GPS full line
// Debugs wrong wirings or faulty GPS modules
// If no GPS data received for more than 5 seconds, print debug message every 5
static unsigned long lastDebugTime = 0; // Last time debug message was printed



// ========================== Encryption Key ============================
const unsigned char aes_key[16] = {
  0x00, 0x01, 0x02, 0x03,
  0x04, 0x05, 0x06, 0x07,
  0x08, 0x09, 0x0a, 0x0b,
  0x0c, 0x0d, 0x0e, 0x0f
};

// ========================== Lock/Authorization State ===================

bool identified = true;  // true = UNLOCKED, false = LOCKED

// ========================== Forward Declarations =======================
// Formater
void putGpsData2txpacket();
// LoRa
void sendLoRaData(void (*formatPacket)());
void receivedLoRaData(uint16_t packetSize);
// Radio event handlers ensure we return to continuous RX quickly
void OnTxDone(void);
void OnTxTimeout(void);
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr);
void OnRxTimeout(void);
// AES Helper
void aes_encrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len);
void aes_decrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len);
// =============================================================================
// Other third party functions
// =============================================================================
  // ========================== Lighting  ==========================
  #define LIGHTPIN 3              // Pin Light Sensor
  #define RELAYPIN 2              // Pin controlling relay (bike power lock)
  #define LIGHT_THRESHOLD 512     // Threshold for light level
  #define MOVING_AVG_WINDOW 10    // Window size for moving average

  int lightReadings[MOVING_AVG_WINDOW]; // Array to store light readings
  int currentIndex = 0;                // Current index in the array
  int sumReadings = 0;                 // Sum of readings for moving average
  //Auto Light System
  void light_ifDark(); //Note the bike light is managed by the relay

// ============================= Anti-Stole System ===================
  // --- Tunables ---
  #define BUZZERPIN 5             // Pin for buzzer
  #define MOTION_SPEED_KMPH        2.0f            // above this = moving
  #define MOTION_MAX_AGE_MS        ((uint32_t)1500) // GPS speed must be newer than this
  #define MOTION_HOLD_MS           ((uint32_t)2000) // tolerate brief GPS dropouts
  #define MOTION_REQUIRED_MS       ((uint32_t)(3UL * 60UL * 1000UL)) // require 3 min of motion
  #define MOTION_PAUSE_GRACE_MS    ((uint32_t)10000) // allow brief stops without reset (10 s)

  // --- State ---
  static uint32_t s_lastAboveMs     = 0;   // last time speed was above threshold
  static bool     s_moving          = false;
  static uint32_t s_motionStartMs   = 0;   // when sustained-motion window started
  static bool     s_buzzerActive    = false;
  
  static inline bool isMovingNow();
  void bip_ifStolen();

#if DEBUG_SENSORS
static unsigned long s_lastSensorsDebugMs = 0;
static void debug_printSensors();
// Return the latest moving-average light level (integer)
static inline int currentLightLevel() {
  return (MOVING_AVG_WINDOW > 0) ? (sumReadings / MOVING_AVG_WINDOW) : 0;
}
static void debug_printSensors() {
  // Gather states with zero side-effects
  const bool lock_unlocked = identified;
  const int  lightLevel    = currentLightLevel();
  const bool relayOn       = digitalRead(RELAYPIN) == HIGH;
  const bool buzzerOn      = digitalRead(BUZZERPIN) == HIGH;

  // GPS state
  const bool gpsValid      = gps.location.isValid();
  const uint32_t gpsAge    = gps.location.age(); // ms since last fix
  const double lat         = gpsValid ? gps.location.lat() : 0.0;
  const double lng         = gpsValid ? gps.location.lng() : 0.0;
  const bool spdValid      = gps.speed.isValid();
  const float spdKmph      = spdValid ? gps.speed.kmph() : 0.0f;
  const uint32_t spdAge    = gps.speed.age(); // ms
  const uint32_t sats      = gps.satellites.isValid() ? gps.satellites.value() : 0;

  // Motion/anti-theft internals (read-only)
  const bool moving        = s_moving;
  const uint32_t nowMs     = millis();
  const uint32_t sinceAbove= (uint32_t)(nowMs - s_lastAboveMs);
  const uint32_t motionRun = (s_motionStartMs ? (uint32_t)(nowMs - s_motionStartMs) : 0);
  const bool loraIdle      = lora_idle;

  // Single compact line (CSV-like key=val)
  Serial.print("DBG:");
  Serial.print(" lock=");        Serial.print(lock_unlocked ? "UNLOCKED" : "LOCKED");
  Serial.print(" light=");       Serial.print(lightLevel);
  Serial.print(" thr=");         Serial.print(LIGHT_THRESHOLD);
  Serial.print(" relay=");       Serial.print(relayOn ? 1 : 0);
  Serial.print(" buzzer=");      Serial.print(buzzerOn ? 1 : 0);

  Serial.print(" gpsFix=");      Serial.print(gpsValid ? 1 : 0);
  Serial.print(" gpsAgeMs=");    Serial.print(gpsAge);
  Serial.print(" lat=");         if (gpsValid) Serial.print(lat, 6); else Serial.print("N/A");
  Serial.print(" lng=");         if (gpsValid) Serial.print(lng, 6); else Serial.print("N/A");
  Serial.print(" spdOk=");       Serial.print(spdValid ? 1 : 0);
  Serial.print(" spdKmph=");     Serial.print(spdKmph, 2);
  Serial.print(" spdAgeMs=");    Serial.print(spdAge);
  Serial.print(" sats=");        Serial.print(sats);

  Serial.print(" moving=");      Serial.print(moving ? 1 : 0);
  Serial.print(" sinceAboveMs=");Serial.print(sinceAbove);
  Serial.print(" motionRunMs="); Serial.print(motionRun);

  Serial.print(" loraIdle=");    Serial.print(loraIdle ? 1 : 0);

  Serial.println();
}
#endif


// ========================== Setup ======================================
void setup() {
  Serial.begin(115200);

  // GPS serial setup
  Serial1.begin(BAUD, SERIAL_8N1, RXPIN, TXPIN);

  // LoRa setup (unchanged parameters)
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);
  RadioEvents.TxDone    = OnTxDone;
  RadioEvents.TxTimeout = OnTxTimeout;
  RadioEvents.RxDone    = OnRxDone;
  RadioEvents.RxTimeout = OnRxTimeout;
  Radio.Init(&RadioEvents);
  // Apply LoRa Settings
  Radio.SetChannel(RF_FREQUENCY);
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000);
  Radio.SetRxConfig(MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                    LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                    LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                    0, true, 0, 0, LORA_IQ_INVERSION_ON, true);

  // Start Radio in perpetual receive mode
  lora_idle = true;
  Radio.Rx(0);

  // IO setup
  pinMode(RELAYPIN, OUTPUT);
  pinMode(BUZZERPIN, OUTPUT);
  //Make sure these pin are off by default
  digitalWrite(RELAYPIN, LOW);
  digitalWrite(BUZZERPIN, LOW);


  pinMode(LIGHTPIN, INPUT);
  for (int i = 0; i < MOVING_AVG_WINDOW; i++) lightReadings[i] = 0;

  delay(200);
}

// ========================== Loop =======================================
void loop() {
  // Process radio IRQs
  Radio.IrqProcess();
  // -------------------------- GPS handling  ----------------------------
  gpsDataReceived = false;
  if (millis() - lastGpsLineTime > GPS_CHECK_INTERVAL) {
    while (Serial1.available() > 0) {
      if (Serial1.peek() != '\n') {
        gps.encode(Serial1.read());
        gpsDataReceived = true;
        lastGpsDataTime = millis();
      } else {
        Serial1.read(); // consume '\n'
        if (gps.time.second() == 0) {
          continue;
        }
        // Update GPS availability flag
        if (gps.location.isValid()) {
          gpsDataAvailable = true;
        } else {
          gpsDataAvailable = false;
        }
        lastGpsLineTime = millis();
        break;
      }
    }
    if (!gpsDataReceived && (millis() - lastGpsDataTime) > 20000 && (millis() - lastDebugTime) > 20000) {
      Serial.println("Warning: No GPS data received for over 20 seconds.");
      lastDebugTime = millis();
    }
  }
  //Other utilities
  if(identified)
  {
    light_ifDark();
  }else
  {
    bip_ifStolen();
  }
  // -------------------------- LoRa TX break for GPS --------------------
  // TX only when idle; callbacks return to continuous RX.
  if (millis() - lastLoRaGpsTime > LoRa_GPS_Interval && lora_idle) {
    sendLoRaData(putGpsData2txpacket);
    lastLoRaGpsTime = millis();
  }
    // Defensive: ensure we remain in RX when idle
    if (lora_idle) {
      Radio.Rx(0);
    }
    #if DEBUG_SENSORS
      // Periodic consolidated debug line
      if (millis() - s_lastSensorsDebugMs >= DEBUG_SENSORS_INTERVAL_MS) {
        debug_printSensors();
        s_lastSensorsDebugMs = millis();
      }
    #endif
    delay(20);
}

// ========================== Radio callbacks ============================
// All callbacks return radio to continuous RX
void OnTxDone(void) {
  Serial.println("TX done");
  lora_idle = true;
  Radio.Rx(0);
}

void OnTxTimeout(void) {
  Serial.println("TX timeout");
  lora_idle = true;
  Radio.Rx(0);
}

void OnRxTimeout(void) {
  // Keep listening; timeouts are expected on idle channels
  lora_idle = true;
  Radio.Rx(0);
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  // Copy encrypted payload and process
  if (size > BUFFER_SIZE) size = BUFFER_SIZE;
  memcpy(rxpacket, payload, size);
  receivedLoRaData(size);
  lora_idle = true;
  Radio.Rx(0);
}
void sendLoRaData(void (*formatPacket)()) {
  if (!formatPacket) {
    Serial.println("sendLoRaData: null formatter");
    return;
  }

  // Only break RX when we're idle
  if (!lora_idle) {
    Serial.println("sendLoRaData: radio busy, skipping TX");
    return;
  }

  // 1) Let caller format the plaintext into txpacket
  formatPacket();

  // 2) Encrypt in-place into txpacket buffer (output -> txpacket)
  size_t encryptedLength = 0;
  const size_t plainLen = strlen(txpacket);
  if (plainLen == 0) {
    Serial.println("sendLoRaData: empty payload");
    return;
  }
  aes_encrypt((const uint8_t*)txpacket, plainLen, (uint8_t*)txpacket, &encryptedLength);

  // 3) Transmit
  Serial.print("TX (encrypted, len=");
  Serial.print(encryptedLength);
  Serial.println(")");

  lora_idle = false;                 // We briefly leave RX to TX
  Radio.Send((uint8_t*)txpacket, encryptedLength);
  // Radio will be put back into Rx(0) in OnTxDone/OnTxTimeout
}
// ========================== LoRa Packet Processing =====================
// Authorization protocol (no USER_ID):
//   aut,BIKE_ID,STATUS
// STATUS must be LOCKED or UNLOCKED (case-insensitive).
void receivedLoRaData(uint16_t packetSize) {
  // Basic bounds check against our ASCII-hex buffer capacity
  if (packetSize == 0 || packetSize >= (BUFFER_SIZE * 2)) {
    Serial.println("RX: invalid size (must be >0 and below capacity)");
    return;
  }

  // Print the incoming ASCII-hex as-is (skip CR/LF for readability)
  Serial.print("RX (enc ASCII): ");
  for (uint16_t i = 0; i < packetSize; i++) {
    char c = (char)rxpacket[i];
    if (c == '\r' || c == '\n') continue;
    Serial.write(c);
  }
  Serial.println();

  // 1) ASCII-hex -> binary ciphertext
  uint8_t ct[BUFFER_SIZE];
  size_t ctLen = 0;
  if (!hex2bin((const char*)rxpacket, packetSize, ct, sizeof(ct), &ctLen)) {
    Serial.println("RX: not valid ASCII hex");
    return;
  }
  if ((ctLen % 16) != 0) {
    Serial.println("RX: ECB ciphertext must be multiple of 16 bytes");
    return;
  }

  // 2) AES-ECB decrypt (+ PKCS#7)
  uint8_t pt[BUFFER_SIZE];
  size_t ptLen = 0;
  const bool expect_pkcs7 = true;   // set false if sender disabled padding
  if (!aes_ecb_decrypt(ct, ctLen, aes_key, 128, pt, sizeof(pt), &ptLen, expect_pkcs7)) {
    Serial.println("RX: decrypt/padding failed (wrong key/mode/padding?)");
    return;
  }

  // 3) Null-terminate for safe String/printing
  if (ptLen >= sizeof(pt)) ptLen = sizeof(pt) - 1;
  pt[ptLen] = 0;

  Serial.print("RX (dec): ");
  Serial.write(pt, ptLen);
  Serial.println();

  // 4) Parse CSV: "aut,BIKE_ID,STATUS"
  String s = String((char*)pt);
  s.trim();

  int c1 = s.indexOf(',');
  if (c1 < 0) { Serial.println("RX: malformed packet (no commas)"); return; }
  String type = s.substring(0, c1);

  if (type != "aut") {
    if (type == "loc") {
      Serial.println("RX: 'loc' packet received (ignored on bike side)");
    } else {
      Serial.println("RX: unknown packet type");
    }
    return;
  }

  int c2 = s.indexOf(',', c1 + 1);
  if (c2 < 0) { Serial.println("RX aut: malformed (missing BIKE_ID or STATUS)"); return; }
  int c3 = s.indexOf(',', c2 + 1);
  if (c3 >= 0) {
    Serial.println("RX aut: rejected (protocol now 'aut,BIKE_ID,STATUS' — extra fields present)");
    return;
  }

  String tBike = s.substring(c1 + 1, c2);
  String tStatus = s.substring(c2 + 1);
  tBike.trim(); tStatus.trim(); tStatus.toUpperCase();

  int bid = tBike.toInt();
  if (bid != BIKE_ID) {
    Serial.println("RX aut: different BIKE_ID -> ignored");
    return;
  }

int status = tStatus.toInt();
  if (status == 1) {
    identified = true;
    Serial.println("Auth: 1 by station");
  } else if (status == 0) {
    identified = false;
    Serial.println("Auth: 0 by station");
  } else {
    Serial.println("Auth: unknown STATUS token -> expected LOCKED or UNLOCKED");
  }
}



// ========================== LoRa Packet Formatting =====================
// Format GPS data into LoRa packet for transmission (unchanged, still "loc")
void putGpsData2txpacket() {
  if (gps.location.isValid()) { // If GPS fix valid, include coordinates
    sprintf(txpacket, "loc,%d,%.6f,%.6f,%d,%02d/%02d/%04d,%02d:%02d:%02d",
            BIKE_ID,
            gps.location.lat(),
            gps.location.lng(),
            gps.satellites.value(),
            gps.date.month(), gps.date.day(), gps.date.year(),
            gps.time.hour(), gps.time.minute(), gps.time.second());
  } else { // If no GPS fix, send placeholders
    sprintf(txpacket, "loc,%d,N/A,N/A,N/A,N/A,N/A,N/A", BIKE_ID);
    Serial.println("TX (GPS): no fix");
  }
}

// ========================== AES Helpers  ==============================
// Encryption function using AES
// Uses mbedTLS library for AES encryption
// Pads input to multiple of 16 bytes using PKCS7 padding
void aes_encrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len) {
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_enc(&aes, aes_key, 128);
    size_t padded_len = ((length + 15) / 16) * 16;
    if (padded_len > BUFFER_SIZE) padded_len = BUFFER_SIZE; // safety
    uint8_t buf[BUFFER_SIZE];
    memcpy(buf, input, length);
    uint8_t pad = padded_len - length;
    for (size_t i = length; i < padded_len; i++)
      buf[i] = pad;
    for (size_t i = 0; i < padded_len; i += 16) {
      mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, buf + i, output + i);
    }
    *out_len = padded_len;  // Return encrypted length
    mbedtls_aes_free(&aes);
}

// Decrypt function using AES
// Uses mbedTLS library for AES decryption
// Assumes input is padded with PKCS7 padding
// Removes padding after decryption
bool hex2bin(const char *hex, size_t hex_len, uint8_t *out, size_t out_cap, size_t *out_len) {
    auto nib = [](int c)->int{
        if (c>='0'&&c<='9') return c-'0';
        if (c>='a'&&c<='f') return c-'a'+10;
        if (c>='A'&&c<='F') return c-'A'+10;
        return -1;
    };
    size_t j = 0, o = 0; int hi = -1;
    for (size_t i = 0; i < hex_len; i++) {
        int c = hex[i];
        if (c==' '||c=='\r'||c=='\n'||c=='\t') continue;
        int v = nib(c); if (v < 0) return false;
        if ((j++ & 1) == 0) hi = v;
        else {
            if (o >= out_cap) return false;
            out[o++] = (uint8_t)((hi << 4) | v);
        }
    }
    if ((j & 1) != 0) return false;
    *out_len = o; return true;
}
bool aes_ecb_decrypt(
    const uint8_t *input, size_t in_len,
    const uint8_t *aes_key, size_t key_bits,   // 128, 192, or 256
    uint8_t *output, size_t out_cap, size_t *out_len,
    bool expect_pkcs7)
{
    if (!input || !aes_key || !output || !out_len) return false;
    *out_len = 0;

    // ECB requires full blocks
    if (in_len == 0 || (in_len % 16) != 0) return false;
    if (out_cap < in_len) return false;

    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    int rc = mbedtls_aes_setkey_dec(&aes, aes_key, (unsigned int)key_bits);
    if (rc != 0) { mbedtls_aes_free(&aes); return false; }

    // Decrypt block-by-block; mbedtls_aes_crypt_ecb processes one 16B block
    for (size_t i = 0; i < in_len; i += 16) {
        rc = mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_DECRYPT, input + i, output + i);
        if (rc != 0) { mbedtls_aes_free(&aes); return false; }
    }

    size_t plain_len = in_len;

    if (expect_pkcs7) {
        uint8_t pad = output[in_len - 1];
        // PKCS#7: pad must be 1..16 and not exceed total length
        if (pad == 0 || pad > 16 || pad > in_len) {
            mbedtls_aes_free(&aes);
            return false;
        }
        // Verify all padding bytes
        for (size_t i = 0; i < pad; i++) {
            if (output[in_len - 1 - i] != pad) {
                mbedtls_aes_free(&aes);
                return false;
            }
        }
        plain_len = in_len - pad;
    }

    *out_len = plain_len;
    mbedtls_aes_free(&aes);
    return true;
}
// === Activate bike light system if dark enough
void light_ifDark() {
  // -------------------------- Light/Relay/Buzzer logic -----------------
  // Moving average read (non-blocking)
  sumReadings -= lightReadings[currentIndex];
  lightReadings[currentIndex] = analogRead(LIGHTPIN);
  sumReadings += lightReadings[currentIndex];
  currentIndex = (currentIndex + 1) % MOVING_AVG_WINDOW;

  const int lightLevel = sumReadings / MOVING_AVG_WINDOW;

  if (identified) { // UNLOCKED: light system active
    digitalWrite(RELAYPIN, (lightLevel < LIGHT_THRESHOLD) ? HIGH : LOW);
  } else {          // LOCKED: force OFF
    digitalWrite(RELAYPIN, LOW);
  }
}

// === Anti-Stole Sytem ===
static inline bool isMovingNow() {
  if (gps.speed.isValid() && gps.speed.age() <= MOTION_MAX_AGE_MS) {
    if (gps.speed.kmph() > MOTION_SPEED_KMPH) {
      s_lastAboveMs = millis();
      s_moving = true;
    } else {
      s_moving = false;
    }
  } else {
    // If data is stale, keep "moving" briefly (rollover-safe)
    s_moving = (uint32_t)(millis() - s_lastAboveMs) < MOTION_HOLD_MS;
  }
  return s_moving;
}

void bip_ifStolen() {
  const uint32_t now = millis();
  const bool moving = isMovingNow();

  // Track sustained motion
  if (moving) {
    if (s_motionStartMs == 0) {
      s_motionStartMs = now;  // start timing
    }
    // Activate once motion has lasted long enough
    if ((uint32_t)(now - s_motionStartMs) >= MOTION_REQUIRED_MS) {
      s_buzzerActive = true;
    }
  } else {
    // Not moving: allow a short grace before resetting the timer/activation
    const bool withinGrace = (uint32_t)(now - s_lastAboveMs) < MOTION_PAUSE_GRACE_MS;
    if (!withinGrace) {
      s_motionStartMs = 0;    // reset the sustained-motion timer
      s_buzzerActive  = false; // deactivate after a real stop
    }
    // If withinGrace, keep timing/activation as-is
  }

  // Drive the buzzer
  digitalWrite(BUZZERPIN, s_buzzerActive ? HIGH : LOW);
}
// ========================== End of Functions Definitions ===============