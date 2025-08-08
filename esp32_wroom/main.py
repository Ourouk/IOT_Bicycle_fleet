import time
from machine import Pin, ADC, UART,time_pulse_us
import network
from umqtt.simple import MQTTClient
import json
from time import sleep_us

# Constants for ultrasonic sensor
_TIMEOUT1 = 1000  # Timeout for waiting for echo start
_TIMEOUT2 = 1000  # Timeout for waiting for echo end

# ========== CONFIGURATION ==========
# Identifiers
DEVICE_ID = "b01"  # Stack ID

# WiFi credentials
SSID = "RPiStation01"
PASSWORD = "RPiStation01"

# MQTT configuration
# Note: Use device ID as the client ID for mqtt
MQTT_SERVER = "192.199.2.254"  # Fixed IP address format
TOPIC_PUB = "station/bornes/replies"  # Strings instead of bytes
TOPIC_SUB = "station/bornes"

# Debug configuration
DEBUG_ENABLED = True  # Set to False to disable debug messages
DEBUG_INTERVAL = 5    # Debug messages every 5 seconds

# ========== HARDWARE SETUP ==========
led_red = Pin(2, Pin.OUT)      # Status LED (red)
led_green = Pin(15, Pin.OUT)    # Parking indicator (green)


#==========Class to handle the GroveUltrasonic Ranger==
class GroveUltrasonic:
    def __init__(self, sig_pin, vcc_is_3v3=True):
        # vcc_is_3v3 is just a reminder; no logic used.
        self.pin = Pin(sig_pin, Pin.OUT)
        self._low()

    def _low(self):
        self.pin.init(Pin.OUT)
        self.pin.value(0)

    def _high(self):
        self.pin.init(Pin.OUT)
        self.pin.value(1)

    def distance_cm(self, timeout_us=30000):
        # 1) Trigger: 10 µs high
        self._low()
        time.sleep_us(2)
        self._high()
        time.sleep_us(10)
        self._low()

        # 2) Switch to input and time the echo high pulse
        self.pin.init(Pin.IN)
        t = time_pulse_us(self.pin, 1, timeout_us)  # waits for high, then measures high duration

        if t < 0:
            # -2: timed out waiting for high; -1: timed out while high
            return None

        # 3) Convert to distance (cm). 58 us per cm is a common approximation.
        return t / 58.0

# Initialize UART for RFID reader
try:
    rfid_uart = UART(1, baudrate=9600, tx=17, rx=16)  # Specify pins explicitly
except Exception as e:
    print(f"Warning: UART initialization failed: {e}")
    rfid_uart = None

# Initialize ultrasonic sensor
ultrasonic_sensor = GroveUltrasonic(12)

# Initialize LEDs
led_red.off()
led_green.off()

# ========== STATE VARIABLES ==========
stack_available = True
current_bike_id = None
last_park_request = None
parking_confirmation_timeout = 30  # 30 seconds to park after confirmation
client = None  # Initialize client variable

# Debug variables
last_debug_print = 0

# ========== HELPER FUNCTIONS ==========
def debug_print(message):
    """Print debug message if debug is enabled"""
    if DEBUG_ENABLED:
        print(f"[DEBUG] {message}")

def get_distance(sensor=ultrasonic_sensor, timeout_us=30000, block=False):
    """
    Read distance (cm) from the GroveUltrasonic.
    - Returns None on timeout/out-of-range unless block=True.
    - If block=True, keeps trying until a valid reading (2–400 cm) is obtained.
    """
    while True:
        dist = sensor.distance_cm(timeout_us=timeout_us)  # cm or None
        if dist is not None and 2 <= dist <= 400:
            return dist
        if not block:
            return None

def measure_distance(sensor=ultrasonic_sensor, samples=5, delay_ms=10, retries=2, retry_delay_ms=5):
    """Read distance with multiple samples, handle None safely, basic filtering, and debug logs."""
    try:
        readings = []
        raw_values = []  # approximate echo pulse widths in µs for debug (or None)

        for _ in range(samples):
            # Optional small retry loop to reduce transient None readings
            distance = None
            for _ in range(retries + 1):
                distance = get_distance(sensor)  # may return None
                if distance is not None:
                    break
                time.sleep_ms(retry_delay_ms)

            # Log raw pulse estimate if we have a valid distance; else keep None
            if distance is not None:
                raw_values.append(int(distance * 58))  # µs
                readings.append(distance)
            else:
                raw_values.append(None)

            time.sleep_ms(delay_ms)

        # Filter out invalid readings
        valid_readings = [r for r in readings if r > 0]

        if not valid_readings:
            debug_print(f"DISTANCE - No valid readings obtained | raw={raw_values}")
            return None

        avg_distance = sum(valid_readings) / len(valid_readings)

        # Debug output
        debug_print(f"DISTANCE - Raw durations (µs): {raw_values}")
        debug_print(f"DISTANCE - Distance readings (cm): {readings}")
        debug_print(f"DISTANCE - Average distance: {avg_distance:.1f}cm")

        # Return distance if reasonable (between 2 cm and 400 cm)
        result = avg_distance if 2 <= avg_distance <= 400 else None
        debug_print(f"DISTANCE - Final result: {result}")
        return result

    except Exception as e:
        debug_print(f"DISTANCE - Error: {e}")
        print(f"Error reading ultrasonic sensor: {e}")
        return None


    except Exception as e:
        debug_print(f"DISTANCE - Error: {e}")
        print(f"Error reading ultrasonic sensor: {e}")
        return None

