import os
import random
import ssl
import threading
import asyncio
import queue

from flask import (
    Flask, render_template, request,
    redirect, url_for, flash, session,
    Response, stream_with_context, jsonify
)
import paho.mqtt.publish as publish
import paho.mqtt.client as mqtt
from twilio.rest import Client as TwilioClient
from webex_bot.webex_bot import WebexBot
from webex_bot.commands.echo import EchoCommand

app = Flask(__name__)
app.secret_key = "dev"

# MQTT secure config
MQTT_BROKER = os.environ.get("MQTT_BROKER", "hepl.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_TOPIC_BASE = "sensors"
MQTT_USERNAME = "smartadmin"
MQTT_PASSWORD = "smartpass"
MQTT_CLIENT_ID_SUB = "trusted-enterprise_smartPedals_sub" # Two different, publish.single() keep the connection a little -> broker kill the old
MQTT_CLIENT_ID_PUB = "trusted-enterprise_smartPedals_pub"

# Certs path (must be available inside container)
CA_CERT = "/etc/ssl/ca.crt"
CLIENT_CERT = "/etc/ssl/enterprise-mqtt.crt"
CLIENT_KEY = "/etc/ssl/enterprise-mqtt.key.unlocked"

received_messages = []
mqtt_client = None
mqtt_thread = None
mqtt_lock = threading.Lock()

# Twilio & Webex
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "")
TARGET_NUMBER = os.environ.get("TARGET_NUMBER", "")

# https://developer.webex.com/messaging/docs/api/v1/rooms/list-rooms
WEBEX_TOKEN = os.environ.get("WEBEX_ACCESS_TOKEN", "")
WEBEX_ROOM_ID = os.environ.get("WEBEX_ROOM_ID", "")

twilio = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
bot = WebexBot(teams_bot_token=WEBEX_TOKEN, approved_rooms=[WEBEX_ROOM_ID], include_demo_commands=False)
bot.add_command(EchoCommand())

def run_bot():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    bot.run()
webex_thread = threading.Thread(target=run_bot, daemon=True)
webex_thread.start()

# Publish random test data
def publish_sensor_data():
    data = {
        "temperature": round(random.uniform(20, 23), 2),
        "humidity": round(random.uniform(3, 6), 2),
        "light": round(random.uniform(50, 60), 2),
        "message": "Test sensor data sent via Flask"
    }

    publish.single(
        f"{MQTT_TOPIC_BASE}/test",
        payload=str(data),
        hostname=MQTT_BROKER,
        port=MQTT_PORT,
        client_id=MQTT_CLIENT_ID_PUB,
        auth={'username': MQTT_USERNAME, 'password': MQTT_PASSWORD},
        tls={
            'ca_certs': CA_CERT,
            'certfile': CLIENT_CERT,
            'keyfile': CLIENT_KEY,
            'tls_version': ssl.PROTOCOL_TLSv1_2,
            'cert_reqs': ssl.CERT_REQUIRED
        }
    )

    return data

# MQTT callbacks
def on_connect(client, userdata, flags, rc, properties=None):
    #print(f"[MQTT] CONNECT result: {rc}")
    if rc == 0:
        client.subscribe(f"{MQTT_TOPIC_BASE}/#")
        print("[MQTT] Connected and subscribed.")
    else:
        print("[MQTT] Connection failed with code", rc)

def on_message(client, userdata, msg):
    #print(f"[MQTT] Received on {msg.topic}: {msg.payload.decode()}")
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

        mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID_SUB, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)

        mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
        mqtt_client.tls_set(
            ca_certs=CA_CERT,
            certfile=CLIENT_CERT,
            keyfile=CLIENT_KEY,
            tls_version=ssl.PROTOCOL_TLSv1_2
        )
        mqtt_client.tls_insecure_set(True)  # Hostname is not the service name

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

# SSE mechanics for chat updates
events_listeners = []
LISTENER_QUEUE_SIZE = 1

def publish_ping():
    dead = []
    for q in events_listeners:
        try:
            q.put_nowait("ping")
        except queue.Full:
            pass
        except Exception:
            dead.append(q)
    for q in dead:
        try:
            events_listeners.remove(q)
        except ValueError:
            pass

@app.route('/smartpedals/chat/stream')
def chat_stream():
    def gen():
        q = queue.Queue(maxsize=LISTENER_QUEUE_SIZE)
        events_listeners.append(q)
        yield ': connected\n\n'
        try:
            while True:
                _ = q.get()
                yield 'data: update\n\n'
        finally:
            try:
                events_listeners.remove(q)
            except ValueError:
                pass
    return Response(stream_with_context(gen()), mimetype='text/event-stream', headers={'Cache-Control':'no-cache'})

@app.route("/smartpedals/chat")
def chat():
    # Twilio notification once per session
    if not session.get("chat_notified"):
        twilio.messages.create(body=f"[CHAT] Connection on {request.host}", from_=TWILIO_NUMBER, to=TARGET_NUMBER)
        session["chat_notified"] = True
    # render HTML; messages loaded via SSE+fetch
    return render_template("chat.html", messages=[])

@app.route("/smartpedals/chat/messages")
def chat_messages():
    # Last 20 webex messages
    msgs = list(bot.teams.messages.list(roomId=WEBEX_ROOM_ID, max=20))
    history = [{"who": m.personEmail, "text": m.text or ''} for m in msgs]
    return jsonify(history)

@app.route("/smartpedals/chat/send", methods=["POST"])
def chat_send():
    msg = request.form["message"]
    bot.teams.messages.create(roomId=WEBEX_ROOM_ID, text=msg)

    # Ping listners after a slight delay to allow Webex to register
    threading.Timer(1, publish_ping).start()
    return redirect(url_for("chat"))

if __name__=="__main__":
    start_subscriber()
    app.run(debug=True, host="0.0.0.0")
