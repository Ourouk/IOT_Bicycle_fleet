# =========================================
# Bike Parking Station (Micropython) - Simplified flows
# =========================================
# This firmware runs the “smart rack”:
# - Controls a relay-based lock
# - Uses a Grove Ultrasonic Ranger to detect a wheel present/absent
# - Reads user and bike RFID via UART
# - Talks to a backend over MQTT
# Design notes:
# - Two high-level flows:
#     1) Locking flow  (user scans while unlocked -> place bike -> scan bike tag)
#     2) Unlocking flow (user scans while locked -> remove bike -> scan again to relock)
# - Authorization is round-tripped to the server using a user_auth request
#   and an auth_response message back from the server.
# - The ultrasonic sensor uses simple near/far thresholds with averaging.
# - LEDs indicate status: red=locked, green=unlocked/parking guidance.
# - Non-blocking LED blink helper avoids stalling the main loop.
#
# Safety:
# - Timeouts are used to avoid being stuck in intermediate states.
# - On timeout during unlock (bike not removed), the rack reverts to idle,
#   leaving the relay on for safety and user retry (design choice; revisit if needed).
#
# Maintenance:
# - All explanatory comments are kept or expanded to ease comprehension.
# - If you need a minimal diff-based patch later, ask and I’ll provide one.
# =========================================

import time
from machine import Pin, ADC, UART, time_pulse_us
import network
from umqtt.simple import MQTTClient
import json
from time import sleep_us

# ========== CONFIGURATION ==========
# Identifiers
DEVICE_ID = "1"  # Rack ID shown to the server and logs

# WiFi credentials (AP provided by site router or hotspot)
SSID = "RPiStation01"
PASSWORD = "RPiStation01"

# MQTT configuration
# Note: Use device ID as the client ID for MQTT to keep sessions distinct
MQTT_SERVER = "192.199.2.254"
TOPIC_PUB = "station/rack/replies"  # outbound (from rack to server)
TOPIC_SUB = "station/rack"          # inbound (from server to rack)

# Debug configuration
DEBUG_ENABLED = True     # Set to False in production to reduce serial noise
DEBUG_INTERVAL = 5       # Emit a status snapshot every N seconds

# Grove Relay configuration
RELAY_PIN = 5            # Set to your Grove relay SIG pin
RELAY_ACTIVE_HIGH = True # True: relay ON when pin=1; False: relay ON when pin=0

# Distance thresholds (cm) for the ultrasonic ranger
# Hysteresis prevents relay chatter and state flapping:
# - OBJECT_NEAR_CM: declare “object present” when distance <= this
# - OBJECT_FAR_CM:  declare “object absent”  when distance >= this
OBJECT_NEAR_CM = 3.0 # Min values that the sensor can see
OBJECT_FAR_CM  = 11.0 # Max values when lock is engaged

# Timeouts (seconds)
# - AUTH_RESPONSE_TIMEOUT: wait time for server to approve/deny user
# - LOCK_FLOW_TIMEOUT:     time allowed to place the bike after unlock
# - UNLOCK_FLOW_TIMEOUT:   time allowed to remove the bike after unlock
# - RELOCK_CONFIRM_TIMEOUT:time to re-present card to re-lock after removal
AUTH_RESPONSE_TIMEOUT   = 1
LOCK_FLOW_TIMEOUT       = 30
UNLOCK_FLOW_TIMEOUT     = 30
RELOCK_CONFIRM_TIMEOUT  = 30

# LED blink timing (visual feedback pacing)
BLINK_SHORT = 0.2
BLINK_LONG  = 0.5

# ========== HARDWARE SETUP ==========
# LEDs:
# - led_red: steady ON means “locked”
# - led_green: steady ON means “unlocked / ready to park”; blinking guides actions
led_red = Pin(2, Pin.OUT)       # Status LED (red)
led_green = Pin(15, Pin.OUT)    # Parking indicator (green)

# Relay wiring note:
# - RELAY_ACTIVE_HIGH True -> .value(1) energizes relay (unlocked if lock is NC/NO accordingly)
# - RELAY_ACTIVE_HIGH False -> .value(0) energizes relay
if RELAY_ACTIVE_HIGH:
    relay = Pin(RELAY_PIN, Pin.OUT, value=0)  # default OFF (locked)
