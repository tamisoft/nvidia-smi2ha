#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt

from smi2ha_core import (
    SENSOR_DESCRIPTIONS,
    SENSOR_KEYS,
    ConfigError,
    ConfigWatcher,
    Scheduler,
    built_in_config,
    format_value,
    load_config,
)

# MQTT Setup
broker = os.environ.get('MQTT_BROKER', '127.0.0.1')
port = int(os.environ.get('MQTT_PORT', 1883))
username = os.environ.get('MQTT_USERNAME', '')
password = os.environ.get('MQTT_PASSWORD', '')

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "config.yaml")
AVAILABILITY_TOPIC = "nvidia-smi/availability"


def get_nvidia_gpus():
    try:
        result = subprocess.run(
            ['nvidia-smi', '--query-gpu=index,name,uuid', '--format=csv,noheader'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
            text=True
        )
        gpus = []
        for line in result.stdout.strip().splitlines():
            index, name, uuid = line.strip().split(',', 2)
            gpus.append({
                'index': int(index.strip()),
                'name': name.strip(),
                'uuid': uuid.strip()
            })
        return gpus
    except subprocess.CalledProcessError as e:
        print("Error running nvidia-smi:", e.stderr)
        return []


def publish_configs(client, gpus, config):
    for gpu in gpus:
        uuid = gpu['uuid']
        for key in SENSOR_KEYS:
            if key in config["disabled"]:
                client.publish(f"homeassistant/sensor/{uuid}_{key}/config",
                               "", retain=True)
                continue
            desc = SENSOR_DESCRIPTIONS[key]
            interval = config["sensors"][key]["interval"]
            payload = {
                "device": {
                    "name": gpu["name"],
                    "identifiers": [uuid],
                    "manufacturer": "NVIDIA",
                    "model": gpu["name"]
                },
                "name": desc["name"],
                "unit_of_measurement": desc["unit"],
                "unique_id": f"{uuid}_{key}",
                "state_class": "measurement",
                "expire_after": max(int(interval * 3), 60),
                "enabled_by_default": True,
                "availability_topic": AVAILABILITY_TOPIC,
                "state_topic": f"nvidia-smi/{uuid}/{key}"
            }
            if desc.get("device_class"):
                payload["device_class"] = desc["device_class"]
            client.publish(f"homeassistant/sensor/{uuid}_{key}/config",
                           json.dumps(payload), retain=True)

    client.publish(AVAILABILITY_TOPIC, "online", retain=True)


def _to_float(value):
    if value == '-':
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_row(headers, values):
    data = dict(zip(headers, values))
    gpu_id = data.pop("gpu", None)
    row = {key: _to_float(v) for key, v in data.items()}
    return gpu_id, row


def reader_loop(proc, index_to_uuid, scheduler, stop_event):
    try:
        headers = proc.stdout.readline().decode().strip('# \n').split(', ')
        proc.stdout.readline()  # units line, ignored
        while True:
            line = proc.stdout.readline()
            if not line:
                print("nvidia-smi dmon ended unexpectedly")
                break
            text = line.decode().strip()
            if not text or text.startswith('#'):
                continue
            gpu_id, row = parse_row(headers, text.split(', '))
            uuid = index_to_uuid.get(gpu_id)
            if not uuid:
                continue
            ts = time.monotonic()
            for key in SENSOR_KEYS:
                if key in row:
                    scheduler.on_sample(uuid, key, ts, row[key])
    finally:
        stop_event.set()


def main():
    sys.stdout.reconfigure(line_buffering=True)
    print("*** Starting NVIDIA MQTT Exporter ***")

    gpus = get_nvidia_gpus()
    if not gpus:
        print("No GPUs found")
        return 1

    try:
        config = load_config(CONFIG_PATH)
    except ConfigError as e:
        print(f"Using built-in config defaults: {e}")
        print(f"(create {CONFIG_PATH} to customise; see config.yaml.example)")
        config = built_in_config()

    holder = {"config": config}

    def on_ha_status(client, userdata, message):
        print(f"Re-publishing configs on HA request: {message.payload.decode()}")
        publish_configs(client, gpus, holder["config"])

    def on_connect(client, userdata, flags, rc, properties=None):
        print(f"Connected with result code {rc}")
        client.message_callback_add("homeassistant/status", on_ha_status)
        client.subscribe("homeassistant/status")
        publish_configs(client, gpus, holder["config"])

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "nvidia-ha-reporter")
    client.username_pw_set(username, password)
    client.will_set(AVAILABILITY_TOPIC, "offline", retain=True)
    client.user_data_set(gpus)
    client.on_connect = on_connect
    client.on_message = (lambda c, u, m:
                         print(f"Unhandled message on {m.topic}: "
                               f"{m.payload.decode()}"))

    try:
        client.connect(broker, port)
        client.loop_start()
    except Exception as e:
        print(f"MQTT connection error: {e}")
        return 1

    index_to_uuid = {str(g["index"]): g["uuid"] for g in gpus}
    scheduler = Scheduler(config, [g["uuid"] for g in gpus])
    watcher = ConfigWatcher(CONFIG_PATH)
    stop_event = threading.Event()

    proc = subprocess.Popen(
        ["nvidia-smi", "dmon", "--format", "csv", "-s", "pucvmet"],
        stdout=subprocess.PIPE
    )
    reader = threading.Thread(
        target=reader_loop,
        args=(proc, index_to_uuid, scheduler, stop_event),
        daemon=True
    )
    reader.start()

    print("Monitoring GPUs and sending MQTT updates...")
    try:
        while not stop_event.is_set():
            now = time.monotonic()
            for uuid, key, value in scheduler.tick(now):
                client.publish(f"nvidia-smi/{uuid}/{key}", format_value(value))
            new_config = watcher.check()
            if new_config is not None:
                print("Config changed, reloading")
                holder["config"] = new_config
                scheduler.reload_config(new_config)
                publish_configs(client, gpus, new_config)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("Shutting down...")
    finally:
        proc.terminate()
        proc.wait()
        client.publish(AVAILABILITY_TOPIC, "offline", retain=True)
        client.loop_stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
