from flask import Flask, render_template, request, redirect, url_for, flash
import paho.mqtt.publish as publish
import paho.mqtt.client as mqtt
import random
import threading

app = Flask(__name__)
app.secret_key = 'dev'

MQTT_BROKER = "hepl.local"
MQTT_PORT = 1883
MQTT_TOPIC_BASE = "sensors"
received_messages = []

mqtt_client = None
mqtt_thread = None
mqtt_lock = threading.Lock()

# Publish random test data
def publish_sensor_data():
    data = {
        "temperature": round(random.uniform(20, 23), 2),
        "humidity": round(random.uniform(3, 6), 2),
        "light": round(random.uniform(50, 60), 2),
        "message": "Test sensor data sent via Flask"
    }
    publish.single(f"{MQTT_TOPIC_BASE}/test", payload=str(data), hostname=MQTT_BROKER, port=MQTT_PORT, client_id="trusted-enterprise_smartPedals")
    return data

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        client.subscribe(f"{MQTT_TOPIC_BASE}/#")
        print("[MQTT] Connected and subscribed.")
    else:
        print("[MQTT] Connection failed with code", rc)

def on_message(client, userdata, msg):
    if len(received_messages) >= 10:
        received_messages.pop(0)
    received_messages.append(f"{msg.topic}: {msg.payload.decode()}")

# Start/restart subscriber
def start_subscriber():
    global mqtt_client

    with mqtt_lock:
        if mqtt_client:
            try:
                mqtt_client.disconnect()
            except:
                pass

        mqtt_client = mqtt.Client(client_id="trusted-enterprise_smartPedals", callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        mqtt_client.on_connect = on_connect
        mqtt_client.on_message = on_message
        mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)

        def loop():
            try:
                mqtt_client.loop_forever()
            except Exception as e:
                print("[MQTT] loop_forever exception:", e)

        global mqtt_thread
        mqtt_thread = threading.Thread(target=loop, daemon=True)
        mqtt_thread.start()

# Initial start
start_subscriber()

# Flask routes
@app.route("/smartpedals/")
def index():
    return render_template("index.html", messages=received_messages)

@app.route("/smartpedals/send", methods=["POST"])
def send():
    data = publish_sensor_data()
    flash(f"Data sent: {data}")
    return redirect(url_for("index"))

@app.route("/smartpedals/reconnect", methods=["POST"])
def reconnect():
    start_subscriber()
    flash("MQTT subscriber reconnected.")
    return redirect(url_for("index"))

if __name__=="__main__":
    start_subscriber()
    app.run(debug=True, host="0.0.0.0")