else:
    relay = Pin(RELAY_PIN, Pin.OUT, value=1)  # default OFF (locked at inactive level)

def relay_on():
    """Energize relay: unlock mechanical lock (depending on hardware)."""
    relay.value(1 if RELAY_ACTIVE_HIGH else 0)

def relay_off():
    """De-energize relay: lock mechanical lock (depending on hardware)."""
    relay.value(0 if RELAY_ACTIVE_HIGH else 1)

# ========== Grove Ultrasonic Ranger ==========
# Uses single-wire SIG pin with time-of-flight echo measure.
# Timing constants are kept for clarity though the driver uses time_pulse_us.
_TIMEOUT1 = 1000  # Timeout for waiting for echo start (not directly used here)
_TIMEOUT2 = 1000  # Timeout for waiting for echo end   (not directly used here)

class GroveUltrasonic:
    """Minimal driver for Grove Ultrasonic Ranger (single SIG pin)."""
    def __init__(self, sig_pin, vcc_is_3v3=True):
        self.pin = Pin(sig_pin, Pin.OUT)
        self._low()

    def _low(self):
        self.pin.init(Pin.OUT)
        self.pin.value(0)

    def _high(self):
        self.pin.init(Pin.OUT)
        self.pin.value(1)

    def distance_cm(self, timeout_us=30000):
        """
        Trigger a pulse and measure echo high time with time_pulse_us.
        Returns distance in centimeters, or None on timeout.
        Conversion constant ~58 us per cm (speed of sound roundtrip).
        """
        # 10 µs trigger pulse
        self._low()
        time.sleep_us(2)
        self._high()
        time.sleep_us(10)
        self._low()

        # Switch to input to read echo
        self.pin.init(Pin.IN)
        t = time_pulse_us(self.pin, 1, timeout_us)
        if t < 0:
            return None
        return t / 58.0

# Initialize UART for RFID reader (user and bike tags come over same UART)
try:
    rfid_uart = UART(1, baudrate=9600, tx=17, rx=16)  # Pinout: adjust per board
except Exception as e:
    print(f"Warning: UART initialization failed: {e}")
    rfid_uart = None  # System can still run for diagnostics without RFID

# Initialize ultrasonic sensor on SIG pin 12 (change if wired differently)
ultrasonic_sensor = GroveUltrasonic(12)

# Initialize LEDs and relay to known safe state at boot
led_red.off()
led_green.off()
relay_off()

# ========== STATE VARIABLES ==========
# Bike association:
# - current_bike_id None -> rack is available
# - set when a bike tag is read during locking flow
current_bike_id = None

# MQTT client handle (None until connected)
client = None

# Debug pacing
last_debug_print = 0

# Authorization handshake tracking:
# - pending_auth_action: "lock" or "unlock" currently being authorized
# - pending_auth_user: user ID we asked the server to approve
# - awaiting_auth: True until an auth_response arrives (or we timeout)
# - last_auth_result: the latest response payload from server (for this request)
pending_auth_action = None
pending_auth_user   = None
awaiting_auth       = False
auth_request_time   = 0
last_auth_result    = None

# High-level finite state machine (FSM)
# Naming tries to reflect both user intent and hardware posture.
STATE_IDLE                   = "idle"
STATE_AUTH_LOCKING           = "auth_locking"            # waiting auth for locking flow
STATE_LOCKING_UNLOCK_RELAY   = "locking_unlock_relay"    # relay energized, grant access
STATE_LOCKING_WAIT_OBJECT    = "locking_wait_object"     # wait for wheel near
STATE_LOCKING_READ_BIKE_RFID = "locking_read_bike_rfid"  # (kept for clarity; merged in WAIT_OBJECT)

STATE_AUTH_UNLOCKING         = "auth_unlocking"          # waiting auth for unlocking flow
STATE_UNLOCKING_RELAY_ON     = "unlocking_relay_on"      # relay on, green on; user removes bike
STATE_UNLOCKING_WAIT_RELOCK  = "unlocking_wait_relock"   # removed -> wait for user to re-scan to relock
STATE_ERROR                  = "error"                   # generic error state (LEDs blink)

