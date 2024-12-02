#!/usr/bin/env bash
mosquitto_sub -h localhost -p 1883 -t "test/topic"
