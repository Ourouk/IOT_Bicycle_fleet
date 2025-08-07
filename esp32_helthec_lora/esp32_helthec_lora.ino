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
#define RX_REPEAT_NUMBER 5    // Number of RX repeats
#define BUFFER_SIZE 256          // Packet buffer size

// Buffers for sending/receiving LoRa data
char txpacket[BUFFER_SIZE];
char rxpacket[BUFFER_SIZE];
bool lora_idle = true;          // Indicates if LoRa is ready for a new transmission
static RadioEvents_t RadioEvents; // Radio event handler structure

// ========================== LoRa Message Configuration =================
#define LoRa_GPS_Interval 60000 // Interval to send GPS data in ms
#define LoRa_Lock_Status_EventDriven true // Send lock status only when changed
#define LoRa_Lock_Status_Interval 300000 // Interval to send lock status in ms ONLY USED IF LoRa_Lock_Status_EventDriven IS FALSE
// Associated var
unsigned long lastLoRaGpsTime = 0; // Last time GPS data was sent via LoRa
unsigned long lastLoRaLockStatusTime = 0; // Last time lock status was sent

// ========================== GPS Configuration ==========================
#define BAUD 9600               // Baud rate for GPS communication
#define RXPIN 33                 // GPS RX pin (data from GPS module)
#define TXPIN 34                 // GPS TX pin (data to GPS module)
#define GPS_CHECK_INTERVAL 5000 // Interval to check GPS data in ms
TinyGPSPlus gps;                // GPS object from TinyGPS++ library
bool gpsDataAvailable = false;  // Flag to indicate GPS fix availability
bool gpsDataReceived = false;   // Flag to indicate if GPS data was received
static unsigned long lastGpsDataTime = 0; // Last time GPS data was received
static unsigned long lastGpsLineTime = 0;  // Last time had a GPS full line
// Debugs wrong wirings or faulty GPS modules
// If no GPS data received for more than 5 seconds, print debug message every 5
static unsigned long lastDebugTime = 0; // Last time debug message was printed

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
void OnTxDone(void); // Callback on successful transmission
void OnTxTimeout(void); // Callback on transmission timeout
void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr); // Callback on received data
void OnRxTimeout(void); // Callback on reception timeout
// Function to manage LoRa data
void sendLoRaData(void *);
void receivedLoRaData();
// Function to format data from/for LoRa packet
void putGpsData2txpacket();
void isGpsDataAvailable();
int readSmoothedLightLevel();
void receivedLoRaData();
bool gotValidResponse;
// Crypto
void aes_encrypt(const uint8_t*, size_t, uint8_t*, size_t*);
void aes_decrypt(const uint8_t*, size_t, uint8_t*, size_t*);