# Current FSM state
state = STATE_IDLE
state_entered_at = time.time()

# ========== HELPER FUNCTIONS ==========
def debug_print(message):
    """Guarded debug print to keep serial output manageable."""
    if DEBUG_ENABLED:
        print(f"[DEBUG] {message}")

def now():
    """Wall-clock seconds (float)."""
    return time.time()

def set_state(new_state):
    """Transition FSM to a new state and timestamp the entry."""
    global state, state_entered_at
    state = new_state
    state_entered_at = now()
    debug_print(f"STATE -> {state}")

def blink_led_nonblocking(led, period=0.6):
    """
    Non-blocking blinker to provide visual guidance without sleeping.
    Call this repeatedly in the loop; it toggles the given LED based on time.
    """
    t = now()
    phase = int((t * 1000) // int(period * 1000)) % 2
    led.value(1 if phase == 0 else 0)
    return True

def solid_leds(green=False, red=False):
    """Convenience to set LEDs to a steady state."""
    led_green.value(1 if green else 0)
    led_red.value(1 if red else 0)

def get_distance(sensor=ultrasonic_sensor, timeout_us=30000, block=False):
    """
    Single shot distance read with basic plausibility filter.
    Returns None on invalid/timeout unless block=True (then retries forever).
    """
    while True:
        dist = sensor.distance_cm(timeout_us=timeout_us)
        if dist is not None and 2 <= dist <= 400:
            return dist
        if not block:
            return None

def measure_distance(sensor=ultrasonic_sensor, samples=5, delay_ms=10, retries=2, retry_delay_ms=5):
    """
    Take multiple readings and average valid ones to reduce noise.
    Returns averaged cm value or None if all attempts fail.
    """
    try:
        readings, raw_values = [], []
        for _ in range(samples):
            distance = None
            for _ in range(retries + 1):
                distance = get_distance(sensor)
                if distance is not None:
                    break
                time.sleep_ms(retry_delay_ms)
            raw_values.append(int(distance * 58) if distance is not None else None)
            if distance is not None:
                readings.append(distance)
            time.sleep_ms(delay_ms)
        valid = [r for r in readings if r > 0]
        if not valid:
            debug_print(f"DISTANCE - No valid readings | raw={raw_values}")
            return None
        avg_distance = sum(valid) / len(valid)
        debug_print(f"DISTANCE - avg={avg_distance:.1f}cm readings={readings}")
        return avg_distance if 2 <= avg_distance <= 400 else None
    except Exception as e:
        debug_print(f"DISTANCE - Error: {e}")
        print(f"Error reading ultrasonic sensor: {e}")
        return None

def connect_wifi(ssid, password, timeout=20):
    """
    Connect to WiFi STA. Retries up to `timeout` seconds.
    Returns WLAN interface on success, raises on failure.
    """
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if sta.isconnected():
        print("Already connected to WiFi:", sta.ifconfig())
        return sta
    print(f"Connecting to WiFi: {ssid}")
    sta.connect(ssid, password)
    count = 0
    while not sta.isconnected() and count < timeout:
        time.sleep(1); count += 1
        if count % 5 == 0:
            print(f"Still connecting... ({count}/{timeout})")
    if not sta.isconnected():
        raise RuntimeError(f"Could not connect to WiFi after {timeout} seconds")
    print("WiFi connected:", sta.ifconfig())
    return sta

def connect_mqtt(DEVICE_ID, server, timeout=10):
    """
    Establish MQTT connection and return client object.
    Note: keepalive set to 60s; reconnection is handled in main loop.
    """
    try:
        mqtt_client = MQTTClient(DEVICE_ID, server, keepalive=60)
        mqtt_client.connect()
        print(f"Connected to MQTT broker at {server}")
        return mqtt_client
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")
        return None

def publish_data(client, payload):
    """
    Publish JSON payload to TOPIC_PUB. Returns True on success.
    This function centralizes printing and error handling.
    """
    if client is None:
        print("MQTT client not connected")
        return False
    try:
        message = json.dumps(payload)
        client.publish(TOPIC_PUB, message.encode("utf-8"))
        print("Data sent:", payload)
        return True
    except Exception as e:
        print(f"Failed to publish data: {e}")
        return False

# ========== AUTH AND FLOW HELPERS ==========
def send_user_auth(user_id, action):
    """
    Publish an authorization request to the server and start waiting.
    action: "lock" | "unlock"
    The server should respond with an 'auth_response' carrying status ok/denied.
    """
    global awaiting_auth, pending_auth_action, pending_auth_user, auth_request_time, last_auth_result
    payload = {
        'type': 'user_auth',
        'rack_id': DEVICE_ID,
        'user_id': user_id,
        'action': action,  # "lock" or "unlock"
        'timestamp': now()
    }
    publish_data(client, payload)
    awaiting_auth = True
    pending_auth_action = action
    pending_auth_user = user_id
    auth_request_time = now()
    last_auth_result = None
    debug_print(f"Auth requested: user={user_id} action={action}")

def handle_auth_response(payload):
    """
    Process inbound auth response.
    Expected payload:
    {'type':'auth_response','rack_id':'r01','user_id':'...','action':'lock|unlock','status':'ok|denied'}
    Only handles responses matching the pending request (rack, user, action).
    """
    global awaiting_auth, last_auth_result
    if not awaiting_auth:
        debug_print("Received auth response but not awaiting any")
        return
    if payload.get('rack_id') != DEVICE_ID:
        debug_print(f"Auth response for wrong rack: {payload.get('rack_id')} != {DEVICE_ID}")
        return
    if payload.get('action') != pending_auth_action:
        debug_print(f"Auth response for wrong action: {payload.get('action')} != {pending_auth_action}")
        return
    #TODO: Re-enabled when having custom rfid
    # if payload.get('user_id') != pending_auth_user:
    #     debug_print(f"Auth response for wrong user: {payload.get('user_id')} != {pending_auth_user}")
    #     return
    last_auth_result = payload
    awaiting_auth = False
    debug_print(f"Auth response: {payload}")

def read_rfid():
    """
    Read a frame from UART (RFID reader).
    Returns a decoded string (user or bike tag) or None if nothing/invalid.
    """
    if rfid_uart is None:
        debug_print("RFID - UART not initialized")
        return None
    try:
        bytes_available = rfid_uart.any()
        if bytes_available:
            rfid_data = rfid_uart.read()
            if rfid_data:
                try:
                    decoded = rfid_data.decode('utf-8').strip()
                    if len(decoded) > 0:
                        return decoded
                except UnicodeDecodeError:
                    # Some readers output raw binary; ignore undecodable noise
                    return None
    except Exception as e:
        debug_print(f"RFID - Exception: {e}")
        print(f"Error reading RFID: {e}")
    return None

def print_system_status():
    """
    Human-friendly snapshot printed periodically for diagnostics.
    Shows connectivity, state, sensors, and relay posture.
    """
    available = (current_bike_id is None)
    print("\n" + "="*50)
    print("SYSTEM STATUS REPORT")
    print("="*50)
    print(f"Device ID: {DEVICE_ID}")
    print(f"Available: {available}")
    print(f"Current Bike ID: {current_bike_id}")
    print(f"WiFi Connected: {sta.isconnected() if 'sta' in globals() else 'Unknown'}")
    print(f"MQTT Connected: {client is not None}")
    distance = measure_distance()
    rfid = read_rfid()
    print(f"Current Distance: {distance}")
    print(f"Current RFID (peek): {rfid}")
    print(f"Relay State: {'ON' if (relay.value()==(1 if RELAY_ACTIVE_HIGH else 0)) else 'OFF'}")
    print(f"State Machine: {state}")
    print("="*50 + "\n")

def object_present():
    """Return (True, distance_cm) when wheel is near; else (False, distance or None)."""
    d = measure_distance()
    return (d is not None) and (OBJECT_NEAR_CM <= d <= OBJECT_FAR_CM), d

def set_locked_outputs():
    """
    Locked posture:
    - Green OFF (not accepting insertion)
    - Red ON   (locked)
    - Relay OFF (de-energized -> locked, wiring dependent)
    """
    led_green.off()
    led_red.on()
    relay_off()

def set_unlocked_outputs():
    """
    Unlocked posture:
    - Green ON  (guidance: you can insert/remove)
    - Red OFF
    - Relay ON  (energized -> unlocked, wiring dependent)
    """
    led_green.on()
    led_red.off()
    relay_on()

# ========== MQTT CALLBACK ==========
def mqtt_callback(topic, msg):
    """
    Handle inbound MQTT messages on TOPIC_SUB.
    In this simplified version, we only process 'auth_response'.
    """
    global client
    try:
        if isinstance(msg, bytes):
            msg = msg.decode('utf-8')
        payload = json.loads(msg)
        print("Received MQTT message:", payload)

        mtype = payload.get('type')

        # Only process auth responses now (park_confirm/reject/status_request removed)
        if mtype == 'auth_response':
            handle_auth_response(payload)

    except Exception as e:
        print(f"Error processing MQTT message: {e}")
        debug_print(f"MQTT - Exception: {e}")

# ========== MAIN PROGRAM ==========
try:
    print("Starting bike parking system...")
    debug_print("System initialization started")

    # 1) Bring up WiFi
    sta = connect_wifi(SSID, PASSWORD)

    # Visual feedback: quick green blink = WiFi OK
    for _ in range(3):
        led_green.on(); time.sleep(0.2)
        led_green.off(); time.sleep(0.2)

    # 2) Connect to MQTT and subscribe for control messages
    client = connect_mqtt(DEVICE_ID, MQTT_SERVER)
    if client:
        client.set_callback(mqtt_callback)
        client.subscribe(TOPIC_SUB)
        print(f"Subscribed to {TOPIC_SUB}")
        # Visual feedback: a few faster blinks
        for _ in range(5):
            led_green.on(); time.sleep(0.1)
            led_green.off(); time.sleep(0.1)

    # Init timers for periodic tasks
    last_heartbeat = 0
    last_mqtt_reconnect = 0
    last_status_check = 0
    last_debug_print = 0
    mqtt_reconnect_interval = 30   # seconds between reconnect attempts
    heartbeat_interval = 30        # seconds between heartbeats
    status_check_interval = 0.2    # loop pacing for FSM

    print("System ready - entering main loop")
    debug_print("Main loop started")

    # Start from a conservative secure posture: locked
    set_state(STATE_IDLE)
    set_locked_outputs()

    # ========== MAIN LOOP ==========
    while True:
        current_time = now()

        # 0) Periodic debug snapshot for maintenance (optional in production)
        if DEBUG_ENABLED and (current_time - last_debug_print > DEBUG_INTERVAL):
            print_system_status()
            last_debug_print = current_time

        # 1) Ensure WiFi stays up; try to recover if needed
        if not sta.isconnected():
            print("WiFi disconnected, attempting reconnection...")
            led_red.on()  # indicate connectivity issue
            try:
                sta = connect_wifi(SSID, PASSWORD)
                led_red.off()
                debug_print("WiFi reconnected successfully")
            except Exception as e:
                print(f"WiFi reconnection failed: {e}")
                debug_print(f"WiFi reconnection failed: {e}")
                time.sleep(5)
                continue  # retry loop

        # 2) Ensure MQTT stays up; reconnect periodically if dropped
        if client is None and (current_time - last_mqtt_reconnect > mqtt_reconnect_interval):
            print("Attempting MQTT reconnection...")
            client = connect_mqtt(DEVICE_ID, MQTT_SERVER)
            if client:
                client.set_callback(mqtt_callback)
                client.subscribe(TOPIC_SUB)
                print("MQTT reconnected successfully")
                debug_print("MQTT reconnected successfully")
            last_mqtt_reconnect = current_time

        # 3) Drain inbound MQTT (auth responses)
        if client:
            try:
                client.check_msg()  # non-blocking; invokes mqtt_callback
            except Exception as e:
                print(f"MQTT check_msg error: {e}")
                debug_print(f"MQTT check_msg error: {e}")
                client = None  # Force reconnection next cycle

        # 4) Heartbeat: advertise status so server has fresh view without polling
        if current_time - last_heartbeat > heartbeat_interval:
            heartbeat_msg = {
                'type': 'heartbeat',
                'rack_id': DEVICE_ID,
                'status': 'active',
                'available': (current_bike_id is None),  # derived availability
                'current_bike': current_bike_id,
                'state': state,
                'timestamp': current_time
            }
            if publish_data(client, heartbeat_msg):
                last_heartbeat = current_time

        # 5) Main FSM logic (single-pass each loop)
        # Read any RFID now; value can be user (start flow) or bike (during lock)
        rfid = read_rfid()
        # =============== Waiting State ===========================
        if state == STATE_IDLE:
            # Idle posture: waiting for a user to scan a card
            # Decision is based on relay posture:
            # - If a bike is currently not present, we expect a lock flow.
            # - If a bike is currently present, we expect an unlock flow.
            if rfid:
                if current_bike_id is None:
                    # Start locking flow: authorize user who wants to lock a bike
                    send_user_auth(user_id=rfid, action="lock")
                    set_state(STATE_AUTH_LOCKING)
                else:
                    # Start unlocking flow: authorize user who wants to remove a bike
                    send_user_auth(user_id=rfid, action="unlock")
                    set_state(STATE_AUTH_UNLOCKING)
        # =============== Locking Flow ===============================
        elif state == STATE_AUTH_LOCKING:
            # Waiting for server approval to proceed with locking flow
            if not awaiting_auth and last_auth_result:
                if last_auth_result.get('reply') == 'accept':
                    # Grant access: unlock and guide user to insert wheel
                    set_unlocked_outputs()
                    set_state(STATE_LOCKING_UNLOCK_RELAY)
                else:
                    # Denied: flash red briefly and return to idle (still locked)
                    led_red.on(); time.sleep(1); led_red.off()
                    set_state(STATE_IDLE)
            elif (now() - auth_request_time) > AUTH_RESPONSE_TIMEOUT:
                # Server didn’t reply in time; report and go back to idle
                publish_data(client, {
                    'type': 'error', 'rack_id': DEVICE_ID,
                    'message': 'auth_timeout_lock', 'timestamp': now()
                })
                set_state(STATE_IDLE)

        elif state == STATE_LOCKING_UNLOCK_RELAY:
            # Relay is ON (unlocked). We wait until an object is detected near.
            present, dist = object_present()
            if present:
                # Bike is in position; move to the stage where we expect bike tag
                set_state(STATE_LOCKING_WAIT_OBJECT)
            elif (now() - state_entered_at) > LOCK_FLOW_TIMEOUT:
                # User didn’t place the bike in time; close and inform server
                set_locked_outputs()
                publish_data(client, {
                    'type': 'lock_timeout', 'rack_id': DEVICE_ID, 'timestamp': now()
                })
                set_state(STATE_IDLE)
            else:
                # Keep unlocked to let the user try
                set_unlocked_outputs()

        elif state == STATE_LOCKING_WAIT_OBJECT:
            # Blink green as guidance: “present bike tag now”
            blink_led_nonblocking(led_green, period=0.6)
            present, dist = object_present()
            if present:
                if rfid:
                    # Treat this scan as the bike RFID and finalize lock
                    current_bike_id = rfid
                    publish_data(client, {
                        'type': 'lock',
                        'rack_id': DEVICE_ID,
                        'bike_id': current_bike_id,
                        'user_id': pending_auth_user,
                        'timestamp': now()
                    })
                    set_locked_outputs()
                    pending_auth_user = None
                    # Go back to idle
                    set_state(STATE_IDLE)
            elif (now() - state_entered_at) > LOCK_FLOW_TIMEOUT:
                # Took too long with no valid placement/tag
                set_locked_outputs()
                publish_data(client, {
                    'type': 'error', 'rack_id': DEVICE_ID,
                    'message': 'lock_flow_timeout', 'timestamp': now()
                })
                # Clear user info NOTE: we keep bike_id for the rack to know what it hosts
                
                set_state(STATE_IDLE)

        # =============== Unlocking Flow ===============================
        elif state == STATE_AUTH_UNLOCKING:
            # Waiting for server approval to unlock (bike removal)
            if not awaiting_auth and last_auth_result:
                if last_auth_result.get('reply') == 'accept':
                    # Grant access: unlock so the user can remove the bike
                    set_unlocked_outputs()
                    set_state(STATE_UNLOCKING_RELAY_ON)
                else:
                    # Denied: brief red flash; remain locked
                    led_red.on(); time.sleep(1); led_red.off()
                    set_state(STATE_IDLE)
            elif (now() - auth_request_time) > AUTH_RESPONSE_TIMEOUT:
                # No server response; report and stay locked
                publish_data(client, {
                    'type': 'error', 'rack_id': DEVICE_ID,
                    'message': 'auth_timeout_unlock', 'timestamp': now()
                })
                set_state(STATE_IDLE)

        elif state == STATE_UNLOCKING_RELAY_ON:
            # Relay ON; wait for the wheel to move far enough (bike removed)
            present, dist = object_present()
            if not present:
                # Bike seems removed; now require user to re-scan to confirm re-lock
                set_state(STATE_UNLOCKING_WAIT_RELOCK)
            elif (now() - state_entered_at) > UNLOCK_FLOW_TIMEOUT:
                # User didn’t remove in time -> visual warning, then leave unlocked
                for _ in range(6):
                    led_green.on(); led_red.on(); time.sleep(0.2)
                    led_green.off(); led_red.off(); time.sleep(0.2)
                # Design choice: remain unlocked to avoid trapping
                set_unlocked_outputs()
                set_state(STATE_IDLE)

        elif state == STATE_UNLOCKING_WAIT_RELOCK:
            # Green blinks to ask the user to tap again to confirm re-lock
            blink_led_nonblocking(led_green, period=0.4)
            if rfid:
                # Finalize: lock, notify server, clear bike association
                set_locked_outputs()
                publish_data(client, {
                    'type': 'unlock', 'rack_id': DEVICE_ID,
                    'bike_id': current_bike_id,
                    'user_id': pending_auth_user,
                    'timestamp': now()
                })
                # Clear user and bike info
                current_bike_id = None
                pending_auth_user = None
                set_state(STATE_IDLE)
            elif (now() - state_entered_at) > RELOCK_CONFIRM_TIMEOUT:
                # User walked away without confirming; warn, then lock for safety
                for _ in range(6):
                    led_green.on(); led_red.on(); time.sleep(0.2)
                    led_green.off(); led_red.off(); time.sleep(0.2)
                # Note: This behavior can be revisited (TODO marker kept intentionally)
                set_locked_outputs()  # TODO: Review desired behavior with ops
                set_state(STATE_IDLE)

        elif state == STATE_ERROR:
            # Generic error: blink both LEDs. System can be reset or recover.
            blink_led_nonblocking(led_red, period=0.6)
            blink_led_nonblocking(led_green, period=0.6)
            pass

        # Small delay to avoid hogging the CPU (keeps loop responsive)
        time.sleep(0.05)

except KeyboardInterrupt:
    # Graceful shutdown on Ctrl+C during development
    print("\nProcess stopped by user")
    debug_print("Process interrupted by user")

except Exception as e:
    # Top-level catch: indicate fault and keep red ON
    print(f"Unexpected error: {e}")
    debug_print(f"Unexpected error: {e}")
    led_red.on()

finally:
    # Always try to clean up networking and outputs
    print("Cleaning up...")
    debug_print("Starting cleanup")
    try:
        if client:
            disconnect_msg = {
                'type': 'disconnect',
                'rack_id': DEVICE_ID,
                'timestamp': time.time()
            }
            publish_data(client, disconnect_msg)
            client.disconnect()
            print("Disconnected from MQTT broker")
            debug_print("MQTT client disconnected")
    except Exception as e:
        print(f"Error during cleanup: {e}")
        debug_print(f"Cleanup error: {e}")

    # Ensure hardware is left in a safe, silent state
    led_red.off()
    led_green.off()
    relay_off()
    debug_print("LEDs and relay turned off")
    print("System shutdown complete")