def connect_wifi(ssid, password, timeout=20):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)

    if sta.isconnected():
        print("Already connected to WiFi:", sta.ifconfig())
        return sta

    print(f"Connecting to WiFi: {ssid}")
    sta.connect(ssid, password)

    count = 0
    while not sta.isconnected() and count < timeout:
        time.sleep(1)
        count += 1
        if count % 5 == 0:
            print(f"Still connecting... ({count}/{timeout})")

    if not sta.isconnected():
        raise RuntimeError(f"Could not connect to WiFi after {timeout} seconds")

    print("WiFi connected:", sta.ifconfig())
    return sta

def connect_mqtt(DEVICE_ID, server, timeout=10):
    try:
        mqtt_client = MQTTClient(DEVICE_ID, server, keepalive=60)
        mqtt_client.connect()
        print(f"Connected to MQTT broker at {server}")
        return mqtt_client
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")
        return None

def publish_data(client, payload):
    if client is None:
        print("MQTT client not connected")
        return False
    try:
        message = json.dumps(payload)
        client.publish(TOPIC_PUB, message)
        print("Data sent:", payload)
        return True
    except Exception as e:
        print(f"Failed to publish data: {e}")
        return False

def read_rfid():
    """Read RFID tag from UART"""
    if rfid_uart is None:
        debug_print("RFID - UART not initialized")
        return None

    try:
        # Check if data is available
        bytes_available = rfid_uart.any()
        debug_print(f"RFID - Bytes available: {bytes_available}")

        if bytes_available:
            rfid_data = rfid_uart.read()
            debug_print(f"RFID - Raw data: {rfid_data}")

            if rfid_data:
                try:
                    decoded = rfid_data.decode('utf-8').strip()
                    debug_print(f"RFID - Decoded: '{decoded}', Length: {len(decoded)}")

                    if len(decoded) > 0:
                        debug_print(f"RFID - Valid tag detected: '{decoded}'")
                        return decoded
                    else:
                        debug_print("RFID - Empty string after decoding")
                except UnicodeDecodeError as de:
                    debug_print(f"RFID - Decode error: {de}")
                    return None
        else:
            debug_print("RFID - No data available")

    except Exception as e:
        debug_print(f"RFID - Exception: {e}")
        print(f"Error reading RFID: {e}")

    debug_print("RFID - Returning None")
    return None

def check_bike_parked():
    """Check if bike is properly parked (distance + RFID)"""
    debug_print("BIKE_PARKED - Starting check")
    debug_print(f"BIKE_PARKED - Current expected bike ID: {current_bike_id}")

    # Get distance measurement
    distance = measure_distance()
    debug_print(f"BIKE_PARKED - Distance measurement: {distance}")

    if distance is None:
        result = (False, "Distance sensor error")
        debug_print(f"BIKE_PARKED - {result}")
        return result

    if distance > 20:
        result = (False, f"Distance too far: {distance:.1f}cm (threshold: 20cm)")
        debug_print(f"BIKE_PARKED - {result}")
        return result

    debug_print(f"BIKE_PARKED - Distance OK: {distance:.1f}cm <= 20cm")

    # Get RFID reading
    rfid_tag = read_rfid()
    debug_print(f"BIKE_PARKED - RFID reading: '{rfid_tag}'")

    if rfid_tag is None:
        result = (False, "No RFID detected")
        debug_print(f"BIKE_PARKED - {result}")
        return result

    debug_print(f"BIKE_PARKED - Comparing RFID: expected='{current_bike_id}', got='{rfid_tag}'")

    if current_bike_id and rfid_tag == current_bike_id:
        result = (True, f"RFID matched: {rfid_tag}")
        debug_print(f"BIKE_PARKED - SUCCESS: {result}")
        return result

    result = (False, f"RFID mismatch: expected '{current_bike_id}', got '{rfid_tag}'")
    debug_print(f"BIKE_PARKED - {result}")
    return result

