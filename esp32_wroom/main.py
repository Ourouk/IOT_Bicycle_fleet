import time
from machine import Pin
import network
from umqtt.simple import MQTTClient

# WiFi credentials
SSID = "RPiStation01"
PASSWORD = "RPiStation01"

# MQTT configuration
MQTT_SERVER = "192.199.2.254"
CLIENT_ID = "b01"
TOPIC_PUB = b"station/bornes/replies"
TOPIC_SUB = b"station/bornes"

# Initialize LEDs
led_red = Pin(2, Pin.OUT)
led_green = Pin(3, Pin.OUT)

# Initialize WiFi connection using DHCP
def connect_wifi(ssid, password):
    sta = network.WLAN(network.STA_IF)
    sta.active(True)
    if not sta.isconnected():
        print("Connecting to WiFi...")
        sta.connect(ssid, password)
        timeout = 10  # 10 seconds timeout
        while not sta.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
            print(f"Attempting to connect... ({10 - timeout}/10)")
        if not sta.isconnected():
            raise RuntimeError("Could not connect to WiFi")
    print("Network connected:", sta.ifconfig())
    return sta

# Connect to MQTT broker
def connect_mqtt(client_id, server):
    try:
        client = MQTTClient(client_id, server)
        client.connect()
        print(f"Connected to MQTT broker at {server}")
        return client
    except Exception as e:
        print(f"Failed to connect to MQTT broker: {e}")
        return None

# Publish data to MQTT
def publish_data(client):
    try:
        payload = '{"status": "active"}'
        client.publish(TOPIC_PUB, payload)
        print("Data sent:", payload)
    except Exception as e:
        print(f"Failed to publish data: {e}")

# Main logic
client = None  # Initialize client as None
try:
    # Connect to WiFi
    sta = connect_wifi(SSID, PASSWORD)

    # Connect to the MQTT broker
    client = connect_mqtt(CLIENT_ID, MQTT_SERVER)

    # Main loop to publish data
    last_mqtt_reconnect_time = 0
    while True:
        try:
            # Send data via MQTT
            if client is not None:
                publish_data(client)
                led_red.off()  # Turn off the red LED when connected to MQTT
            else:
                led_red.on()  # Turn on the red LED when not connected to MQTT

            # Reconnect to MQTT every 2 minutes
            if time.time() - last_mqtt_reconnect_time >= 120:
                try:
                    if client is not None:
                        client.disconnect()
                        print("Disconnected from MQTT broker")
                except Exception as e:
                    print(f"Failed to disconnect MQTT client: {e}")
                client = connect_mqtt(CLIENT_ID, MQTT_SERVER)
                last_mqtt_reconnect_time = time.time()

        except Exception as e:
            print(f"An error occurred: {e}")
            led_red.on()  # Turn on the red LED when there's an error

        # Wait for 30 seconds
        time.sleep(30)

except KeyboardInterrupt:
    print("Process stopped by user")

finally:
    try:
        if client is not None:
            client.disconnect()
            print("Disconnected from MQTT broker")
    except Exception as e:
        print(f"Failed to disconnect MQTT client: {e}")

    # Turn off the LED when the script ends
    led_red.off()
