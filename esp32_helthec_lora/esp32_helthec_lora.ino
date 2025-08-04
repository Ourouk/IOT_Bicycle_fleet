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
#define BUFFER_SIZE 256          // Packet buffer size

// Buffers for sending/receiving LoRa data
char txpacket[BUFFER_SIZE];
char rxpacket[BUFFER_SIZE];
double txNumber;                // Counter for transmissions
bool lora_idle = true;          // Indicates if LoRa is ready for a new transmission
static RadioEvents_t RadioEvents; // Radio event handler structure

// ========================== GPS Configuration ==========================
#define BAUD 9600               // Baud rate for GPS communication
#define RXPIN 33                 // GPS RX pin (data from GPS module)
#define TXPIN 34                 // GPS TX pin (data to GPS module)
TinyGPSPlus gps;                // GPS object from TinyGPS++ library
bool gpsDataAvailable = false;  // Flag to indicate GPS fix availability
// Debugs wrong wirings or faulty GPS modules
// If no GPS data received for more than 5 seconds, print debug message every 5
static unsigned long lastGpsDataTime = 0;
static unsigned long lastDebugTime = 0;
static unsigned long lastGpsFixTime = 0;
bool gpsDataReceived = false;
// ========================== RFID Configuration ==========================
#define RXPINRFID 1             // RFID RX pin
#define TXPINRFID 38            // RFID TX pin
String rfid;                    // Buffer to store RFID scan
String lastscannedrfid;         // Stores last scanned RFID tag
bool identified = false;        // True if a valid RFID is identified
bool firstScan = true;          // Ensures first RFID scan is always accepted

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

// Function declarations
void OnTxDone(void);
void OnTxTimeout(void);
void putGpsData2txpacket();
void isGpsDataAvailable();
int readSmoothedLightLevel();

void setup() {
  // Initialize debug serial monitor
  Serial.begin(115200);

  // Initialize GPS (Serial1) and RFID (Serial2) serial ports
  Serial1.begin(BAUD, SERIAL_8N1, RXPIN, TXPIN);
  Serial2.begin(BAUD, SERIAL_8N1, RXPINRFID, TXPINRFID);

  // ========================== LoRa Setup ==========================
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);         // Initialize Heltec MCU
  txNumber = 0;                                   // Reset transmission counter
  RadioEvents.TxDone = OnTxDone;                  // Callback on successful transmission
  RadioEvents.TxTimeout = OnTxTimeout;            // Callback on transmission timeout
  Radio.Init(&RadioEvents);                       // Initialize LoRa radio with events
  Radio.SetChannel(RF_FREQUENCY);                 // Set LoRa frequency
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000); // Transmission config

  // Print TinyGPS++ library version for debugging
  Serial.print(F("Debug TinyGPSPlus v."));
  Serial.println(TinyGPSPlus::libraryVersion());

  // Initialize I/O pins for relay, buzzer, and light
  pinMode(RELAYPIN, OUTPUT);
  pinMode(BUZZERPIN, OUTPUT);
  pinMode(LIGHTPIN, INPUT); // Set light pin as input

  // Initialize light readings array
  for (int i = 0; i < MOVING_AVG_WINDOW; i++) {
    lightReadings[i] = 0;
  }
  delay(1000); //Allow all components to initialize properly
}

