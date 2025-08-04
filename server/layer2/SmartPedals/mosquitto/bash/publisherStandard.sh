#!/usr/bin/env bash
# This script publishes a message to an MQTT topic using mosquitto_pub
mosquitto_pub -h localhost -p 1883 -t "test/topic" -m "Hello MQTT!"
