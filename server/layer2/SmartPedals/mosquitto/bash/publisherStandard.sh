#!/usr/bin/env bash
mosquitto_pub -h localhost -p 1883 -t "test/topic" -m "Hello MQTT!"
