import os
import queue
import threading
import time
import ssl
import re
import json

from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from functools import wraps # For Flask decorators
from flask import (
    Flask, render_template, request, flash,
    Response, stream_with_context, url_for, redirect,
    jsonify, abort
)
from pymongo import MongoClient
import paho.mqtt.client as mqtt
from bson import ObjectId
from twilio.rest import Client as TwilioClient

# Flask
FLASK_TLS_CERT = os.environ.get("FLASK_TLS_CERT", "/etc/ssl/client-flask.crt")
FLASK_TLS_KEY = os.environ.get("FLASK_TLS_KEY",  "/etc/ssl/client-flask.key.unlocked")
FLASK_TLS_PORT = int(os.environ.get("FLASK_TLS_PORT", "8443"))
SMARTPEDALS_API_KEY = os.environ.get("SMARTPEDALS_API_KEY", "changeme")

# MongoDB
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

# MQTT (local / secured)
MQTT_BROKER = os.environ.get("MQTT_BROKER", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", 1883))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "smartadmin")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "smartpass")
MQTT_CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "trusted-hepl_smartPedals")

# TLS certs (paths must exist in container/host)
MQTT_CA_CERT = os.environ.get("MQTT_CA_CERT", "/etc/ssl/ca.crt")
MQTT_CLIENT_CERT = os.environ.get("MQTT_CLIENT_CERT", "/etc/ssl/client-mqtt.crt")
MQTT_CLIENT_KEY = os.environ.get("MQTT_CLIENT_KEY", "/etc/ssl/client-mqtt.key.unlocked")

# External MQTT (test.mosquitto.org)
EXT_MQTT_BROKER = os.environ.get("EXT_MQTT_BROKER", "test.mosquitto.org")
EXT_MQTT_PORT = int(os.environ.get("EXT_MQTT_PORT", 8884))
EXT_MQTT_CLIENT_ID = os.environ.get("EXT_MQTT_CLIENT_ID", "smartPedals-ext-disponibilities")
EXT_MQTT_CA_CERT = os.environ.get("EXT_MQTT_CA_CERT", "/etc/ssl/testmosquitto/mosquitto.org.crt")
EXT_MQTT_CLIENT_CERT = os.environ.get("EXT_MQTT_CLIENT_CERT", "/etc/ssl/testmosquitto/client.crt")
EXT_MQTT_CLIENT_KEY = os.environ.get("EXT_MQTT_CLIENT_KEY", "/etc/ssl/testmosquitto/client.key")

# OpenWeatherMap
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")
DEFAULT_CITY = os.environ.get("DEFAULT_CITY", "Angleur")
OPENWEATHER_LANG = os.environ.get("OPENWEATHER_LANG", "en")

# Mailtrap
MAILTRAP_TOKEN = os.environ.get("MAILTRAP_TOKEN", "")
MAILTRAP_EMAIL = os.environ.get("MAILTRAP_EMAIL", "")
MAILTRAP_CAT = os.environ.get("MAILTRAP_CAT", "end-user")

# Twilio (SMS notifications)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "")
TARGET_NUMBER = os.environ.get("TARGET_NUMBER", "")
ZERO_ALERT_SECONDS = int(os.environ.get("ZERO_ALERT_SECONDS", 15 * 60)) # Send message after 15

# Webex
WEBEX_API_BASE = os.environ.get("WEBEX_API_BASE", "https://webexapis.com/v1")
WEBEX_ACCESS_TOKEN = os.environ.get("WEBEX_ACCESS_TOKEN", "")
SUPPORT_DEFAULT_TITLE = os.environ.get("SUPPORT_DEFAULT_TITLE", "Support - HEPL")

# Shodan
SHODAN_API_BASE = os.environ.get("SHODAN_API_BASE", "https://api.shodan.io")
SHODAN_API_KEY = os.environ.get("SHODAN_API_KEY", "")

# SSE (Server-Sent Events)
events_listeners = []
LISTENER_QUEUE_SIZE = 10

# Other
BRUSSELS = ZoneInfo("Europe/Brussels")
AUTH_MAX_SKEW_SECONDS = int(os.environ.get("AUTH_MAX_SKEW_SECONDS", "120"))
# External topic cache
latest_disponibilities = None
latest_disponibilities_count = None
# Twilio
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
# Zero-availability alert state
zero_since_ts = None
zero_alert_sent = False

"""
Initialization and helpers
"""

# App
app = Flask(__name__)
app.secret_key = "dev"

# API Key
def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("x-api-key")
        if not api_key or api_key != SMARTPEDALS_API_KEY:
            abort(401)  # Unauthorized
        return f(*args, **kwargs)
    return decorated

# Initialize MongoDB
def init_db():
    client = MongoClient(MONGO_URL)
    db = client.smartpedals
    data_col = db.data
    users_col = db.users
    bikes_col = db.bikes
    racks_col = db.racks
    stations_col = db.stations
    locations_col = db.locations
    return client, db, data_col, users_col, bikes_col, racks_col, stations_col, locations_col

client, db, data_col, users_col, bikes_col, racks_col, stations_col, locations_col = init_db()

# SSE (Server-Sent Events)
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

