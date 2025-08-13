# sensor_test.py
# Minimal hardware test for: LEDs, Relay, Ultrasonic, RFID (UART), Wi-Fi, MQTT
# Board: MicroPython (ESP32/ESP8266). Adjust pins if your wiring differs.

import time
import json
from machine import Pin, UART, time_pulse_us
import network

# -------- CONFIG --------
DEVICE_ID = "b01"

# Pins (adjust to your wiring)
LED_RED_PIN = 2
LED_GREEN_PIN = 15
RELAY_PIN = 5
RELAY_ACTIVE_HIGH = True  # True: relay ON when pin=1; False when pin=0
ULTRASONIC_SIG_PIN = 12
UART_TX_PIN = 17
UART_RX_PIN = 16
UART_BAUD = 9600

# Networking (set ENABLE_WIFI/MQTT to False if you just want local tests)
ENABLE_WIFI = True
ENABLE_MQTT = False  # Set True to test MQTT publish
SSID = "RPiStation01"
PASSWORD = "RPiStation01"
MQTT_SERVER = "192.199.2.254"
MQTT_TOPIC = "station/bornes/replies"

# Test timing
PRINT_INTERVAL_S = 1.0      # status print frequency
RELAY_TOGGLE_EVERY_S = 5.0  # toggle relay this often
LED_BLINK_PERIOD_S = 0.5    # blink LEDs

# Ultrasonic sanity range (cm)
MIN_CM, MAX_CM = 2, 400

# -------- HARDWARE --------
led_red = Pin(LED_RED_PIN, Pin.OUT, value=0)
led_green = Pin(LED_GREEN_PIN, Pin.OUT, value=0)

if RELAY_ACTIVE_HIGH:
    relay = Pin(RELAY_PIN, Pin.OUT, value=0)  # OFF by default
else:
    relay = Pin(RELAY_PIN, Pin.OUT, value=1)  # OFF by default (inactive level)

def relay_on():
    relay.value(1 if RELAY_ACTIVE_HIGH else 0)

def relay_off():
    relay.value(0 if RELAY_ACTIVE_HIGH else 1)

# Grove Ultrasonic on a single SIG pin
class GroveUltrasonic:
    def __init__(self, sig_pin):
        self.pin = Pin(sig_pin, Pin.OUT)
        self.pin.value(0)

    def distance_cm(self, timeout_us=30000):
        # Trigger 10us HIGH pulse
        self.pin.init(Pin.OUT)
        self.pin.value(0); time.sleep_us(2)
        self.pin.value(1); time.sleep_us(10)
        self.pin.value(0)
        # Listen for echo high
        self.pin.init(Pin.IN)
        t = time_pulse_us(self.pin, 1, timeout_us)
        if t < 0:
            return None
        d = t / 58.0  # microseconds to cm (approx for 20°C)
        if d < MIN_CM or d > MAX_CM:
            return None
        return d

ultra = GroveUltrasonic(ULTRASONIC_SIG_PIN)

# RFID UART (non-blocking read)
try:
    rfid_uart = UART(1, baudrate=UART_BAUD, tx=UART_TX_PIN, rx=UART_RX_PIN, timeout=10)
except Exception as e:
    print("UART init failed:", e)
    rfid_uart = None

def read_rfid():
    if not rfid_uart:
        return None
    try:
        if rfid_uart.any():
            data = rfid_uart.read()
            if not data:
                return None
            try:
                s = data.decode("utf-8", "ignore").strip()
                return s if s else None
            except Exception:
                return None
    except Exception as e:
        print("RFID read error:", e)
    return None

# -------- WIFI / MQTT (optional) --------
client = None

def connect_wifi(ssid, password, timeout=20):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        print("Wi-Fi already connected:", sta.ifconfig())
        return sta
    print("Connecting Wi-Fi to", ssid)
    sta.connect(ssid, password)
    t0 = time.time()
    while not sta.isconnected() and (time.time() - t0) < timeout:
        time.sleep(1)
        print(" ... waiting")
    if not sta.isconnected():
        raise RuntimeError("Wi-Fi connect timeout")
    print("Wi-Fi connected:", sta.ifconfig())
    return sta

def connect_mqtt():
    global client
    try:
        from umqtt.simple import MQTTClient
    except ImportError:
        print("umqtt.simple not found; disable ENABLE_MQTT or install it.")
        return None
    try:
        client = MQTTClient(DEVICE_ID, MQTT_SERVER, keepalive=60)
        client.connect()
        print("MQTT connected to", MQTT_SERVER)
        return client
    except Exception as e:
        print("MQTT connect error:", e)
        client = None
        return None

