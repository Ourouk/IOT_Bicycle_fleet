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

app = Flask(__name__)
app.secret_key = "dev"

# MongoDB configuration
mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")

# MQTT configuration
mqtt_broker = os.environ.get("MQTT_BROKER", "localhost")
mqtt_port = int(os.environ.get("MQTT_PORT", 1883))

# External topic
latest_disponibilities = None
latest_disponibilities_count = None
mqtt_broker_ext = "test.mosquitto.org"
mqtt_port_ext = 8884

# OpenWeatherMap configuration
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY", "")

# Initialize MongoDB
BRUSSELS = ZoneInfo("Europe/Brussels")
def init_db():
    client = MongoClient(mongo_url)
    db = client.smartpedals
    data_col = db.data
    users_col = db.users
    bikes_col = db.bikes
    stations_col = db.stations
    locations_col = db.locations
    return client, db, data_col, users_col, bikes_col, stations_col, locations_col

client, db, data_col, users_col, bikes_col, stations_col, locations_col = init_db()

# SSE (Server-Sent Events)
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
        temp_client = MongoClient(mongo_url)
        temp_db = temp_client.smartpedals
        temp_data_col = temp_db.data
        result = temp_data_col.insert_one({"topic": topic, "payload": payload})
        print(f"[MQTT] Inserted: {result.inserted_id}")
        publish_ping()
        temp_client.close()
    except Exception as e:
        print(f"[MQTT] Error during insert: {e}")

def handle_auth_message(mqtt_client_instance, payload):
    now = datetime.now(BRUSSELS)
    reply_topic = "hepl/auth_reply"
    try:
        data = json.loads(payload)
        user_id = data.get("user_id")
        bike_id = data.get("bike_id")
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
                    {"$set": {"status": "in_use", "currentUser": user_id, "currentStation": None},
                     "$push": {"history": {"action": "undock", "userRfid": user_id, "timestamp": now}}}
                )
                users_col.update_one(
                    {"rfid": user_id},
                    {"$push": {"history": {"bikeId": bike_id, "action": "undock", "timestamp": now}}}
                )

                # Update station
                # if bike.get("currentStation"):
                #     stations_col.update_one(
                #         {"stationId": bike["currentStation"]},
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

# MQTT local
def on_connect(client, userdata, flags, rc):
    print(f"Connected with result code {rc}")
    #client.subscribe("sensors/#")
    client.subscribe("hepl/#")
    client.subscribe("hepl/auth")

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
            mqtt_client.connect(mqtt_broker, mqtt_port, 60)
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
mqtt_client = mqtt.Client(client_id="trusted-hepl_smartPedals")
mqtt_client.username_pw_set(username="smartadmin", password="smartpass")
mqtt_client.tls_set(
    ca_certs="/etc/ssl/ca.crt",
    certfile="/etc/ssl/client-mqtt.crt",
    keyfile="/etc/ssl/client-mqtt.key.unlocked",
    tls_version=ssl.PROTOCOL_TLSv1_2
)
mqtt_client.tls_insecure_set(True)
mqtt_client.on_connect = on_connect
mqtt_client.on_message = on_message

# Start MQTT thread
mqtt_thread = threading.Thread(target=start_mqtt_loop)
mqtt_thread.daemon = True
mqtt_thread.start()

# MQTT ext
def on_connect_ext(client, userdata, flag, rc):
    print(f"[EXT MQTT] Connected to test.mosquitto.org with rc {rc}")
    client.subscribe("hepl/disponibilities")

def on_message_ext(client, userdata, msg):
    global latest_disponibilities, latest_disponibilities_count
    try:
        payload = msg.payload.decode()
        latest_disponibilities = payload

        # Regex to extract number
        m = re.search(r"\d+", payload)
        latest_disponibilities_count = int(m.group(0)) if m else None

        print(f"[EXT MQTT] {msg.topic}={payload} (count={latest_disponibilities_count})")
    except Exception as e:
        print(f"[EXT MQTT] Error in on_message_ext: {e}")

        # Reload SSE
        publish_ping()

def start_mqtt_loop_ext():
    while True:
        try:
            mqtt_client_ext.connect(mqtt_broker_ext, mqtt_port_ext, 60)
            mqtt_client_ext.loop_start()
            while True:
                if not mqtt_client_ext.is_connected():
                    print("[MQTT EXT] Disconnected! Reconnecting...")
                    mqtt_client_ext.reconnect()
                time.sleep(5)
        except Exception as e:
            print(f"[MQTT EXT] loop error: {e}")

mqtt_client_ext = mqtt.Client(client_id="smartPedals-ext-disponibilities")
mqtt_client_ext.tls_set(
    ca_certs="/etc/ssl/testmosquitto/mosquitto.org.crt",
    certfile="/etc/ssl/testmosquitto/client.crt",
    keyfile="/etc/ssl/testmosquitto/client.key",
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
            "rfid": d.get("rfid")})
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
        "rfid": d.get("rfid")
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
            "currentStation": d.get("currentStation"),
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
        "currentStation": d.get("currentStation"),
        "history": d.get("history", [])
    }
    return jsonify(bike), 200

