import os
import pymongo
import paho.mqtt.client as mqtt

def get_env_variable(var_name, default=None):
    """Retrieve environment variables or return default."""
    value = os.getenv(var_name, default)
    if value is None:
        raise EnvironmentError(f"Environment variable {var_name} is not set.")
    return value

# Retrieve MongoDB connection details from environment variables
mongo_host = get_env_variable("MONGO_HOST", "localhost")
mongo_port = int(get_env_variable("MONGO_PORT", 27017))
mongo_db = get_env_variable("MONGO_DB", "testdb")
mongo_user = get_env_variable("MONGO_USER", None)
mongo_password = get_env_variable("MONGO_PASSWORD", None)

# Retrieve MQTT broker details from environment variables
mqtt_broker = get_env_variable("MQTT_BROKER", "localhost")
mqtt_port = int(get_env_variable("MQTT_PORT", 1883))
mqtt_topic = get_env_variable("MQTT_TOPIC", "test/topic")
mqtt_username = get_env_variable("MQTT_USERNAME", None)
mqtt_password = get_env_variable("MQTT_PASSWORD", None)

# Connect to MongoDB
def connect_to_mongodb():
    """Connect to MongoDB using pymongo."""
    try:
        if mongo_user and mongo_password:
            uri = f"mongodb://{mongo_user}:{mongo_password}@{mongo_host}:{mongo_port}/{mongo_db}"
        else:
            uri = f"mongodb://{mongo_host}:{mongo_port}/{mongo_db}"

        client = pymongo.MongoClient(uri)
        db = client[mongo_db]
        print("Connected to MongoDB successfully.")
        return db
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        raise

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    """Callback for when the client receives a CONNACK response from the server."""
    if rc == 0:
        print("Connected to MQTT Broker!")
        client.subscribe(mqtt_topic)
    else:
        print(f"Failed to connect, return code {rc}")

def on_message(client, userdata, msg):
    """Callback for when a PUBLISH message is received from the server."""
    print(f"Received message from topic {msg.topic}: {msg.payload.decode()}")

# Connect to MongoDB
db = connect_to_mongodb()

# Connect to MQTT Broker
mqtt_client = mqtt.Client()

# Set MQTT credentials if provided
if mqtt_username and mqtt_password:
    mqtt_client.username_pw_set(mqtt_username, mqtt_password)

try:
    mqtt_client.connect(mqtt_broker, mqtt_port)
    mqtt_client.loop_forever()
except Exception as e:
    print(f"Failed to connect to MQTT Broker: {e}")

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message
