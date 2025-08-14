from flask import Flask, render_template, request, flash, Response, stream_with_context, url_for
from pymongo import MongoClient
import paho.mqtt.client as mqtt
import os, queue, threading
from bson import ObjectId
import ssl

app = Flask(__name__)
app.secret_key = "dev"

# Configuration
mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
mqtt_broker = os.environ.get("MQTT_BROKER", "localhost")
mqtt_port = int(os.environ.get("MQTT_PORT", 1883))

client = MongoClient(mongo_url)
db = client.smartpedals
collection = db.data

# SSE (server-sent event) setup
#listeners = set()
listeners = []
LISTENER_QUEUE_SIZE = 1

def publish_ping():
    dead = []
    for q in listeners:
        try:
            q.put_nowait("ping")
        except queue.Full:
            pass
        except Exception:
            dead.append(q)
    for q in dead:
        #listeners.discard(q)
        try:
            listeners.remove(q)
        except ValueError:
            pass

def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    client.subscribe("sensors/#")

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        # If deleted
        if db is None or collection is None:
            print("[MQTT] DB not ready")
            return
        collection.insert_one({"topic": msg.topic, "payload": payload})
        publish_ping()
        print(f"Stored: {msg.topic} -> {payload}")
    except Exception as e:
        print(f"[MQTT] Error in on_message: {e}")

#mqtt_client = mqtt.Client() # No sec
mqtt_client = mqtt.Client(client_id="trusted-hepl_smartPedals") # Prefix id
mqtt_client.username_pw_set(username="smartadmin", password="smartpass") # User password
#mqtt_client.tls_set(ca_certs="/etc/ssl/ca.crt", tls_version=ssl.PROTOCOL_TLSv1_2) # Tls encrypt
mqtt_client.tls_set(ca_certs="/etc/ssl/ca.crt", certfile="/etc/ssl/client-mqtt.crt", keyfile="/etc/ssl/client-mqtt.key.unlocked", tls_version=ssl.PROTOCOL_TLSv1_2) # Tls encrypt and auth
mqtt_client.tls_insecure_set(True) # Don't look the hostname (look for mqtt but hepl.local is given)

mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

mqtt_client.connect(mqtt_broker, mqtt_port, 60)
mqtt_client.loop_start()

@app.route("/smartpedals/stream")
def stream():
    q = queue.Queue(maxsize=LISTENER_QUEUE_SIZE)
    #listeners.add(q)
    listeners.append(q)
    def gen():
        try:
            yield ": connected\n\n"
            while True:
                _ = q.get()
                yield "data: ping\n\n"
        finally:
            #listeners.discard(q)
            try:
                listeners.remove(q)
            except ValueError:
                pass
    return Response(stream_with_context(gen()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache"})

@app.route("/smartpedals/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "clear_all":
            #collection.delete_many({})
            try:
                ids = collection.distinct("_id")
                for entry_id in ids:
                    collection.delete_one({"_id": entry_id})
                flash("All data deleted!")
            except Exception as e:
                flash(f"Error during deletion: {e}")
        elif action == "delete_selected":
            selected_ids = request.form.getlist('entry_checkbox')
            for entry_id in selected_ids:
                try:
                    collection.delete_one({"_id": ObjectId(entry_id)})
                except Exception as e:
                    flash(f"Delete error: {e}")
            flash("Selected entries deleted!")
    data = list(collection.find({}, {"_id": 1, "topic": 1, "payload": 1}))
    return render_template("index.html", data=data)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0")