def mqtt_publish(topic, payload):
    if not client:
        return False
    try:
        msg = json.dumps(payload)
        client.publish(topic, msg)
        return True
    except Exception as e:
        print("MQTT publish error:", e)
        return False

# -------- SIMPLE TEST HELPERS --------
def blink_nonblocking(led, period_s, tnow):
    phase = int((tnow * 1000) // int(period_s * 1000)) % 2
    led.value(1 if phase == 0 else 0)

def self_test_once():
    print("\n=== SELF TEST START ===")

    # LEDs
    print("LEDs: red on"); led_red.on(); time.sleep(0.3)
    print("LEDs: green on"); led_green.on(); time.sleep(0.3)
    print("LEDs: both off"); led_red.off(); led_green.off()

    # Relay
    print("Relay: ON 1s")
    relay_on(); time.sleep(1.0)
    print("Relay: OFF")
    relay_off()

    # Ultrasonic
    d = ultra.distance_cm()
    print("Ultrasonic distance (cm):", "None" if d is None else "{:.1f}".format(d))

    # RFID
    print("RFID: present a card (1s window)")
    t0 = time.ticks_ms()
    got = None
    while time.ticks_diff(time.ticks_ms(), t0) < 1000:
        s = read_rfid()
        if s:
            got = s
            break
        time.sleep_ms(50)
    print("RFID read:", got if got else "None")

    print("=== SELF TEST END ===\n")

# -------- MAIN LOOP --------
def main():
    sta = None
    if ENABLE_WIFI:
        try:
            sta = connect_wifi(SSID, PASSWORD)
        except Exception as e:
            print("Wi-Fi error:", e)

    if ENABLE_MQTT and sta:
        connect_mqtt()

    # startup blink
    for _ in range(3):
        led_green.on(); time.sleep(0.15)
        led_green.off(); time.sleep(0.15)

    self_test_once()

    print("Entering sensor test loop. Press Ctrl+C to stop.")
    last_print = 0
    last_relay_toggle = 0
    relay_state = False

    try:
        while True:
            now_s = time.time()

            # Blink LEDs in opposite phase so both are tested
            blink_nonblocking(led_green, LED_BLINK_PERIOD_S, now_s)
            blink_nonblocking(led_red, LED_BLINK_PERIOD_S, now_s + LED_BLINK_PERIOD_S/2)

            # Toggle relay periodically
            if now_s - last_relay_toggle >= RELAY_TOGGLE_EVERY_S:
                relay_state = not relay_state
                relay_on() if relay_state else relay_off()
                print("[{:d}] Relay {}".format(int(now_s), "ON" if relay_state else "OFF"))
                last_relay_toggle = now_s

            # Read ultrasonic frequently but print at a slower rate
            distance = ultra.distance_cm()

            # Non-blocking RFID read
            rfid = read_rfid()

            # Periodic status print
            if now_s - last_print >= PRINT_INTERVAL_S:
                print("[{}] dist_cm={} | rfid={} | wifi={} | mqtt={}".format(
                    int(now_s),
                    "None" if distance is None else "{:.1f}".format(distance),
                    (rfid if rfid else "None"),
                    ("up" if (ENABLE_WIFI and sta and sta.isconnected()) else "off"),
                    ("up" if (ENABLE_MQTT and client) else "off"),
                ))
                # Optional: publish a heartbeat if MQTT is enabled
                if ENABLE_MQTT and client:
                    mqtt_publish(MQTT_TOPIC, {
                        "type": "sensor_test",
                        "stack_id": DEVICE_ID,
                        "distance_cm": None if distance is None else round(distance, 1),
                        "rfid": rfid,
                        "relay": "on" if relay_state else "off",
                        "ts": now_s
                    })
                last_print = now_s

            time.sleep(0.05)  # keep CPU usage reasonable

    except KeyboardInterrupt:
        print("\nStopping test...")

    finally:
        # cleanup
        try:
            if client:
                mqtt_publish(MQTT_TOPIC, {"type": "disconnect", "stack_id": DEVICE_ID, "ts": time.time()})
                client.disconnect()
        except Exception as e:
            print("MQTT cleanup error:", e)
        led_red.off(); led_green.off(); relay_off()
        print("Cleanup done.")

# ----- run -----
if __name__ == "__main__":
    main()