void setup() {
  // Initialize debug serial monitor
  Serial.begin(115200);

  // Initialize GPS (Serial1) and RFID (Serial2) serial ports
  Serial1.begin(BAUD, SERIAL_8N1, RXPIN, TXPIN);
  Serial2.begin(BAUD, SERIAL_8N1, RXPINRFID, TXPINRFID);

  // ========================== LoRa Setup ==========================
  Mcu.begin(HELTEC_BOARD, SLOW_CLK_TPYE);         // Initialize Heltec MCU on slow clock for power saving
  RadioEvents.TxDone = OnTxDone;                  // Callback on successful transmission
  RadioEvents.TxTimeout = OnTxTimeout;            // Callback on transmission timeout
  RadioEvents.RxDone = OnRxDone;                  // Callback on received data
  Radio.Init(&RadioEvents);                       // Initialize LoRa radio with events
  Radio.SetChannel(RF_FREQUENCY);                 // Set LoRa frequency
  Radio.SetTxConfig(MODEM_LORA, TX_OUTPUT_POWER, 0, LORA_BANDWIDTH,
                    LORA_SPREADING_FACTOR, LORA_CODINGRATE,
                    LORA_PREAMBLE_LENGTH, LORA_FIX_LENGTH_PAYLOAD_ON,
                    true, 0, 0, LORA_IQ_INVERSION_ON, 3000); // Transmission config
  Radio.SetRxConfig( MODEM_LORA, LORA_BANDWIDTH, LORA_SPREADING_FACTOR,
                              LORA_CODINGRATE, 0, LORA_PREAMBLE_LENGTH,
                              LORA_SYMBOL_TIMEOUT, LORA_FIX_LENGTH_PAYLOAD_ON,
                              0, true, 0, 0, LORA_IQ_INVERSION_ON, true );

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



// ========================== Entering Main Loop ============================
void loop() {
  // ========================== LoRa Processing =============================
  Radio.IrqProcess();  // Handle LoRa IRQ events (e.g., TX done, RX received)

  // ========================== GPS Data Processing ==========================
  gpsDataReceived = false; // Reset GPS data received flag
  // GPS Serial data reading
  if(millis() - lastGpsLineTime > GPS_CHECK_INTERVAL)
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
        lastGpsLineTime = millis();
        break;
      }
    }
    // If GPS Serial received no data for more than 20 seconds.
    if (!gpsDataReceived && (millis() - lastGpsDataTime) > 20000 && (millis() - lastDebugTime) > 20000) {
        Serial.println("Warning: No GPS data received for over 20 seconds.");
        lastDebugTime = millis();
    }
    // LoRa GPS data sending
    if( millis() - lastLoRaGpsTime > LoRa_GPS_Interval && gpsDataAvailable && lora_idle) {
      sendLoRaData(putGpsData2txpacket); // Send GPS data via LoRa
      lastLoRaGpsTime = millis(); // Update last GPS transmission time
    }
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
        //Handle having a respond from the server
        sendLoRaData(putLockStatus2txpacket); // Send lock status via LoRa Event-Driven
        // Wait for the response from the server
        int response_timeout = 0;
        while (!lora_idle && !gotValidResponse && response_timeout > RX_REPEAT_NUMBER  ) {
          Radio.Rx(RX_TIMEOUT_VALUE);
        }
        digitalWrite(BUZZERPIN, LOW);
      }
      rfid = ""; // Clear buffer for next scan
    }
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
  delay(50); // Small delay to avoid overwhelming the serial buffer and the processor
}
// ========================== Quitting Main Loop ==========================





// ========================= Entering Functions Definitions ===============
// Function to read and smooth light level using moving average
int readSmoothedLightLevel() {
  sumReadings -= lightReadings[currentIndex]; // Subtract the oldest reading
  lightReadings[currentIndex] = analogRead(LIGHTPIN); // Read from the light sensor
  sumReadings += lightReadings[currentIndex]; // Add the new reading
  currentIndex = (currentIndex + 1) % MOVING_AVG_WINDOW; // Move to the next index
  return sumReadings / MOVING_AVG_WINDOW; // Return the average
}


// ========================== GPS Utilities ==========================
void isGpsDataAvailable() {
  if (gps.location.isValid()) { // Check if GPS has a valid fix
    gpsDataAvailable = true;
    Serial.println("GPS: Fix acquired");
  } else {
    gpsDataAvailable = false; //Permit to decide if a localization need to be sent
    Serial.print("GPS: ");
    Serial.print(gps.satellites.value());
    Serial.println(" satellite(s), no fix acquired");
  }
}
// ========================== Lora Event Handlers ===========================
void OnTxDone(void) {
  Serial.println("TX done...");
  lora_idle = true; // Mark LoRa as ready for next transmission
}

void OnTxTimeout(void) {
  Radio.Sleep(); // Put LoRa radio to sleep on timeout
  Serial.println("TX timeout...");
  lora_idle = true;
}

void OnRxDone(uint8_t *payload, uint16_t size, int16_t rssi, int8_t snr) {
  memcpy(rxpacket, payload, size); // Copy received payload to rxpacket
  receivedLoRaData(size);
  lora_idle = true; // Mark LoRa as ready for next transmission
}