void loop() {
  // ========================== LoRa Processing ==========================
  Radio.IrqProcess();  // Handle LoRa IRQ events (e.g., TX done, RX received)

  // ========================== GPS Data Processing ==========================
  gpsDataReceived = false; // Reset GPS data received flag

  if(millis() - lastGpsFixTime > 5000)
  {
    while (Serial1.available() > 0) // Check if GPS sent new data
    { 
      if(Serial1.peek()!='\n')
      {
        gps.encode(Serial1.read());
        gpsDataReceived = true; // Doesn't matter if GPS data is valid or not, we received data
        lastGpsDataTime = millis();
      }
      else
      {
        Serial1.read(); // Read and discard newline character
          if(gps.time.second()==0)
          {
            continue;
          }
        isGpsDataAvailable();  // Update GPS fix status
        lastGpsFixTime = millis();
        break;
      }
    }
  }

  // If no GPS data received for more than 20 seconds, print debug message every 20 seconds
  if (!gpsDataReceived && (millis() - lastGpsDataTime) > 20000 && (millis() - lastDebugTime) > 20000) {
      Serial.println("Warning: No GPS data received for over 20 seconds.");
      lastDebugTime = millis();
  }

  // ========================== RFID Processing ==========================
  while (Serial2.available()) {     // Check if RFID scanner sent data
    char c = Serial2.read();
    rfid += c;                      // Append character to RFID buffer
    if (rfid.length() >= 12) {      // Typical RFID tag length is 12 chars
      if (firstScan || rfid != lastscannedrfid) { // Accept if first scan or different tag
        identified = true;
        lastscannedrfid = rfid;      // Save last scanned RFID
        firstScan = false;
        Serial.println("RFID scanned: " + rfid);
        digitalWrite(BUZZERPIN, HIGH);  // Activate buzzer briefly
        delay(100);
        digitalWrite(BUZZERPIN, LOW);
      }
      rfid = ""; // Clear buffer for next scan
    }
    delay(50); // Small delay to avoid overwhelming the serial buffer and the processor
  }

  // ========================== LoRa GPS Transmission ==========================
  if (gpsDataAvailable && lora_idle) { // Send GPS data if fix acquired and LoRa is idle
    putGpsData2txpacket();             // Format GPS data into packet
    Serial.println("LoRa idle, sending GPS data...");
    Radio.Send((uint8_t *)txpacket, strlen(txpacket)); // Transmit packet
    lora_idle = false;                 // Mark LoRa as busy
    txNumber += 0.01;                  // Increment transmission count
  }

  // ========================== Lighting & Relay Control ==========================
  int lightLevel = readSmoothedLightLevel(); // Read smoothed light level

  if (identified) { // If RFID identified (bike unlocked)
    if (lightLevel < LIGHT_THRESHOLD) {
      digitalWrite(RELAYPIN, HIGH); // Power relay ON (Low Light)
    } else {
      digitalWrite(RELAYPIN, LOW); // Power Relay Off (Hight Light)
    }
  } else {
    digitalWrite(RELAYPIN, LOW); // Locked bike do not need light
    // Activate buzzer if not authenticated AND GPS in motion
    if (gpsDataAvailable && gps.speed.kmph() > 1.0) { // Speed threshold 1 km/h
      digitalWrite(BUZZERPIN, HIGH);
    } else {
      digitalWrite(BUZZERPIN, LOW);
    }
  }
}

// Function to read and smooth light level using moving average
int readSmoothedLightLevel() {
  sumReadings -= lightReadings[currentIndex]; // Subtract the oldest reading
  lightReadings[currentIndex] = analogRead(LIGHTPIN); // Read from the light sensor
  sumReadings += lightReadings[currentIndex]; // Add the new reading
  currentIndex = (currentIndex + 1) % MOVING_AVG_WINDOW; // Move to the next index

  return sumReadings / MOVING_AVG_WINDOW; // Return the average
}

// ========================== LoRa ================================
void aes_encrypt(const uint8_t *input, size_t length, uint8_t *output) {
  mbedtls_aes_context aes;
  mbedtls_aes_init(&aes);
  mbedtls_aes_setkey_enc(&aes, aes_key, 128);
  // Pad length to multiple of 16 bytes (PKCS7 padding)
  size_t padded_len = ((length + 15) / 16) * 16;
  uint8_t buf[padded_len];
  memcpy(buf, input, length);
  uint8_t pad = padded_len - length;
  for (size_t i = length; i < padded_len; i++) {
    buf[i] = pad;
  }
  // Encrypt each 16-byte block
  for (size_t i = 0; i < padded_len; i += 16) {
    mbedtls_aes_crypt_ecb(&aes, MBEDTLS_AES_ENCRYPT, buf + i, output + i);
  }
  mbedtls_aes_free(&aes);
}

void OnTxDone(void) {
  Serial.println("TX done...");
  lora_idle = true; // Mark LoRa as ready for next transmission
}

void OnTxTimeout(void) {
  Radio.Sleep(); // Put LoRa radio to sleep on timeout
  Serial.println("TX timeout...");
  lora_idle = true;
}

// ========================== GPS Utilities ==========================
void isGpsDataAvailable() {
  if (gps.location.isValid()) { // Check if GPS has a valid fix
    gpsDataAvailable = true;
    Serial.println("GPS: Fix acquired");
  } else {
    gpsDataAvailable = false; //Permit to decide if a localisation need to be sent
    Serial.print("GPS: ");
    Serial.print(gps.satellites.value());
    Serial.println(" satellite(s), no fix acquired");
  }
}

// Format GPS data into LoRa packet for transmission
void putGpsData2txpacket() {
  if (gps.location.isValid()) { // If GPS fix valid, include coordinates
    sprintf(txpacket, "GPS,%d,%.6f,%.6f,%d,%02d/%02d/%02d,%02d:%02d:%02d",
            BIKE_ID,
            gps.location.lat(),
            gps.location.lng(),
            gps.satellites.value(),
            gps.date.month(), gps.date.day(), gps.date.year(),
            gps.time.hour(), gps.time.minute(), gps.time.second());
  } else { // If no GPS fix, send placeholders
    sprintf(txpacket, "GPS,%d,N/A,N/A,N/A,N/A,N/A,N/A", BIKE_ID);
  }
}
