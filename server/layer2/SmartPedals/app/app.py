import os
import paho.mqtt.client as paho
import time

# Récupération des variables d'environnement
ip_broker = os.getenv("BROKER_IP", "test.mosquitto.org")
port_broker = int(os.getenv("BROKER_PORT", 1883))
mqtt_topic = os.getenv("MQTT_TOPIC", "HEPL/M18/test")
payload = '{"message":"Hello World"}'

def publisher(payload):
    client = paho.Client()
    client.on_connect = on_connect
    client.on_publish = on_publish
    client.loop_start()
    client.connect(ip_broker, port_broker, 60)
    time.sleep(2)
    client.disconnect()
    client.loop_stop()

def on_connect(client, userdata, flags, rc):
    client.publish(mqtt_topic, payload, 0)
    print("Message envoyé : " + payload)

def on_publish(client, userdata, mid):
    print("Message reçu")

if __name__ == "__main__":
    publisher(payload)
