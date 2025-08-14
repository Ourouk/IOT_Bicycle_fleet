import os
import queue
import threading
import time
import ssl
import re

from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from flask import (
    Flask, render_template, request, flash,
    Response, stream_with_context, url_for, redirect,
    jsonify
)
from pymongo import MongoClient
import paho.mqtt.client as mqtt
from bson import ObjectId
from twilio.rest import Client as TwilioClient

# Flask
FLASK_TLS_CERT = os.environ.get("FLASK_TLS_CERT", "/etc/ssl/client-flask.crt")
FLASK_TLS_KEY = os.environ.get("FLASK_TLS_KEY",  "/etc/ssl/client-flask.key.unlocked")
FLASK_TLS_PORT = int(os.environ.get("FLASK_TLS_PORT", "8443"))

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

# Twilio (SMS notifications)
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
TWILIO_NUMBER = os.environ.get("TWILIO_NUMBER", "")
TARGET_NUMBER = os.environ.get("TARGET_NUMBER", "")
ZERO_ALERT_SECONDS = int(os.environ.get("ZERO_ALERT_SECONDS", 15 * 60)) # Send message after 15

# Webex
WEBEX_API_BASE = os.environ.get("WEBEX_API_BASE", "https://webexapis.com/v1")
WEBEX_ACCESS_TOKEN = os.environ.get("WEBEX_ACCESS_TOKEN", "")
SUPPORT_DEFAULT_TITLE = os.environ.get("SUPPORT_DEFAULT_TITLE", "Support – HEPL")

# SSE (Server-Sent Events)
events_listeners = []
LISTENER_QUEUE_SIZE = 1

# Other
BRUSSELS = ZoneInfo("Europe/Brussels")
# External topic cache
latest_disponibilities = None
latest_disponibilities_count = None
# Twilio
twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
# Zero-availability alert state
zero_since_ts = None
zero_alert_sent = False

# App
app = Flask(__name__)
app.secret_key = "dev"

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