@app.route("/smartpedals/api/bikes", methods=["POST"])
def create_bike():
    bike_data = request.get_json()

    # Force status to be available
    bike_data["status"] = "available"

    # Station need to exist and be empty
    station_id = bike_data.get("currentStation")
    now = datetime.now(BRUSSELS)
    if station_id:
        station = stations_col.find_one({"stationId": station_id})
        if not station:
            return jsonify({"status": "error", "message": f"Station '{station_id}' not found"}), 400
        if station.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Station '{station_id}' is already occupied"}), 400
    try:
        result = bikes_col.insert_one(bike_data)

        # Mark that station as now this bike
        if station_id:
            stations_col.update_one({"stationId": station_id}, {"$set": {"currentBike": bike_data["bikeId"]}, "$push": {"history": {
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

    # Ty to change station
    new_station = update.get("currentStation")
    now = datetime.now(BRUSSELS)
    if new_station is not None:
        station = stations_col.find_one({"stationId": new_station})
        if not station:
            return jsonify({"status": "error", "message": f"Station '{new_station}' not found"}), 400
        if station.get("currentBike") is not None:
            return jsonify({"status": "error", "message": f"Station '{new_station}' is already occupied"}), 400
    try:
        # Get the old station
        bike = bikes_col.find_one({"bikeId": bike_id})

        # Don't update a bike if it is in use
        if bike.get("status") == "in_use" or bike.get("currentUser") is not None:
            return jsonify({"status": "error", "message": f"Cannot change station while bike '{bike_id}' is in use"}), 400

        old_station = bike.get("currentStation")

        res = bikes_col.update_one(
            {"bikeId": bike_id},
            {"$set": update}
        )
        if res.matched_count:
            # Free old slot and occupy a new one
            if new_station is not None:
                if old_station:
                    stations_col.update_one({"stationId": old_station}, {"$set": {"currentBike": None}, "$push": {"history": {
                        "bikeId": bike_id,
                        "action": "undock",
                        "timestamp": now}}})
                    stations_col.update_one({"stationId": new_station}, {"$set": {"currentBike": bike_id}, "$push": {"history": {
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

    # Undock from the station
    now = datetime.now(BRUSSELS)
    old_station = bike.get("currentStation")
    if old_station:
        stations_col.update_one({"stationId": old_station}, {"$set": {"currentBike": None}, "$push": {"history": {
            "bikeId": bike_id,
            "action": "undock",
            "timestamp": now}}})
    result = bikes_col.delete_one({"bikeId": bike_id})
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
            "id": str(d["_id"]),
            "stationId": d.get("stationId"),
            "location": d.get("location"),
            "currentBike": d.get("currentBike"),
            "history": d.get("history", [])
        })
    return jsonify(stations), 200

@app.route("/smartpedals/api/stations/<string:station_id>", methods=["GET"])
def get_station(station_id):
    d = stations_col.find_one({"stationId": station_id})
    if not d:
        return jsonify({"status": "not_found"}), 404
    station = {
        "id": str(d["_id"]),
        "stationId": d.get("stationId"),
        "location": d.get("location"),
        "currentBike": d.get("currentBike"),
        "history": d.get("history", [])
    }
    return jsonify(station), 200

@app.route("/smartpedals/api/stations", methods=["POST"])
def create_station():
    station_data = request.get_json()
    try:
        result = stations_col.insert_one(station_data)
        return jsonify({"status": "success", "id": str(result.inserted_id)}), 201
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 400

@app.route("/smartpedals/api/stations/<string:station_id>", methods=["DELETE"])
def delete_station(station_id):
    station = stations_col.find_one({"stationId": station_id})

    # Don't delete if there is a bike
    if station.get("currentBike") is not None:
        return jsonify({"status": "error", "message": f"Cannot delete station '{station_id}' while a bike is docked"}), 400
    result = stations_col.delete_one({"stationId": station_id})
    if result.deleted_count:
        return jsonify({"status": "deleted"}), 200
    return jsonify({"status": "not_found"}), 404

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
    city = request.args.get("city", "Angleur")
    weather = None
    if not OPENWEATHER_API_KEY:
        app.logger.error("OPENWEATHER_API_KEY not configured")
    else:
        url = "https://api.openweathermap.org/data/2.5/weather"
        params = {
            "q": f"{city},BE",
            "appid": OPENWEATHER_API_KEY,
            "units": "metric",
            "lang": "en"
        }
        try:
            resp = requests.get(url, params=params, timeout=5)
            resp.raise_for_status()
            weather = resp.json()
        except requests.RequestException as e:
            app.logger.error(f"Error weather API: {e}")

    return render_template("weather.html", weather=weather, city=city)

if __name__ == "__main__":
    app.run(debug=True, use_reloader=False, threaded=True, host="0.0.0.0")
