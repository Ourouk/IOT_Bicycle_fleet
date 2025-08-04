#!/usr/bin/env bash
# This script subscribes to an MQTT topic using mosquitto_sub
mosquitto_sub -h localhost -p 1883 -t "test/topic"