def mqtt_callback(topic, msg):
    """Handle incoming MQTT messages"""
    global stack_available, current_bike_id, last_park_request

    try:
        # Decode bytes to string first
        if isinstance(msg, bytes):
            msg = msg.decode('utf-8')

        payload = json.loads(msg)
        print("Received MQTT message:", payload)

        if payload.get('type') == 'park_request':
            bike_id = payload.get('bike_id')
            target_stack = payload.get('stack_id', DEVICE_ID)

            debug_print(f"MQTT - Park request: bike_id='{bike_id}', target_stack='{target_stack}', this_device='{DEVICE_ID}'")

            # Check if this message is for this stack
            if target_stack != DEVICE_ID:
                debug_print(f"MQTT - Message not for this device, ignoring")
                return

            if stack_available:
                # Confirm parking availability
                confirm_msg = {
                    'type': 'park_confirm',
                    'stack_id': DEVICE_ID,
                    'bike_id': bike_id,
                    'status': 'available',
                    'timestamp': time.time()
                }
                publish_data(client, confirm_msg)
                current_bike_id = bike_id
                last_park_request = time.time()
                stack_available = False

                debug_print(f"MQTT - Parking confirmed, updated state: current_bike_id='{current_bike_id}', stack_available={stack_available}")

                # Blink green LED to indicate parking allowed
                print(f"Parking confirmed for bike {bike_id}")
                for _ in range(3):
                    led_green.on()
                    time.sleep(0.3)
                    led_green.off()
                    time.sleep(0.3)
            else:
                # Reject parking request
                reject_msg = {
                    'type': 'park_reject',
                    'stack_id': DEVICE_ID,
                    'bike_id': bike_id,
                    'status': 'occupied',
                    'timestamp': time.time()
                }
                publish_data(client, reject_msg)
                debug_print(f"MQTT - Parking rejected, stack occupied")
                print(f"Parking rejected for bike {bike_id} - stack occupied")

        elif payload.get('type') == 'status_request':
            # Respond with current status
            status_msg = {
                'type': 'status_response',
                'stack_id': DEVICE_ID,
                'available': stack_available,
                'current_bike': current_bike_id,
                'timestamp': time.time()
            }
            publish_data(client, status_msg)
            debug_print(f"MQTT - Status response sent: available={stack_available}, current_bike='{current_bike_id}'")

    except Exception as e:
        print(f"Error processing MQTT message: {e}")
        debug_print(f"MQTT - Exception: {e}")

def blink_led(led, times=1, delay=0.5):
    """Helper function to blink LED"""
    for _ in range(times):
        led.on()
        time.sleep(delay)
        led.off()
        time.sleep(delay)

def print_system_status():
    """Print comprehensive system status"""
    print("\n" + "="*50)
    print("SYSTEM STATUS REPORT")
    print("="*50)
    print(f"Device ID: {DEVICE_ID}")
    print(f"Stack Available: {stack_available}")
    print(f"Current Bike ID: {current_bike_id}")
    print(f"Last Park Request: {last_park_request}")
    print(f"WiFi Connected: {sta.isconnected() if 'sta' in globals() else 'Unknown'}")
    print(f"MQTT Connected: {client is not None}")

    # Get current sensor readings
    distance = measure_distance()
    rfid = read_rfid()
    bike_status = check_bike_parked()

    print(f"Current Distance: {distance}")
    print(f"Current RFID: {rfid}")
    print(f"Bike Parked Status: {bike_status}")
    print("="*50 + "\n")

