import time
from machine import Pin
import dht
import network
from umqtt.simple import MQTTClient

# WiFi credentials
SSID = 'RPiStation01'
PASSWORD = 'RPiStation01'

# Configure your static IP address, subnet mask, gateway, and DNS
STATIC_IP = '192.199.1.101'
SUBNET_MASK = '255.255.255.0'
GATEWAY = '192.199.1.2'

# MQTT configuration
MQTT_SERVER = '192.199.1.110'
CLIENT_ID = 'ESP32_DHT11'
TOPIC_PUB = b'home/dht11'

# Sensor configuration for DHT11
SENSOR_PIN = 2
d = dht.DHT11(Pin(SENSOR_PIN))

# LED configuration
LED_PIN = 0
led = Pin(LED_PIN, Pin.OUT)

# Initialize WiFi connection
def connect_wifi(ssid, password):
    sta = network.WLAN(network.STA_IF)
    if not sta.isconnected():
        print('Connecting to WiFi...')
        sta.active(True)
        # sta.ifconfig((STATIC_IP, SUBNET_MASK, GATEWAY))
        sta.connect(ssid, password)
        # Timeout loop with a limit to avoid infinite loops
        timeout = 10  # 10 seconds timeout
        while not sta.isconnected() and timeout > 0:
            time.sleep(1)
            timeout -= 1
            print(f'Attempting to connect... ({10 - timeout}/10)')
        if not sta.isconnected():
            raise RuntimeError('Could not connect to WiFi')
    print('Network connected:', sta.ifconfig())
    return sta

# Connect to MQTT broker
def connect_mqtt(client_id, server):
    try:
        client = MQTTClient(client_id, server)
        client.connect()
        print(f'Connected to MQTT broker at {server}')
        return client
    except Exception as e:
        print(f'Failed to connect to MQTT broker: {e}')
        return None

# Publish sensor data to MQTT
def publish_data(client, temperature, humidity):
    try:
        payload = '{"temperature": %s, "humidity": %s}' % (temperature, humidity)
        client.publish(TOPIC_PUB, payload)
        print('Data sent:', payload)
    except Exception as e:
        print(f'Failed to publish data: {e}')
        return None

# Main logic
try:
    #Wifi Logic
    # Connect to WiFi
    sta = connect_wifi(SSID,PASSWORD)
    # Set static IP configuration
    sta.ifconfig((STATIC_IP, SUBNET_MASK, GATEWAY))

    # Connect to the MQTT broker
    client = connect_mqtt(CLIENT_ID, MQTT_SERVER)

    # Main loop to read sensor data and publish it
    while True:
        try:
            d.measure()
            temperature = d.temperature()
            humidity = d.humidity()

            print(f'Temperature: {temperature} °C')
            print(f'Humidity: {humidity} %')

            # Send data via MQTT
            if client is not None:
                publish_data(client, temperature, humidity)

                # Turn off the LED when connected to MQTT
                led.off()
            else:
                # Turn on the LED when not connected to MQTT
                led.on()

            # Turn off the LED when connected to MQTT
            led.off()

        except OSError as e:
            print(f'Failed to read sensor data: {e}')

            # Turn on the LED when not connected to MQTT
            led.on()

        # Wait for 30 seconds
        time.sleep(30)

        # Reconnect to MQTT every 2 minutes
        if time.time() % 120 < 30:
            try:
                client.disconnect()
                print('Disconnected from MQTT broker')
            except Exception as e:
                print(f'Failed to disconnect MQTT client: {e}')

            client = connect_mqtt(CLIENT_ID, MQTT_SERVER)

except KeyboardInterrupt:
    print('Process stopped by user')

finally:
    try:
        # client.disconnect()
        print('Disconnected from MQTT broker')
    except Exception as e:
        print(f'Failed to disconnect MQTT client: {e}')

    # Turn off the LED when the script ends
    led.off()