@app.route("/smartpedals/stream")
def stream():
    q = queue.Queue(maxsize=LISTENER_QUEUE_SIZE)
    events_listeners.append(q)
    def gen():
        # notify client on connect and on every new message
        yield ": connected\n\n"
        try:
            while True:
                _ = q.get()
                yield "data: ping\n\n"
        finally:
            # cleanup listener on disconnect
            try:
                events_listeners.remove(q)
            except ValueError:
                pass
    return Response(stream_with_context(gen()), mimetype="text/event-stream", headers={"Cache-Control": "no-cache"})

# Insert MQTT messages inside the mongodb
def insert_to_mongo(topic, payload):
    try:
        temp_client = MongoClient(MONGO_URL)
        temp_db = temp_client.smartpedals
        temp_data_col = temp_db.data
        result = temp_data_col.insert_one({"topic": topic, "payload": payload})
        print(f"[MQTT] Inserted: {result.inserted_id}")
        publish_ping()
        temp_client.close()
    except Exception as e:
        print(f"[MQTT] Error during insert: {e}")

# Send email via Mailtrap
def send_mailtrap_email(to_email: str, subject: str, text: str, to_name: str | None = None) -> bool:
    try:
        if not MAILTRAP_TOKEN or not MAILTRAP_EMAIL:
            app.logger.warning("[MAILTRAP] Missing MAILTRAP_TOKEN or MAILTRAP_EMAIL")
            return False

        headers = {
            "Authorization": f"Bearer {MAILTRAP_TOKEN}",
            "Content-Type": "application/json",
        }
        payload = {
            "from": {"email": MAILTRAP_EMAIL, "name": "SmartPedals"},
            "to": [{"email": to_email, "name": (to_name or "User")}],
            "subject": subject,
            "text": text,
            "category": MAILTRAP_CAT,
        }

        resp = requests.post(
            "https://send.api.mailtrap.io/api/send",
            headers=headers,
            json=payload,
            timeout=5,
        )
        if 200 <= resp.status_code < 300:
            app.logger.info(f"[MAILTRAP] Email sent to {to_email}: {subject}")
            return True
        else:
            app.logger.warning(f"[MAILTRAP] Send failed {resp.status_code}: {resp.text}")
            return False
    except Exception as e:
        app.logger.exception(f"[MAILTRAP] Exception while sending email: {e}")
        return False

# Twilio message
def twilio_send_sms(body: str) -> bool:
    try:
        msg = twilio_client.messages.create(
            from_=TWILIO_NUMBER,
            to=TARGET_NUMBER,
            body=body
        )
        print(f"[TWILIO] SMS sent: sid={msg.sid}")
        return True
    except Exception as e:
        print(f"[TWILIO] Send error: {e}")
        return False

"""
MQTT local secured for authentication (+email) and location messages AND external MQTT for disponibilities (+twilio sms)
"""