// ========================== Lora Sending/Receiving Functions ==========================
// Function to send data via LoRa (Use as parameter the function to call to format the packet)
void sendLoRaData(void (*formatPacket)()) {
  if (lora_idle) { // Check if LoRa is ready for transmission
    formatPacket(); // Call the provided function to format the packet
    Serial.print("Sending packet: ");
    Serial.println(txpacket); // Print the packet to be sent
    size_t encryptedLength = 0;
    aes_encrypt((const uint8_t *)txpacket, strlen(txpacket), (uint8_t *)txpacket, &encryptedLength); // Encrypt the packet
    Serial.print("Encrypted packet: ");
    for (size_t i = 0; i < encryptedLength; i++) {
      Serial.print(txpacket[i], HEX);
      Serial.print(" ");
    }
    Serial.println();
    lora_idle = false; // Mark LoRa as busy
    Radio.Send((uint8_t *)txpacket, encryptedLength); // Send the packet
  } else {
    Serial.println("LoRa is busy, cannot send data.");
  }
}
// Function to receive data via LoRa
void receivedLoRaData(uint16_t packetSize) {
  // Check if a packet size is a multiple of 16 bytes to discard invalid packets
  //TODO : Reduce the debugging output of this function could stall to many CPU cycles in between loRa packets
  if (packetSize > 0 && packetSize < BUFFER_SIZE && packetSize % 16 == 0)
  {
    Serial.print("Received packet: ");
    Serial.println(rxpacket); // Print the crypted packet
    // Decrypt the received packet using AES
    uint8_t decryptedPacket[BUFFER_SIZE];
    size_t decryptedLength = 0;
    aes_decrypt((const uint8_t *)rxpacket, packetSize, decryptedPacket, &decryptedLength); // Decrypt the packet
    Serial.print("Decrypted packet: ");
    for (size_t i = 0; i < decryptedLength; i++) {
      Serial.print(decryptedPacket[i], HEX);
      Serial.print(" ");
    }
    Serial.println();
    // Process the received decrypted packet
    // Isolate the first part of the packet to determine its type
    // TODO: Call handler functions
    String packetType = String((char *)decryptedPacket).substring(0, 3);
    if (packetType == "aut") {
      Serial.println("Received authentication packet.");
      gotValidResponse = true;
      // Extract bike ID and user ID from the packet
      
    } else if (packetType == "loc") {
      Serial.println("Received GPS data packet.");
      gotValidResponse = false;
    } else {
      Serial.println("Received unknown packet type.");
      gotValidResponse = false;
    }
  } else {
    // If packet size is invalid, discard it
    Serial.println("Received packet size is invalid. So it was discarded.");
    // Change global variable to signal that the response wasn't valid
    gotValidResponse = false;
  }
}
// ========================== LoRa Packet Formatting ==========================
// Format GPS data into LoRa packet for transmission
void putGpsData2txpacket() {
  if (gps.location.isValid()) { // If GPS fix valid, include coordinates
    sprintf(txpacket, "loc,%d,%.6f,%.6f,%d,%02d/%02d/%02d,%02d:%02d:%02d",
            BIKE_ID,
            gps.location.lat(),
            gps.location.lng(),
            gps.satellites.value(),
            gps.date.month(), gps.date.day(), gps.date.year(),
            gps.time.hour(), gps.time.minute(), gps.time.second());
  } else { // If no GPS fix, send placeholders
    sprintf(txpacket, "loc,%d,N/A,N/A,N/A,N/A,N/A,N/A", BIKE_ID);
  }
}

void putLockStatus2txpacket() {
  // Decode user ID from RFID tag
  //Using Wiegand format, first 8 chars are user ID
  String user_id = lastscannedrfid.substring(0, 8);
  if (user_id.length() < 8) {
    // If user ID is less than 8 chars, pad with spaces
    user_id += String(8 - user_id.length(), ' ');
  }
  // Format lock status into LoRa packet
  sprintf(txpacket, "aut,%d,%s,%s", BIKE_ID, user_id.c_str(), identified ? "UNLOCKED" : "LOCKED");
}

// Encryption function using AES
// Uses mbedTLS library for AES encryption
// Pads input to multiple of 16 bytes using PKCS7 padding
void aes_encrypt(const uint8_t *input, size_t length, uint8_t *output, size_t *out_len) {
    mbedtls_aes_context aes;
    mbedtls_aes_init(&aes);
    mbedtls_aes_setkey_enc(&aes, aes_key, 128);
    size_t padded_len = ((length + 15) / 16) * 16;
    uint8_t buf[padded_len];
    memcpy(buf, input, length);
    uint8_t pad = padded_len - length;
    for (size_t i = length; i < padded_len; i++) buf[i] = pad;
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