# ========== MAIN PROGRAM ==========
try:
    # Connect to WiFi
    print("Starting bike parking system...")
    debug_print("System initialization started")
    sta = connect_wifi(SSID, PASSWORD)

    # Indicate WiFi connected
    blink_led(led_green, 3, 0.2)

    # Connect to MQTT broker
    client = connect_mqtt(DEVICE_ID, MQTT_SERVER)
    if client:
        client.set_callback(mqtt_callback)
        client.subscribe(TOPIC_SUB)
        print(f"Subscribed to {TOPIC_SUB}")
        blink_led(led_green, 5, 0.1)

    # Initialize timing variables
    last_heartbeat = 0
    last_mqtt_reconnect = 0
    last_status_check = 0
    last_debug_print = 0
    mqtt_reconnect_interval = 30
    heartbeat_interval = 30
    status_check_interval = 2

    print("System ready - entering main loop")
    debug_print("Main loop started")

    while True:
        current_time = time.time()

        # Print periodic debug information
        if DEBUG_ENABLED and (current_time - last_debug_print > DEBUG_INTERVAL):
            print_system_status()
            last_debug_print = current_time

        # Check WiFi connection
        if not sta.isconnected():
            print("WiFi disconnected, attempting reconnection...")
            led_red.on()
            try:
                sta = connect_wifi(SSID, PASSWORD)
                led_red.off()
                debug_print("WiFi reconnected successfully")
            except Exception as e:
                print(f"WiFi reconnection failed: {e}")
                debug_print(f"WiFi reconnection failed: {e}")
                time.sleep(5)
                continue

        # Check MQTT connection and reconnect if needed
        if client is None and (current_time - last_mqtt_reconnect > mqtt_reconnect_interval):
            print("Attempting MQTT reconnection...")
            debug_print("Attempting MQTT reconnection...")
            client = connect_mqtt(DEVICE_ID, MQTT_SERVER)
            if client:
                client.set_callback(mqtt_callback)
                client.subscribe(TOPIC_SUB)
                print("MQTT reconnected successfully")
                debug_print("MQTT reconnected successfully")
            last_mqtt_reconnect = current_time

        # Check for incoming MQTT messages
        if client:
            try:
                client.check_msg()
            except Exception as e:
                print(f"MQTT check_msg error: {e}")
                debug_print(f"MQTT check_msg error: {e}")
                client = None  # Force reconnection

        # Send heartbeat periodically
        if current_time - last_heartbeat > heartbeat_interval:
            heartbeat_msg = {
                'type': 'heartbeat',
                'stack_id': DEVICE_ID,
                'status': 'active',
                'available': stack_available,
                'current_bike': current_bike_id,
                'timestamp': current_time
            }
            if publish_data(client, heartbeat_msg):
                last_heartbeat = current_time
                debug_print("Heartbeat sent successfully")
            else:
                client = None  # Force reconnection on publish failure
                debug_print("Heartbeat failed, forcing reconnection")

        # Handle parking confirmation timeout
        if (last_park_request and
            (current_time - last_park_request > parking_confirmation_timeout)):
            print("Parking confirmation timed out")
            debug_print(f"Parking timeout: {current_time - last_park_request}s > {parking_confirmation_timeout}s")
            timeout_msg = {
                'type': 'park_timeout',
                'stack_id': DEVICE_ID,
                'bike_id': current_bike_id,
                'timestamp': current_time
            }
            publish_data(client, timeout_msg)

            last_park_request = None
            stack_available = True
            current_bike_id = None
            led_green.off()
            debug_print("State reset due to timeout")

        # Regular status checks
        if current_time - last_status_check > status_check_interval:
            debug_print("Starting regular status check")

            # Check if bike is properly parked
            if not stack_available and current_bike_id:
                debug_print("Checking if expected bike is properly parked")
                is_parked, status_msg = check_bike_parked()
                debug_print(f"Park check result: {is_parked}, {status_msg}")

                if is_parked:
                    success_msg = {
                        'type': 'park_success',
                        'stack_id': DEVICE_ID,
                        'bike_id': current_bike_id,
                        'status': 'parked',
                        'timestamp': current_time
                    }
                    publish_data(client, success_msg)
                    print(f"Bike {current_bike_id} successfully parked")
                    debug_print(f"Bike successfully parked: {current_bike_id}")
                    last_park_request = None
                    led_green.on()  # Keep green LED on when bike is parked

            # Check for unexpected bike presence
            elif stack_available:
                debug_print("Stack available - checking for unexpected bike")
                distance = measure_distance()
                debug_print(f"Distance check for unexpected bike: {distance}")

                if distance is not None and distance < 20:
                    print("Unexpected bike detected")
                    debug_print(f"Unexpected bike detected at distance {distance}cm")
                    stack_available = False
                    current_bike_id = "unknown"
                    error_msg = {
                        'type': 'error',
                        'stack_id': DEVICE_ID,
                        'message': 'Unexpected bike detected',
                        'distance': distance,
                        'timestamp': current_time
                    }
                    publish_data(client, error_msg)
                    led_green.off()
                else:
                    led_green.off()  # Ensure LED is off when no bike
                    if distance is not None:
                        debug_print(f"No bike detected, distance: {distance}cm")

            last_status_check = current_time
            debug_print("Status check completed")

        time.sleep(0.1)  # Small delay to prevent CPU overload

except KeyboardInterrupt:
    print("\nProcess stopped by user")
    debug_print("Process interrupted by user")

except Exception as e:
    print(f"Unexpected error: {e}")
    debug_print(f"Unexpected error: {e}")
    led_red.on()

finally:
    print("Cleaning up...")
    debug_print("Starting cleanup")
    try:
        if client:
            disconnect_msg = {
                'type': 'disconnect',
                'stack_id': DEVICE_ID,
                'timestamp': time.time()
            }
            publish_data(client, disconnect_msg)
            client.disconnect()
            print("Disconnected from MQTT broker")
            debug_print("MQTT client disconnected")
    except Exception as e:
        print(f"Error during cleanup: {e}")
        debug_print(f"Cleanup error: {e}")

    # Turn off all LEDs
    led_red.off()
    led_green.off()
    debug_print("LEDs turned off")
    print("System shutdown complete")