# Handle authentication messages
def handle_auth_message(mqtt_client_instance, payload):
    reply_topic = "hepl/auth_reply"
    now = datetime.now(BRUSSELS)
    now_iso = now.isoformat(timespec="seconds")

    def send_deny(reason=None):
        reply = {
            "user_id": user_id,
            "action": action,
            "rack_id": rack_id,
            "timestamp": now_iso,
            "type": "auth_response",
            "station_id": station_id if 'station_id' in locals() else None,
            "reply": "deny"
        }
        mqtt_client_instance.publish(reply_topic, json.dumps(reply), qos=2, retain=False)
        if reason:
            app.logger.info(f"[AUTH] {reason} → deny")

    try:
        data = json.loads(payload)
        user_id = data.get("user_id")
        bike_id = data.get("bike_id")
        rack_id = data.get("rack_id")
        action  = data.get("type") or data.get("action")
        ts_str  = data.get("timestamp")

        # Check if all required fields are present
        if not all([user_id, bike_id, rack_id, action, ts_str]):
            return send_deny("Missing fields")

        # Check user
        user = users_col.find_one({"rfid": str(user_id)})
        if not user:
            return send_deny(f"Unknown user {user_id}")

        # Check bike
        bike_doc = bikes_col.find_one({"bike_id": str(bike_id)})
        if not bike_doc:
            return send_deny(f"No bike found in rack {rack_id}")

        # Check timestamp
        try:
            ts_msg = datetime.fromisoformat(str(ts_str))
            if ts_msg.tzinfo is None:
                ts_msg = ts_msg.replace(tzinfo=BRUSSELS)
            skew = abs((now - ts_msg).total_seconds())
        except Exception:
            return send_deny(f"Invalid timestamp format {ts_str}")

        if skew > AUTH_MAX_SKEW_SECONDS:
            return send_deny(f"Timestamp skew too large ({skew:.1f}s)")

        # Get rack and station_id for the reply
        rack_doc = racks_col.find_one({"rack_id": str(rack_id)})
        station_id = str(rack_doc.get("station_id")) if rack_doc else None

        # Action: unlock
        if action == "unlock":
            update_bike = bikes_col.update_one(
                {"bike_id": str(bike_id), "status": "available", "currentRack": str(rack_id)},
                {"$set": {"status": "in_use", "currentUser": str(user_id), "currentRack": None},
                "$push": {"history": {"action": "unlock", "user_id": str(user_id), "timestamp": now}}}
            )
            if update_bike.modified_count == 0:
                return send_deny(f"Unlock denied for user={user_id} bike={bike_id} rack={rack_id}")

            update_rack = racks_col.update_one(
                {"rack_id": str(rack_id), "currentBike": str(bike_id)},
                {"$set": {"currentBike": None},
                "$push": {"history": {"bike_id": str(bike_id), "action": "unlock", "timestamp": now}}}
            )
            if update_rack.modified_count == 0:
                bikes_col.update_one(
                    {"bike_id": str(bike_id), "status": "in_use", "currentUser": str(user_id), "currentRack": None},
                    {"$set": {"status": "available", "currentUser": None, "currentRack": str(rack_id)},
                    "$push": {"history": {"action": "unlock_rollback", "user_id": str(user_id), "timestamp": now}}}
                )
                return send_deny(f"Rack update failed for rack={rack_id} bike={bike_id} [rollback ok]")

            users_col.update_one(
                {"rfid": str(user_id)},
                {"$push": {"history": {"bike_id": str(bike_id), "action": "unlock", "timestamp": now}}}
            )
            reply = {
                "user_id": user_id,
                "action": action,
                "rack_id": rack_id,
                "timestamp": now_iso,
                "type": "auth_response",
                "station_id": station_id,
                "reply": "accept"
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply), qos=2, retain=False)
            app.logger.info(f"[AUTH] Unlock accepted for user={user_id} bike={bike_id} rack={rack_id}")
            publish_ping()

            try:
                # User email notification
                to_email = user.get("email") if isinstance(user, dict) else None
                first = user.get("firstName") if isinstance(user, dict) else None
                last  = user.get("lastName")  if isinstance(user, dict) else None
                full_name = f"{first} {last}".strip() if first or last else "User"

                if to_email:
                    subject = f"Bike {bike_id} unlocked"
                    text = (
                        f"Hello {full_name},\n\n"
                        f"Your bike {bike_id} has been unlocked at {now_iso}.\n"
                        f"Rack: {rack_id or 'n/a'}.\n"
                        f"Enjoy the ride!\n\n— HEPL Team"
                    )
                    send_mailtrap_email(to_email=to_email, subject=subject, text=text, to_name=full_name)
                else:
                    app.logger.warning(f"[MAILTRAP] No email for user {user_id}; skipping email")
            except Exception as e:
                app.logger.exception(f"[MAILTRAP] Error while preparing/sending unlock email: {e}")

            # If we reach here, the unlock was successful
            return

        # Action: lock
        if action == "lock":
            update_bike = bikes_col.update_one(
                {"bike_id": str(bike_id), "status": "in_use", "currentUser": str(user_id)},
                {"$set": {"status": "available", "currentUser": None, "currentRack": str(rack_id)},
                "$push": {"history": {"action": "lock", "user_id": str(user_id), "timestamp": now}}}
            )
            if update_bike.modified_count == 0:
                return send_deny(f"Lock denied for user={user_id} bike={bike_id} (not in use by this user)")

            update_rack = racks_col.update_one(
                {"rack_id": str(rack_id), "currentBike": None},
                {"$set": {"currentBike": str(bike_id)},
                "$push": {"history": {"bike_id": str(bike_id), "action": "lock", "timestamp": now}}}
            )
            if update_rack.modified_count == 0:
                bikes_col.update_one(
                    {"bike_id": str(bike_id), "status": "available", "currentUser": None, "currentRack": str(rack_id)},
                    {"$set": {"status": "in_use", "currentUser": str(user_id), "currentRack": None},
                    "$push": {"history": {"action": "lock_rollback", "user_id": str(user_id), "timestamp": now}}}
                )
                return send_deny(f"Rack busy/missing for rack={rack_id} bike={bike_id} [rollback ok]")

            users_col.update_one(
                {"rfid": str(user_id)},
                {"$push": {"history": {"bike_id": str(bike_id), "action": "lock", "timestamp": now}}}
            )
            reply = {
                "user_id": user_id,
                "action": action,
                "rack_id": rack_id,
                "timestamp": now_iso,
                "type": "auth_response",
                "station_id": station_id,
                "reply": "accept"
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply), qos=2, retain=False)
            app.logger.info(f"[AUTH] Lock accepted for user={user_id} bike={bike_id} rack={rack_id}")
            publish_ping()
            return

        # Unknown action
        return send_deny(f"Unknown action '{action}'")

    except Exception as e:
        app.logger.info(f"[AUTH] Error: {e}")
        try:
            reply = {
                "user_id": data.get("user_id") if isinstance(data, dict) else None,
                "action": (data.get("type") or data.get("action")) if isinstance(data, dict) else None,
                "rack_id": data.get("rack_id") if isinstance(data, dict) else None,
                "timestamp": now_iso,
                "type": "auth_response",
                "station_id": None,
                "reply": "deny"
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply), qos=2, retain=False)
        except Exception:
            pass

