#include "LoRaWan_APP.h"
#include "Arduino.h"
#include "HT_TinyGPS++.h"
#include "mbedtls/aes.h"

// Each bike of the fleet is associated with an ID
#define BIKE_ID 1

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

// ========================== Lighting & Buzzer ==========================
#define LIGHTPIN 3              // Pin controlling the bike light
#define RELAYPIN 2              // Pin controlling relay (bike power lock)
#define BUZZERPIN 6             // Pin for buzzer
#define LIGHT_THRESHOLD 512     // Threshold for light level
#define MOVING_AVG_WINDOW 10    // Window size for moving average

int lightReadings[MOVING_AVG_WINDOW]; // Array to store light readings
int currentIndex = 0;                // Current index in the array
int sumReadings = 0;                 // Sum of readings for moving average

// ========================== Encryption Key ============================
const unsigned char aes_key[16] = {
  0x00, 0x01, 0x02, 0x03,
  0x04, 0x05, 0x06, 0x07,
  0x08, 0x09, 0x0a, 0x0b,
  0x0c, 0x0d, 0x0e, 0x0f
};

// ========================== Lock/Authorization State ===================

bool identified = false;  // true = UNLOCKED, false = LOCKED

// ========================== Forward Declarations =======================
void putGpsData2txpacket();
void aes_encrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len);
void aes_decrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len);
void sendLoRaData(void (*formatPacket)());
void receivedLoRaData(uint16_t packetSize);

// Radio event handlers ensure we return to continuous RX quickly
void OnTxDone(void);
void OnTxTimeout(void);
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr);
void OnRxTimeout(void);

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

// -------------------------- LoRa TX break for GPS --------------------
// [ADDED RX-ALWAYS] TX only when idle; callbacks return to continuous RX.
if (millis() - lastLoRaGpsTime > LoRa_GPS_Interval && lora_idle) {
  sendLoRaData(putGpsData2txpacket);
  lastLoRaGpsTime = millis();
}

  // -------------------------- Light/Relay/Buzzer logic -----------------
  // This section is unchanged functionally; lock state now comes from aut packets.
  // Moving average read
  sumReadings -= lightReadings[currentIndex];
  lightReadings[currentIndex] = analogRead(LIGHTPIN);
  sumReadings += lightReadings[currentIndex];
  currentIndex = (currentIndex + 1) % MOVING_AVG_WINDOW;
  int lightLevel = sumReadings / MOVING_AVG_WINDOW;

  if (identified) { // UNLOCKED
    if (lightLevel < LIGHT_THRESHOLD) {
      digitalWrite(RELAYPIN, HIGH);  // ON in low light
    } else {
      digitalWrite(RELAYPIN, LOW);   // OFF in bright light
    }
    digitalWrite(BUZZERPIN, LOW);
  } else { // LOCKED
    digitalWrite(RELAYPIN, LOW);     // No light when locked
    // Keep buzzer quiet unless you want anti-tamper here; leaving OFF.
    digitalWrite(BUZZERPIN, LOW);
  }

  // [ADDED RX-ALWAYS] Defensive: ensure we remain in RX when idle
  if (lora_idle) {
    Radio.Rx(0);
  }

  delay(20);
}

// ========================== Radio callbacks ============================
// [ADDED RX-ALWAYS] All callbacks return radio to continuous RX
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

  lora_idle = false;                 // [ADDED RX-ALWAYS] we briefly leave RX to TX
  Radio.Send((uint8_t*)txpacket, encryptedLength);
  // Radio will be put back into Rx(0) in OnTxDone/OnTxTimeout
}
// ========================== LoRa Packet Processing =====================
// Authorization protocol (no USER_ID):
//   aut,BIKE_ID,STATUS
// STATUS must be LOCKED or UNLOCKED (case-insensitive).
void receivedLoRaData(uint16_t packetSize) {
  if (packetSize == 0 || packetSize >= BUFFER_SIZE || (packetSize % 16) != 0) {
    Serial.println("RX: invalid size (must be >0, <BUFFER_SIZE, and 16-byte multiple)");
    return;
  }

  // Debug print encrypted
  Serial.print("RX (enc): ");
  for (uint16_t i = 0; i < packetSize; i++) {
    Serial.print((uint8_t)rxpacket[i], HEX);
    Serial.print(" ");
  }
  Serial.println();

  // Decrypt
  uint8_t decryptedPacket[BUFFER_SIZE];
  size_t decryptedLength = 0;
  aes_decrypt((const uint8_t *)rxpacket, packetSize, decryptedPacket, &decryptedLength);

  // Null-terminate for safe String use
  if (decryptedLength >= BUFFER_SIZE) decryptedLength = BUFFER_SIZE - 1;
  decryptedPacket[decryptedLength] = 0;

  Serial.print("RX (dec): ");
  for (size_t i = 0; i < decryptedLength; i++) Serial.print((char)decryptedPacket[i]);
  Serial.println();

  // Parse header/type
  String s = String((char*)decryptedPacket);
  s.trim();

  // Expect CSV: type,field2,field3 with no extras
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

  // Find second comma and ensure there is NO third comma
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

  // Validate bike id
  int bid = tBike.toInt();
  if (bid != BIKE_ID) {
    Serial.println("RX aut: different BIKE_ID -> ignored");
    return;
  }

  // Apply status
  if (tStatus == "UNLOCKED") {
    identified = true;
    Serial.println("Auth: UNLOCKED by station");
  } else if (tStatus == "LOCKED") {
    identified = false;
    Serial.println("Auth: LOCKED by station");
  } else {
    Serial.println("Auth: unknown STATUS token -> expected LOCKED or UNLOCKED");
  }

  // Always return to RX handled by callback tail
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
void aes_decrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len) {
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_dec(&aes, aes_key, 128);
    for (size_t i = 0; i < length; i += 16) {
        mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_DECRYPT, input + i, output + i);
    }
    uint8_t pad = output[length - 1];
    if (pad > 0 && pad <= 16) {
        *out_len = length - pad;
    } else {
        *out_len = length; // No padding found
    }
    mbedtls_aes_free(&aes);
}
// ========================== End of Functions Definitions ===============