# Auth message handler
def handle_auth_message(mqtt_client_instance, payload):
    now = datetime.now(BRUSSELS)
    reply_topic = "hepl/auth_reply"
    try:
        data = json.loads(payload)
        user_id = data.get("user_id")
        bike_id = data.get("bike_id")
        rack_id = data.get("rack_id")
        action = data.get("action")

        if not all([user_id, bike_id, action]):
            print("[MQTT] Invalid auth message format.")
            # Send Deny if smth is missing
            reply = {
                "bike_id": bike_id,
                "type": "auth_reply",
                "action": "deny",
                "user_id": user_id,
                "timestamp": now.isoformat()
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply))
            return

        # Check for the user_id in the users collection
        user = users_col.find_one({"rfid": user_id})
        if not user:
            print(f"[MQTT] User with rfid '{user_id}' not found. Denying request.")
            reply = {
                "bike_id": bike_id,
                "type": "auth_reply",
                "action": "deny",
                "user_id": user_id,
                "timestamp": now.isoformat()
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply))
            return

        # Check for the bike
        bike = bikes_col.find_one({"bikeId": bike_id})
        if not bike:
            print(f"[MQTT] Bike with ID '{bike_id}' not found. Denying request.")
            reply = {
                "bike_id": bike_id,
                "type": "auth_reply",
                "action": "deny",
                "user_id": user_id,
                "timestamp": now.isoformat()
            }
            mqtt_client_instance.publish(reply_topic, json.dumps(reply))
            return

        # Reply
        reply_action = "deny"

        if action == "unlock":
            # Check the bike status (need to be available)
            if bike.get("status") == "available" and not bike.get("currentUser"):
                # Mettre à jour l'état du vélo et de l'utilisateur
                bikes_col.update_one(
                    {"bikeId": bike_id},
                    {"$set": {"status": "in_use", "currentUser": user_id, "currentRack": None},
                     "$push": {"history": {"action": "undock", "userRfid": user_id, "timestamp": now}}}
                )
                users_col.update_one(
                    {"rfid": user_id},
                    {"$push": {"history": {"bikeId": bike_id, "action": "undock", "timestamp": now}}}
                )

                # Update racks
                # if bike.get("currentRack"):
                #     racks_col.update_one(
                #         {"rackId": bike["currentRack"]},
                #         {"$set": {"currentBike": None},
                #          "$push": {"history": {"bikeId": bike_id, "action": "undock", "timestamp": now}}}
                #     )
                reply_action = "accept"
                print(f"[MQTT] Unlock request accepted for bike '{bike_id}' by user '{user_id}'.")
            else:
                print(f"[MQTT] Unlock request denied for bike '{bike_id}' (not available).")

        elif action == "lock":
            # Check the bike status (is used by the requested user?)
            if bike.get("status") == "in_use" and bike.get("currentUser") == user_id:
                # Update bikes and users collection
                bikes_col.update_one(
                    {"bikeId": bike_id},
                    {"$set": {"status": "available", "currentUser": None},
                     "$push": {"history": {"action": "dock", "userRfid": user_id, "timestamp": now}}}
                )
                users_col.update_one(
                    {"rfid": user_id},
                    {"$push": {"history": {"bikeId": bike_id, "action": "dock", "timestamp": now}}}
                )
                reply_action = "accept"
                print(f"[MQTT] Lock request accepted for bike '{bike_id}' by user '{user_id}'.")
            else:
                print(f"[MQTT] Lock request denied for bike '{bike_id}' (not in use by this user).")

        # Publish in topic `hepl/auth_reply`
        reply = {
            "bike_id": bike_id,
            "rack_id": rack_id,
            "type": "auth_reply",
            "action": reply_action,
            "user_id": user_id,
            "timestamp": now.isoformat()
        }
        mqtt_client_instance.publish(reply_topic, json.dumps(reply))

        # Update SSE
        publish_ping()

    except json.JSONDecodeError:
        print("[MQTT] Error decoding JSON payload for auth message.")
    except Exception as e:
        print(f"[MQTT] Error handling auth message: {e}")
        reply = {
            "bike_id": data.get("bike_id"),
            "type": "auth_reply",
            "action": "deny",
            "user_id": data.get("user_id"),
            "timestamp": now.isoformat()
        }
        mqtt_client_instance.publish(reply_topic, json.dumps(reply))

# MQTT local (secured)
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    #client.subscribe("sensors/#")
    client.subscribe("hepl/#")
    client.subscribe("hepl/auth")
    client.subscribe("hepl/location")
    client.subscribe("hepl/parked") # bikeId rackId stationId

def on_message(client, userdata, msg):
    try:
        payload = msg.payload.decode()
        print(f"[MQTT] Message received: {payload}")
        # Authentification
        if msg.topic == "hepl/auth":
            handle_auth_message(client, payload)
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

def handle_disponibility_alerts(count: int):
    """
    If count stays 0 for ZERO_ALERT_SECONDS, send one "no bikes" SMS and arm recovery.
    If count > 0 and a "no bikes" SMS was sent, send one "available again" SMS.
    If zero resolves before 15 min, send nothing at all.
    """
    global zero_since_ts, zero_alert_sent
    t = time.time()

    if count == 0:
        if zero_since_ts is None:
            zero_since_ts = t
        if (not zero_alert_sent) and (t - zero_since_ts) >= ZERO_ALERT_SECONDS:
            if twilio_send_sms(f"No bikes available for {ZERO_ALERT_SECONDS // 60} minutes!"):
                zero_alert_sent = True
    else:
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
def create_user():
    user_data = request.get_json()
    try:
        result = users_col.insert_one(user_data)
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/users/<string:rfid>", methods=["PUT"])
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
def delete_user(rfid):
    result = users_col.delete_one({"rfid": rfid})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Bikes