# MQTT local (secured)
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    #client.subscribe("sensors/#")
    client.subscribe("hepl/#")
    client.subscribe("hepl/auth", qos=2)  # auth messages
    client.subscribe("hepl/location")  # location messages
    client.subscribe("hepl/parked", qos=2) # bike_id rack_id station_id

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        print(f"[MQTT] Message received: {payload}")
        # Authentification
        if msg.topic == "hepl/auth":
            handle_auth_message(client, payload)
            insert_to_mongo(msg.topic, payload)
        elif msg.topic == "hepl/location":
            try:
                data = json.loads(payload)
                data["timestamp"] = datetime.now(BRUSSELS)
                locations_col.insert_one(data)
                print(f"[MQTT] Location data inserted into database")
            except json.JSONDecodeError:
                print("[MQTT] Error decoding JSON payload for location message")
            except Exception as e:
                print(f"[MQTT] Error inserting location data: {e}")
            insert_to_mongo(msg.topic, payload)
        else:
            insert_to_mongo(msg.topic, payload)
    except Exception as e:
        print(f"[MQTT] Error in on_message: {e}")

def start_mqtt_loop():
    while True:
        try:
            mqtt_client.connect(MQTT_BROKER, MQTT_PORT, 60)
            mqtt_client.loop_start()
            while True:
                if not mqtt_client.is_connected():
                    print("[MQTT] Disconnected! Reconnecting...")
                    mqtt_client.reconnect()
                time.sleep(5)
        except Exception as e:
            print(f"[MQTT] loop error: {e}")
            time.sleep(5)

# MQTT client setup
mqtt_client = mqtt.Client(client_id=MQTT_CLIENT_ID)
mqtt_client.username_pw_set(username=MQTT_USERNAME, password=MQTT_PASSWORD)
mqtt_client.tls_set(
    ca_certs=MQTT_CA_CERT,
    certfile=MQTT_CLIENT_CERT,
    keyfile=MQTT_CLIENT_KEY,
    tls_version=ssl.PROTOCOL_TLSv1_2
)
mqtt_client.tls_insecure_set(True)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Start MQTT thread
mqtt_thread = threading.Thread(target=start_mqtt_loop)
mqtt_thread.daemon = True
mqtt_thread.start()

# MQTT ext (test broker)
def on_connect_ext(client, userdata, flag, rc):
    print(f"[EXT MQTT] Connected to {EXT_MQTT_BROKER} with rc {rc}")
    client.subscribe("hepl/disponibilities")

# Handle disponibilities alerts with twilio
def handle_disponibility_alerts(count: int):
    """
    If count stays 0 for ZERO_ALERT_SECONDS, send one "no bikes" SMS and arm recovery.
    If count > 0 and a "no bikes" SMS was sent, send one "available again" SMS.
    If zero resolves before 15 min, send nothing at all.
    """
    # init global variables to track state
    global zero_since_ts, zero_alert_sent
    t = time.time()

    # If count is 0, check if we need to send an alert
    if count == 0:
        if zero_since_ts is None:
            zero_since_ts = t
        # If we have not sent an alert yet and the time since zero_since_ts is >= ZERO_ALERT_SECONDS
        if (not zero_alert_sent) and (t - zero_since_ts) >= ZERO_ALERT_SECONDS:
            # Send alert
            if twilio_send_sms(f"No bikes available for {ZERO_ALERT_SECONDS // 60} minutes!"):
                zero_alert_sent = True
    else:
        # If count > 0 and we had sent a zero alert, send an "available again" SMS
        if zero_alert_sent:
            twilio_send_sms(f"Bikes available again: {count}.")
        # reset in all cases when count > 0
        zero_since_ts = None
        zero_alert_sent = False

def on_message_ext(client, userdata, msg):
    global latest_disponibilities, latest_disponibilities_count
    try:
        payload = msg.payload.decode()
        latest_disponibilities = payload

        # Regex to extract number
        m = re.search(r"\d+", payload)
        latest_disponibilities_count = int(m.group(0)) if m else None

        print(f"[EXT MQTT] {msg.topic}={payload} (count={latest_disponibilities_count})")
        # Trigger alerts logic after we have the count
        if latest_disponibilities_count is not None:
            handle_disponibility_alerts(latest_disponibilities_count)

        # Reload SSE
        publish_ping()
    except Exception as e:
        print(f"[EXT MQTT] Error in on_message_ext: {e}")

def start_mqtt_loop_ext():
    while True:
        try:
            mqtt_client_ext.connect(EXT_MQTT_BROKER, EXT_MQTT_PORT, 60)
            mqtt_client_ext.loop_start()
            while True:
                if not mqtt_client_ext.is_connected():
                    print("[MQTT EXT] Disconnected! Reconnecting...")
                    mqtt_client_ext.reconnect()
                time.sleep(5)
        except Exception as e:
            print(f"[MQTT EXT] loop error: {e}")

mqtt_client_ext = mqtt.Client(client_id=EXT_MQTT_CLIENT_ID)
mqtt_client_ext.tls_set(
    ca_certs=EXT_MQTT_CA_CERT,
    certfile=EXT_MQTT_CLIENT_CERT,
    keyfile=EXT_MQTT_CLIENT_KEY,
    tls_version=ssl.PROTOCOL_TLSv1_2
)
mqtt_client_ext.on_connect = on_connect_ext
mqtt_client_ext.on_message = on_message_ext


# Start MQTT EXT thread
mqtt_thread_ext = threading.Thread(target=start_mqtt_loop_ext, daemon=True)
mqtt_thread_ext.start()

