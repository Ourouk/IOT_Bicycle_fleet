import os
import time
import paho.mqtt.client as mqtt
from pymongo import MongoClient
from datetime import datetime

# Configuration MQTT
BROKER_IP = os.getenv("BROKER_IP", "localhost")
BROKER_PORT = int(os.getenv("BROKER_PORT", 1883))
MQTT_TOPIC = os.getenv("MQTT_TOPIC", "HEPL/M18/test")

# Configuration MongoDB
MONGO_IP = os.getenv("MONGO_IP", "localhost")
MONGO_PORT = int(os.getenv("MONGO_PORT", 27017))
DB_NAME = "iot_data"
COLLECTION_NAME = "mqtt_messages"

# Fonction pour insérer un message dans MongoDB
def insert_message_to_mongo(message):
    try:
        client = MongoClient(f"mongodb://{MONGO_IP}:{MONGO_PORT}")
        db = client[DB_NAME]
        collection = db[COLLECTION_NAME]

        # Préparer le document à insérer
        document = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "message": message
        }

        # Insérer dans MongoDB
        collection.insert_one(document)
        print(f"Message inséré dans MongoDB: {document}")

    except Exception as e:
        print(f"Erreur lors de l'insertion dans MongoDB: {e}")

# Callback lorsque le client se connecte au broker MQTT
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("Connecté au broker MQTT")
        client.subscribe(MQTT_TOPIC)
    else:
        print(f"Erreur de connexion au broker MQTT, code: {rc}")

# Callback lorsqu'un message est reçu
def on_message(client, userdata, msg):
    print(f"Message reçu sur le topic {msg.topic}: {msg.payload.decode()}")
    insert_message_to_mongo(msg.payload.decode())

# Initialisation et connexion MQTT
def main():
    print("Initialisation du client MQTT...")
    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    try:
        # Connexion au broker MQTT
        mqtt_client.connect(BROKER_IP, BROKER_PORT, 60)
    except Exception as e:
        print(f"Erreur lors de la connexion au broker MQTT: {e}")
        return

    # Démarrer la boucle pour écouter les messages MQTT
    mqtt_client.loop_start()

    # Garder le script en vie
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Arrêt du script...")
    finally:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()

if __name__ == "__main__":
    main()