@app.route("/smartpedals/api/bikes", methods=["GET"])
def list_bikes():
    docs = bikes_col.find()
    bikes = []
    for d in docs:
        bikes.append({
            "id": str(d["_id"]),
            "bikeId": d.get("bikeId"),
            "status": d.get("status"),
            "currentUser": d.get("currentUser"),
            "currentRack": d.get("currentRack"),
            "history": d.get("history", [])
        })
    return jsonify(bikes), 200

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["GET"])
def get_bike(bike_id):
    d = bikes_col.find_one({"bikeId": bike_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    bike = {
        "id": str(d["_id"]),
        "bikeId": d.get("bikeId"),
        "status": d.get("status"),
        "currentUser": d.get("currentUser"),
        "currentRack": d.get("currentRack"),
        "history": d.get("history", [])
    }
    return jsonify(bike), 200

@app.route("/smartpedals/api/bikes", methods=["POST"])
def create_bike():
    bike_data = request.get_json()

    # Force status to be available
    bike_data["status"] = "available"

    # Rack need to exist and be empty
    rack_id = bike_data.get("currentRack")
    now = datetime.now(BRUSSELS)
    if rack_id:
        rack = racks_col.find_one({"rackId": rack_id})
        if not rack:
            return jsonify({"status": "error", "message": f"Rack '{rack_id}' not found"}), 400
        if rack.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Rack '{rack_id}' is already occupied"}), 400
    try:
        result = bikes_col.insert_one(bike_data)

        # Mark that rack as now this bike
        if rack_id:
            racks_col.update_one({"rackId": rack_id}, {"$set": {"currentBike": bike_data["bikeId"]}, "$push": {"history": {
                "bikeId": bike_data["bikeId"],
                "action": "dock",
                "timestamp": now}}})
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["PUT"])
def update_bike(bike_id):
    update = request.get_json()
    update.pop("bikeId", None)

    # Ty to change rack
    new_rack = update.get("currentRack")
    now = datetime.now(BRUSSELS)
    if new_rack is not None:
        rack = racks_col.find_one({"rackId": new_rack})
        if not rack:
            return jsonify({"status": "error", "message": f"Rack '{new_rack}' not found"}), 400
        if rack.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Rack '{new_rack}' is already occupied"}), 400
    try:
        # Get the old rack
        bike = bikes_col.find_one({"bikeId": bike_id})

        # Don't update a bike if it is in use
        if bike.get("status") == "in_use" or bike.get("currentUser") is not None:
            return jsonify({"status": "error", "message": f"Cannot change rack while bike '{bike_id}' is in use"}), 400

        old_rack = bike.get("currentRack")

        res = bikes_col.update_one(
            {"bikeId": bike_id},
            {"$set": update}
        )
        if res.matched_count:
            # Free old slot and occupy a new one
            if new_rack is not None:
                if old_rack:
                    racks_col.update_one({"rackId": old_rack}, {"$set": {"currentBike": None}, "$push": {"history": {
                        "bikeId": bike_id,
                        "action": "undock",
                        "timestamp": now}}})
                    racks_col.update_one({"rackId": new_rack}, {"$set": {"currentBike": bike_id}, "$push": {"history": {
                        "bikeId": bike_id,
                        "action": "dock",
                        "timestamp": now}}})
            return jsonify({"status": "updated"}), 200
        else:
            return jsonify({"status": "not_found"}), 404
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/bikes/<string:bike_id>", methods=["DELETE"])
def delete_bike(bike_id):
    bike = bikes_col.find_one({"bikeId": bike_id})

    # Don't delete if in use
    if bike.get("status") == "in_use" or bike.get("currentUser") is not None:
        return jsonify({"status": "error", "message": f"Cannot delete bike '{bike_id}' while it is in use"}), 400

    # Undock from the rack
    now = datetime.now(BRUSSELS)
    old_rack = bike.get("currentRack")
    if old_rack:
        racks_col.update_one({"rackId": old_rack}, {"$set": {"currentBike": None}, "$push": {"history": {
            "bikeId": bike_id,
            "action": "undock",
            "timestamp": now}}})
    result = bikes_col.delete_one({"bikeId": bike_id})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Racks
@app.route("/smartpedals/api/racks", methods=["GET"])
def list_racks():
    docs = racks_col.find()
    racks = []
    for d in docs:
        racks.append({
            "id": str(d["_id"]),
            "rackId": d.get("rackId"),
            "stationId": d.get("stationId"),
            "currentBike": d.get("currentBike"),
            "history": d.get("history", [])
        })
    return jsonify(racks), 200

@app.route("/smartpedals/api/racks/<string:rack_id>", methods=["GET"])
def get_rack(rack_id):
    d = racks_col.find_one({"rackId": rack_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    rack = {
        "id": str(d["_id"]),
        "rackId": d.get("rackId"),
        "stationId": d.get("stationId"),
        "currentBike": d.get("currentBike"),
        "history": d.get("history", [])
    }
    return jsonify(rack), 200

@app.route("/smartpedals/api/racks", methods=["POST"])
def create_rack():
    rack_data = request.get_json()
    try:
        result = racks_col.insert_one(rack_data)
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/racks/<string:rack_id>", methods=["DELETE"])
def delete_rack(rack_id):
    rack = racks_col.find_one({"rackId": rack_id})

    # Don't delete if there is a bike
    if rack.get("currentBike") is not None:
        return jsonify({"status": "error", "message": f"Cannot delete rack '{rack_id}' while a bike is docked"}), 400
    result = racks_col.delete_one({"rackId": rack_id})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

# Stations
@app.route("/smartpedals/api/stations", methods=["GET"])
def list_stations():
    docs = stations_col.find()
    stations = []
    for d in docs:
        stations.append({
            "stationId": d.get("stationId"),
            "name": d.get("name"),
            "racks": d.get("racks")
        })
    return jsonify(racks), 200

# Locations
@app.route("/smartpedals/api/locations", methods=["GET"])
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

# Webex support page
# Support page
# @app.route("/smartpedals/support", methods=["GET"])
# def support():
#     room_id = (request.args.get("room_id") or "").strip()
#     message_web_url = (request.args.get("message_web_url") or "").strip()
#     return render_template("support.html", room_id=room_id, message_web_url=message_web_url, default_title=SUPPORT_DEFAULT_TITLE, support_members=os.environ.get("SUPPORT_MEMBERS", ""))
#
# # Create room + invite members + post welcome (capture web URL)
# @app.route("/smartpedals/support/create", methods=["POST"])
# def support_create():
#     token = WEBEX_ACCESS_TOKEN
#     if not token:
#         flash("WEBEX_ACCESS_TOKEN is missing (bot token).", "error")
#         return redirect(url_for("support"))
#
#     title = request.form.get("title") or SUPPORT_DEFAULT_TITLE
#     headers_json = {
#         "Authorization": f"Bearer {token}",
#         "Content-Type": "application/json",
#         "Accept": "application/json",
#     }
#
#     # Create the room
#     try:
#         r = requests.post(f"{WEBEX_API_BASE}/rooms", headers=headers_json,
#                           json={"title": title}, timeout=8)
#         r.raise_for_status()
#         room_id = r.json().get("id")
#         if not room_id:
#             flash("Room creation succeeded but no room ID returned.", "error")
#             return redirect(url_for("support"))
#     except requests.RequestException as e:
#         flash(f"Webex room creation error: {e}", "error")
#         return redirect(url_for("support"))
#
#     # Invite members (skip *.bot — the bot is already in)
#     members_raw = os.environ.get("SUPPORT_MEMBERS", "")
#     members = [m.strip() for m in members_raw.split(",") if m.strip()]
#     for email in members:
#         if email.lower().endswith(".bot"):
#             continue
#         try:
#             mr = requests.post(f"{WEBEX_API_BASE}/memberships", headers=headers_json,
#                                json={"roomId": room_id, "personEmail": email}, timeout=8)
#             if mr.status_code not in (200, 409):  # 409 = already a member
#                 mr.raise_for_status()
#         except requests.RequestException as e:
#             app.logger.error(f"[WEBEX] Invite {email} error: {e}")
#
#     # Post welcome message and capture its web URL (opens the space in the browser)
#     message_web_url = ""
#     try:
#         msg = "Support space created. Open the space and click **Meet** to start the call."
#         mr = requests.post(f"{WEBEX_API_BASE}/messages", headers=headers_json,
#                            json={"roomId": room_id, "markdown": msg}, timeout=8)
#         mr.raise_for_status()
#         message_web_url = (mr.json() or {}).get("webUrl", "")
#     except requests.RequestException as e:
#         app.logger.error(f"[WEBEX] Post message error: {e}")
#
#     # Redirect back with room_id (and message web URL) as query params (no storage needed)
#     return redirect(url_for("support", room_id=room_id, message_web_url=message_web_url))
#
# # Delete room
# @app.route("/smartpedals/support/delete", methods=["POST"])
# def support_delete():
#     token = WEBEX_ACCESS_TOKEN
#     if not token:
#         flash("WEBEX_ACCESS_TOKEN is missing (bot token).", "error")
#         return redirect(url_for("support"))
#
#     room_id = (request.form.get("room_id") or "").strip()
#     if not room_id:
#         flash("Missing room_id.", "error")
#         return redirect(url_for("support"))
#
#     headers = {"Authorization": f"Bearer {token}"}
#     try:
#         r = requests.delete(f"{WEBEX_API_BASE}/rooms/{room_id}", headers=headers, timeout=8)
#         if r.status_code == 204:
#             flash("Space deleted", "success")
#         else:
#             try:
#                 err = r.json()
#             except Exception:
#                 err = r.text
#             flash(f"Delete failed: {r.status_code} {err}", "error")
#     except requests.RequestException as e:
#         flash(f"Webex delete error: {e}", "error")
#
#     return redirect(url_for("support"))

# Support page
@app.route("/smartpedals/support", methods=["GET"])
def support():
    room_id = (request.args.get("room_id") or "").strip()
    message_web_url = (request.args.get("message_web_url") or "").strip()
    return render_template("support.html", room_id=room_id, message_web_url=message_web_url, default_title=SUPPORT_DEFAULT_TITLE, support_members=os.environ.get("SUPPORT_MEMBERS", ""))

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

    # --- MINIMAL CHANGES START HERE ---

    # 1. Check for and delete existing rooms with the same title
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

    # 2. Create a brand new room
    room_id = None
    message_web_url = ""
    try:
        r = requests.post(f"{WEBEX_API_BASE}/rooms", headers=headers_json,
                          json={"title": title}, timeout=8)
        r.raise_for_status()
        room_id = r.json().get("id")
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

    # Invite members (skip *.bot — the bot is already in)
    members_raw = os.environ.get("SUPPORT_MEMBERS", "")
    members = [m.strip() for m in members_raw.split(",") if m.strip()]
    for email in members:
        if email.lower().endswith(".bot"):
            app.logger.info(f"Skipping bot email in membership: {email}")
            continue
        try:
            mr = requests.post(f"{WEBEX_API_BASE}/memberships", headers=headers_json,
                               json={"roomId": room_id, "personEmail": email}, timeout=8)
            if mr.status_code not in (200, 409):  # 409 = already a member
                mr.raise_for_status()
            elif mr.status_code == 409: # Explicitly log already a member
                app.logger.info(f"Member {email} is already in room {room_id}. (Skipped invite)")
        except requests.RequestException as e:
            app.logger.error(f"[WEBEX] Invite {email} error: {e}")

    # Post welcome message and capture its web URL (opens the space in the browser)
    try:
        msg = "Support space created. Open the space and click **Meet** to start the call via the web app."
        mr = requests.post(f"{WEBEX_API_BASE}/messages", headers=headers_json,
                           json={"roomId": room_id, "markdown": msg}, timeout=8)
        mr.raise_for_status()
        message_web_url = (mr.json() or {}).get("webUrl", "")
        app.logger.info(f"Generated web URL for new room: {message_web_url}")
    except requests.RequestException as e:
        app.logger.error(f"[WEBEX] Post message error: {e}")
        flash("Failed to post welcome message to the new space.", "warning")

    # Redirect back with room_id (and message web URL) as query params (no storage needed)
    return redirect(url_for("support", room_id=room_id, message_web_url=message_web_url))

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

if __name__ == "__main__":
    # app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0")
    app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0", port=FLASK_TLS_PORT, ssl_context=(FLASK_TLS_CERT, FLASK_TLS_KEY)) # Secured version (tls)