"""
MONGO API
"""
# Users
@app.route("/smartpedals/api/users", methods=["GET"])
@require_api_key
def list_users():
    docs = users_col.find()
    users = []
    for d in docs:
        users.append({
            "id": str(d["_id"]),
            "firstName": d.get("firstName"),
            "lastName": d.get("lastName"),
            "email": d.get("email"),
            "phone": d.get("phone"),
            "rfid": d.get("rfid"),
            "history": d.get("history")})
    return jsonify(users), 200

@app.route("/smartpedals/api/users/<string:rfid>", methods=["GET"])
@require_api_key
def get_user(rfid):
    d = users_col.find_one({"rfid": rfid})
    if not d:
        return jsonify({"status": "not_found"}), 404
    user = {
        "id": str(d["_id"]),
        "firstName": d.get("firstName"),
        "lastName": d.get("lastName"),
        "email": d.get("email"),
        "phone": d.get("phone"),
        "rfid": d.get("rfid"),
        "history": d.get("history")
    }
    return jsonify(user), 200

@app.route("/smartpedals/api/users", methods=["POST"])
@require_api_key
def create_user():
    user_data = request.get_json()
    try:
        result = users_col.insert_one(user_data)
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/users/<string:rfid>", methods=["PUT"])
@require_api_key
def update_user(rfid):
    update = request.get_json()
    # do not allow changing the rfid itself
    update.pop("rfid", None)
    try:
        res = users_col.update_one(
            {"rfid": rfid},
            {"$set": update}
        )
        if res.matched_count:
            return jsonify({"status": "updated"}), 200
        else:
            return jsonify({"status": "not_found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/users/<string:rfid>", methods=["DELETE"])
@require_api_key
def delete_user(rfid):
    result = users_col.delete_one({"rfid": rfid})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Bikes
@app.route("/smartpedals/api/bikes", methods=["GET"])
@require_api_key
def list_bikes():
    docs = bikes_col.find()
    bikes = []
    for d in docs:
        bikes.append({
            "id": str(d["_id"]),
            "bike_id": d.get("bike_id"),
            "status": d.get("status"),
            "currentUser": d.get("currentUser"),
            "currentRack": d.get("currentRack"),
            "history": d.get("history", [])
        })
    return jsonify(bikes), 200

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["GET"])
@require_api_key
def get_bike(bike_id):
    d = bikes_col.find_one({"bike_id": bike_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    bike = {
        "id": str(d["_id"]),
        "bike_id": d.get("bike_id"),
        "status": d.get("status"),
        "currentUser": d.get("currentUser"),
        "currentRack": d.get("currentRack"),
        "history": d.get("history", [])
    }
    return jsonify(bike), 200

@app.route("/smartpedals/api/bikes", methods=["POST"])
@require_api_key
def create_bike():
    bike_data = request.get_json()

    # Force status to be available
    bike_data["status"] = "available"

    # Rack need to exist and be empty
    rack_id = bike_data.get("currentRack")
    now = datetime.now(BRUSSELS)
    if rack_id:
        rack = racks_col.find_one({"rack_id": rack_id})
        if not rack:
            return jsonify({"status": "error", "message": f"Rack '{rack_id}' not found"}), 400
        if rack.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Rack '{rack_id}' is already occupied"}), 400
    try:
        result = bikes_col.insert_one(bike_data)

        # Mark that rack as now this bike
        if rack_id:
            racks_col.update_one({"rack_id": rack_id}, {"$set": {"currentBike": bike_data["bike_id"]}, "$push": {"history": {
                "bike_id": bike_data["bike_id"],
                "action": "dock",
                "timestamp": now}}})
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["PUT"])
@require_api_key
def update_bike(bike_id):
    update = request.get_json()
    update.pop("bike_id", None)

    # Ty to change rack
    new_rack = update.get("currentRack")
    now = datetime.now(BRUSSELS)
    if new_rack is not None:
        rack = racks_col.find_one({"rack_id": new_rack})
        if not rack:
            return jsonify({"status": "error", "message": f"Rack '{new_rack}' not found"}), 400
        if rack.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Rack '{new_rack}' is already occupied"}), 400
    try:
        # Get the old rack
        bike = bikes_col.find_one({"bike_id": bike_id})

        # Don't update a bike if it is in use
        if bike.get("status") == "in_use" or bike.get("currentUser") is not None:
            return jsonify({"status": "error", "message": f"Cannot change rack while bike '{bike_id}' is in use"}), 400

        old_rack = bike.get("currentRack")

        res = bikes_col.update_one(
            {"bike_id": bike_id},
            {"$set": update}
        )
        if res.matched_count:
            # Free old slot and occupy a new one
            if new_rack is not None:
                if old_rack:
                    racks_col.update_one({"rack_id": old_rack}, {"$set": {"currentBike": None}, "$push": {"history": {
                        "bike_id": bike_id,
                        "action": "undock",
                        "timestamp": now}}})
                racks_col.update_one({"rack_id": new_rack}, {"$set": {"currentBike": bike_id}, "$push": {"history": {
                    "bike_id": bike_id,
                    "action": "dock",
                    "timestamp": now}}})
            return jsonify({"status": "updated"}), 200
        else:
            return jsonify({"status": "not_found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["DELETE"])
@require_api_key
def delete_bike(bike_id):
    bike = bikes_col.find_one({"bike_id": bike_id})

    # Don't delete if in use
    if bike.get("status") == "in_use" or bike.get("currentUser") is not None:
        return jsonify({"status": "error", "message": f"Cannot delete bike '{bike_id}' while it is in use"}), 400

    # Undock from the rack
    now = datetime.now(BRUSSELS)
    old_rack = bike.get("currentRack")
    if old_rack:
        racks_col.update_one({"rack_id": old_rack}, {"$set": {"currentBike": None}, "$push": {"history": {
            "bike_id": bike_id,
            "action": "undock",
            "timestamp": now}}})
    result = bikes_col.delete_one({"bike_id": bike_id})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Racks
@app.route("/smartpedals/api/racks", methods=["GET"])
@require_api_key
def list_racks():
    docs = racks_col.find()
    racks = []
    for d in docs:
        racks.append({
            "id": str(d["_id"]),
            "rack_id": d.get("rack_id"),
            "station_id": d.get("station_id"),
            "currentBike": d.get("currentBike"),
            "history": d.get("history", [])
        })
    return jsonify(racks), 200

@app.route("/smartpedals/api/racks/<string:rack_id>", methods=["GET"])
@require_api_key
def get_rack(rack_id):
    d = racks_col.find_one({"rack_id": rack_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    rack = {
        "id": str(d["_id"]),
        "rack_id": d.get("rack_id"),
        "station_id": d.get("station_id"),
        "currentBike": d.get("currentBike"),
        "history": d.get("history", [])
    }
    return jsonify(rack), 200

@app.route("/smartpedals/api/racks", methods=["POST"])
@require_api_key
def create_rack():
    rack_data = request.get_json()
    rack_id = rack_data.get("rack_id")

    # Station ID is required
    station_id = rack_data.get("station_id")
    if station_id:
        station = stations_col.find_one({"station_id": station_id})
        if not station:
            return jsonify({"status": "error", "message": f"Station '{station_id}' not found"}), 400

    try:
        result = racks_col.insert_one(rack_data)

        # Add this rack to the station
        if station_id:
            stations_col.update_one(
                {"station_id": station_id},
                {"$push": {"racks": rack_id}}
            )
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/racks/<string:rack_id>", methods=["DELETE"])
@require_api_key
def delete_rack(rack_id):
    rack = racks_col.find_one({"rack_id": rack_id})

    # Don't delete if there is a bike
    if rack.get("currentBike") is not None:
        return jsonify({"status": "error", "message": f"Cannot delete rack '{rack_id}' while a bike is docked"}), 400

    # Remove the rack from the station
    station_id = rack.get("station_id")
    if station_id:
        stations_col.update_one(
            {"station_id": station_id},
            {"$pull": {"racks": rack_id}}
        )

    # Delete the rack
    res = racks_col.delete_one({"rack_id": rack_id})
    if res.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Stations
@app.route("/smartpedals/api/stations", methods=["GET"])
@require_api_key
def list_stations():
    docs = stations_col.find()
    stations = []
    for d in docs:
        stations.append({
            "id": str(d["_id"]),
            "station_id": d.get("station_id"),
            "name": d.get("name"),
            "racks": d.get("racks")
        })
    return jsonify(stations), 200

@app.route("/smartpedals/api/stations/<string:station_id>", methods=["GET"])
@require_api_key
def get_station(station_id):
    d = stations_col.find_one({"station_id": station_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    station = {
        "id": str(d["_id"]),
        "station_id": d.get("station_id"),
        "name": d.get("name"),
        "racks": d.get("racks", [])
    }
    return jsonify(station), 200

@app.route("/smartpedals/api/stations", methods=["POST"])
@require_api_key
def create_station():
    station_data = request.get_json()
    try:
        result = stations_col.insert_one(station_data)
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/stations/<string:station_id>", methods=["DELETE"])
@require_api_key
def delete_station(station_id):
    # Check if there are racks in this station
    station = stations_col.find_one({"station_id": station_id})

    # Check if there are racks with bikes
    racks = station.get("racks", [])
    for rack in racks:
        rack_data = racks_col.find_one({"rack_id": rack})
        if rack_data and rack_data.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Cannot delete station '{station_id}' while a bike is docked"}), 400
        
        # Remove the racks from the racks collection
        racks_col.delete_one({"rack_id": rack})

    # racks_col.delete_many({"station_id": station_id})
    result = stations_col.delete_one({"station_id": station_id})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Locations
@app.route("/smartpedals/api/locations", methods=["GET"])
@require_api_key
def list_locations():
    # Exclude _id -> bug in node red
    docs = locations_col.find({}, {"_id": 0})
    locations = []
    for d in docs:
        d["timestamp"] = d["timestamp"].isoformat()
        locations.append(d)
    return jsonify(locations), 200

"""
WEB PAGE
"""
# Home page
@app.route("/smartpedals/")
def home():
    return render_template("home.html")

# Database page
@app.route("/smartpedals/database", methods=["GET", "POST"])
def database():
    # POST (clear_all, delete_selected)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "clear_all":
            try:
                ids = data_col.distinct("_id")
                for entry_id in ids:
                    data_col.delete_one({"_id": entry_id})
                flash("All data deleted!")
            except Exception as e:
                flash(f"Error during deletion: {e}")
        elif action == "delete_selected":
            selected_ids = request.form.getlist('entry_checkbox')
            for entry_id in selected_ids:
                try:
                    data_col.delete_one({"_id": ObjectId(entry_id)})
                except Exception as e:
                    flash(f"Delete error: {e}")
            flash("Selected entries deleted!")

        # Redirect after POST to avoid popup
        return redirect(url_for("database"))

    # Read data
    data = list(data_col.find({}, {"_id": 1, "topic": 1, "payload": 1}))
    return render_template("database.html", data=data, disponibilities=latest_disponibilities, disponibilities_count=latest_disponibilities_count)

# Weather page
@app.route("/smartpedals/weather", methods=["GET"])
def weather():
    city = request.args.get("city", DEFAULT_CITY)
    weather = None
    if not OPENWEATHER_API_KEY:
        app.logger.error("OPENWEATHER_API_KEY not configured")
    else:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": f"{city},BE",
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": OPENWEATHER_LANG
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            weather = resp.json()
        except requests.RequestException as e:
            app.logger.error(f"Error weather API: {e}")
    return render_template("weather.html", weather=weather, city=city)

# Support page
@app.route("/smartpedals/support", methods=["GET"])
def support():
    room_id = (request.args.get("room_id") or "").strip()
    excuse = None
    try:
        # Fetch a random developer excuse from the API
        developer_excuse_response = requests.get("http://developerexcuses.com/", timeout=5)
        developer_excuse_response.raise_for_status()
        excuse = developer_excuse_response.text.strip()

        # Extract the excuse text from the HTML response
        html_content = developer_excuse_response.text
        match = re.search(r'<a href="/" rel="nofollow" style="[^"]*">(.*?)</a>', html_content)
        if match:
            excuse = match.group(1)

    except requests.RequestException as e:
        app.logger.warning(f"Failed to fetch developer excuse: {e}")

    return render_template("support.html", room_id=room_id, default_title=SUPPORT_DEFAULT_TITLE, support_members=os.environ.get("SUPPORT_MEMBERS", ""), excuse=excuse)

# Create room + invite members + post welcome (capture web URL)
@app.route("/smartpedals/support/create", methods=["POST"])
def support_create():
    token = WEBEX_ACCESS_TOKEN
    if not token:
        flash("WEBEX_ACCESS_TOKEN is missing (bot token).", "error")
        app.logger.error("WEBEX_ACCESS_TOKEN is not set.")
        return redirect(url_for("support"))

    title = (request.form.get("title") or SUPPORT_DEFAULT_TITLE).strip()
    headers_json = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # Check if a room with the same title already exists and delete it
    app.logger.info(f"Checking for existing support spaces with title: '{title}' to delete.")
    try:
        # Get rooms with the specified title (Webex API might return partial matches)
        search_response = requests.get(
            f"{WEBEX_API_BASE}/rooms",
            headers=headers_json,
            params={"type": "group", "title": title},
            timeout=8
        )
        search_response.raise_for_status()
        rooms_found = search_response.json().get("items", [])

        deleted_count = 0
        for room in rooms_found:
            # Ensure exact title match before deleting
            if room.get("title") == title:
                room_to_delete_id = room.get("id")
                try:
                    delete_response = requests.delete(
                        f"{WEBEX_API_BASE}/rooms/{room_to_delete_id}",
                        headers=headers_json,
                        timeout=8
                    )
                    if delete_response.status_code == 204: # 204 No Content is success for DELETE
                        app.logger.info(f"Successfully deleted existing room: {room_to_delete_id} ('{title}')")
                        deleted_count += 1
                    else:
                        app.logger.warning(f"Failed to delete existing room {room_to_delete_id}: {delete_response.status_code} - {delete_response.text}")
                except requests.RequestException as e:
                    app.logger.error(f"Error deleting existing room {room_to_delete_id}: {e}")

        if deleted_count > 0:
            flash(f"Deleted {deleted_count} old support space(s) with title '{title}'.", "info")
        else:
            app.logger.info(f"No existing support spaces with title '{title}' found to delete.")

    except requests.RequestException as e:
        flash(f"Error checking for/deleting old Webex spaces: {e}", "error")
        app.logger.error(f"Error during pre-creation cleanup of rooms: {e}")
        # Continue to try creating a new room even if cleanup fails
        pass

    # Create new room
    room_id = None
    try:
        room_creation_response = requests.post(f"{WEBEX_API_BASE}/rooms", headers=headers_json,
                          json={"title": title}, timeout=8)
        room_creation_response.raise_for_status()
        room_id = room_creation_response.json().get("id")
        if not room_id:
            flash("Room creation succeeded but no room ID returned.", "error")
            app.logger.error("New room creation succeeded but no room ID returned.")
            return redirect(url_for("support"))
        flash(f"Created new support space: '{title}'.", "success") # Specific success message
        app.logger.info(f"Created new room with room_id: {room_id}")

    except requests.RequestException as e:
        flash(f"Webex room creation error: {e}", "error")
        app.logger.error(f"Error creating new room: {e}")
        return redirect(url_for("support"))

    # Invite members
    members_raw = os.environ.get("SUPPORT_MEMBERS", "")
    members = [m.strip() for m in members_raw.split(",") if m.strip()]
    # Check if there are any members to invite
    for email in members:
        # Skip bot emails, already a member
        if email.lower().endswith(".bot"):
            app.logger.info(f"Skipping bot email in membership: {email}")
            continue
        try:
            membership_response = requests.post(f"{WEBEX_API_BASE}/memberships", headers=headers_json,
                               json={"roomId": room_id, "personEmail": email}, timeout=8)
            if membership_response.status_code not in (200, 409):  # 409 = already a member
                membership_response.raise_for_status()
            elif membership_response.status_code == 409: # Explicitly log already a member
                app.logger.info(f"Member {email} is already in room {room_id}. (Skipped invite)")
        except requests.RequestException as e:
            app.logger.error(f"[WEBEX] Invite {email} error: {e}")

    # Post welcome message and capture its web URL (opens the space in the browser)
    try:
        msg = "Support space created. Open the space and click **Meet** to start the call via the web app."
        message_response = requests.post(f"{WEBEX_API_BASE}/messages", headers=headers_json,
                           json={"roomId": room_id, "markdown": msg}, timeout=8)
        message_response.raise_for_status()

        app.logger.info(f"Webex Response - Statut: {message_response.status_code}")
        app.logger.info(f"Webex Response - Text: {message_response.text}")
    except requests.RequestException as e:
        app.logger.error(f"[WEBEX] Post message error: {e}")
        flash("Failed to post welcome message to the new space.", "warning")

    # Redirect back with room_id (and message web URL) as query params
    return redirect(url_for("support", room_id=room_id))

# Delete room
@app.route("/smartpedals/support/delete", methods=["POST"])
def support_delete():
    token = WEBEX_ACCESS_TOKEN
    if not token:
        flash("WEBEX_ACCESS_TOKEN is missing (bot token).", "error")
        app.logger.error("WEBEX_ACCESS_TOKEN is not set.")
        return redirect(url_for("support"))

    room_id = (request.form.get("room_id") or "").strip()
    if not room_id:
        flash("Missing room_id.", "error")
        return redirect(url_for("support"))

    headers = {"Authorization": f"Bearer {token}"}
    app.logger.info(f"Attempting to delete room: {room_id}")
    try:
        r = requests.delete(f"{WEBEX_API_BASE}/rooms/{room_id}", headers=headers, timeout=8)
        if r.status_code == 204:
            flash("Space deleted successfully!", "success")
            app.logger.info(f"Room {room_id} deleted successfully.")
        else:
            try:
                err = r.json()
            except Exception:
                err = r.text
            flash(f"Delete failed: {r.status_code} {err}", "error")
            app.logger.error(f"Delete failed for room {room_id}: {r.status_code} {err}")
    except requests.RequestException as e:
        flash(f"Webex delete error: {e}", "error")
        app.logger.error(f"Webex delete error for room {room_id}: {e}")

    return redirect(url_for("support"))

# Security page
@app.route("/smartpedals/security", methods=["GET"])
def security():
    api_info = None
    hepl_info = None
    mosq_info = None

    if not SHODAN_API_KEY:
        app.logger.error("SHODAN_API_KEY not configured")
        flash("SHODAN_API_KEY not configured", "error")
        return render_template("security.html", api_info=None, hepl_info=None, mosq_info=None)

    try:
        # Api info
        url_info = f"{SHODAN_API_BASE}/api-info"
        params = {"key": SHODAN_API_KEY}
        resp_info = requests.get(url_info, params=params, timeout=5)
        resp_info.raise_for_status()
        api_info = resp_info.json()
    except requests.RequestException as e:
        app.logger.error(f"Error fetching Shodan API info: {e}")
        flash("Failed to fetch Shodan API information.", "error")

    try:
        # hepl info
        url_hepl = f"{SHODAN_API_BASE}/shodan/host/{requests.get('https://api.ipify.org').text.strip()}"
        resp_hepl = requests.get(url_hepl, params=params, timeout=10)
        app.logger.info(f"HEPL lookup response: {resp_hepl.status_code} - {resp_hepl.text}")
        # Handle specific status codes
        if resp_hepl.status_code in (401, 403):
            flash(f"HEPL lookup blocked by Shodan plan (HTTP {resp_hepl.status_code}).", "warning")
        resp_hepl.raise_for_status()
        hepl_info = resp_hepl.json()
    except requests.RequestException as e:
        app.logger.error(f"Error fetching Shodan HEPL info: {e}")
        flash("Failed to fetch Shodan HEPL information.", "error")

    try:
        # test.mosquitto.org info
        url_mosq = f"{SHODAN_API_BASE}/shodan/host/test.mosquitto.org"
        resp_mosq = requests.get(url_mosq, params=params, timeout=10)
        app.logger.info(f"MQTT lookup response: {resp_mosq.status_code} - {resp_mosq.text}")
        # Handle specific status codes
        if resp_mosq.status_code in (401, 403):
            flash(f"MQTT lookup blocked by Shodan plan (HTTP {resp_mosq.status_code}).", "warning")
        resp_mosq.raise_for_status()
        mosq_info = resp_host.json()
    except requests.RequestException as e:
        app.logger.error(f"Error fetching Shodan MQTT info: {e}")
        flash("Failed to fetch Shodan MQTT information.", "error")

    return render_template("security.html", api_info=api_info, hepl_info=hepl_info, mosq_info=mosq_info)

if __name__ == "__main__":
    # app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0")
    app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0", port=FLASK_TLS_PORT, ssl_context=(FLASK_TLS_CERT, FLASK_TLS_KEY)) # Secured version (tls)
