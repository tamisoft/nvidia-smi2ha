#!/bin/env python

import subprocess
import re
import csv
import json
import sys
import paho.mqtt.client as mqtt
import os

# try to connect to mqtt
broker = os.environ.get('MQTT_BROKER', '127.0.0.1')
port = int(os.environ.get('MQTT_PORT', 1883))
username = os.environ.get('MQTT_USERNAME', '')
password = os.environ.get('MQTT_PASSWORD', '')

def main():
    # test if nvidia-smi is in the PATH
    try:
        subprocess.check_output("nvidia-smi", shell=True)
    except subprocess.CalledProcessError as e:
        print("nvidia-smi not found in PATH")
        sys.exit(1)

    # Run the command and get the output of all GPUs in the system
    output = subprocess.check_output("nvidia-smi pci -i 0", shell=True).decode()

    # Use regular expressions to parse the output
    matches = re.findall(r"GPU (\d+): (\w+ \w+) \(UUID: (GPU-[a-z0-9-]+)\)", output)

    gpu_info = {}

    for match in matches:
        gpu_id, gpu_name, gpu_uuid = match
        print(f'gpu_id: {gpu_id}, gpu_name: {gpu_name}, gpu_uuid: {gpu_uuid}')
        gpu_info[str(gpu_id)] = {"name": gpu_name, "uuid": gpu_uuid }

    if not gpu_info:
        print("Could find any GPUs")
        sys.exit(1)

    json.dumps(gpu_info, indent=4)

    client_id = "nvidia-ha-reporter"

    # Create a new MQTT client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id)

    # Set the username and password
    client.username_pw_set(username, password)

    #Set the Last-Will
    client.will_set("nvidia-smi/availability", "offline", 1, True)

    # Set userdata to the gpu_info
    client.user_data_set(gpu_info)
    
    client.on_connect = on_connect
    client.on_message = display_message

    try:
        # Connect to the broker
        client.connect(broker, port)
    except Exception as e:
        print('{} in main'.format(e))
        return 1
    try:
        # Start the MQTT client
        client.loop_start()
    except Exception as e:
        print('{} in mqtt loop_start in main'.format(e))
        return 1

    # Run the command and get the output
    process = subprocess.Popen(["nvidia-smi", "dmon", "--format", "csv", "-s", "pucvmet"], stdout=subprocess.PIPE)

    # Read the headers and units
    headers = process.stdout.readline().decode().replace('#', '').strip().split(', ')
    units = process.stdout.readline().decode().replace('#', '').strip().split(', ')
    print('Sending updates to MQTT')
    while True:
        try:
            # Read a line of output from the process
            line = process.stdout.readline().decode()
        except KeyboardInterrupt:
            print('KeyboardInterrupt in main, cleaning up...')
            # If the user presses Ctrl+C, stop the process and disconnect the MQTT client
            process.terminate()
            
            # Publish the availability as offline
            result = client.publish("nvidia-smi/availability", "offline", 1, True)
            try:
                result.wait_for_publish(timeout=5)
            except Exception as e:
                print('{} in shutdown, will forcefully stop the loop.'.format(e))
            
            # Stop the MQTT client
            client.loop_stop()
            print('Exiting...')
            break

        # If the line is empty, the process has ended
        if not line:
            break

        # If the line starts with a # it is a repeated header/units, skip it
        if line.startswith("#"):
            continue

        # Split the line into values
        values = line.strip().split(', ')

        ret = parse_csv_data(headers, units, values)
        # Parse the CSV data and print the JSON object
        # print(ret)
        # Publish the JSON object to the MQTT broker
        gpu_uuid = gpu_info[str(gpu_id)]["uuid"]
        topic = f'nvidia-smi/{gpu_uuid}'

        client.publish(topic, ret["json_object"])

    # Wait for the process to finish
    process.wait()
    return 0

def on_connect(client, userdata, flags, rc, properties=None):
    print(f"Connected with result code {rc}")
    # We subscribe to the homeassistant status updates, so we can publish our state when it becomes online
    client.message_callback_add("homeassistant/status", publish_configs)
    client.subscribe("homeassistant/status")

    # We publish our availability
    publish_configs(client, userdata)

def display_message(mqtt_client: mqtt.Client, userdata, message: mqtt.MQTTMessage):
    print(f"Received unhandled message '{message.payload.decode()}' on topic '{message.topic}'")

def publish_configs(client, userdata, message=None):
    if message:
        print(f"Publishing config because message '{message.payload.decode()}' on topic '{message.topic}'")
    else:
        print('Publishing configurations')
    gpu_info = userdata
    column_json_description = {
        "pwr":{
        "name": "Power Usage",
        "device_class": "power",
        "unit": "W",
        },
        "gtemp":{
        "name": "GPU Temperature",
        "device_class": "temperature",
        "unit": "°C",
        },
        "mtemp":{
        "name": "Memory Temperature",
        "device_class": "temperature",
        "unit": "°C",
        },
        "sm":{
        "name": "SM Utilization",
        "device_class": None,
        "unit": "%",
        },
        "mem":{
        "name": "Memory Utilization",
        "device_class": None,
        "unit": "%",
        },
        "enc":{
        "name": "Encoder Utilization",
        "device_class": None,
        "unit": "%",
        },
        "dec":{
        "name": "Decoder Utilization",
        "device_class": None,
        "unit": "%",
        },
        "jpg":{
        "name": "JPEG Utilization",
        "device_class": None,
        "unit": "%",
        },
        "ofa":{
        "name": "Optical Flow Utilization",
        "device_class": None,
        "unit": "%",
        },
        "mclk":{
        "name": "Memory Clock",
        "device_class": "frequency",
        "unit": "MHz",
        },
        "pclk":{
        "name": "Processor Clock",
        "device_class": "frequency",
        "unit": "MHz",
        },
    "pviol":{
        "name": "Power Violation",
        "device_class": None,
        "unit": None,
        },
        "tviol":{
        "name": "Thermal Violation",
        "device_class": None,
        "unit": None,
        },
        "fb":{
        "name": "Frame Buffer",
        "device_class": "data_size",
        "unit": "MB",
        },
        "bar1":{
        "name": "BAR1 Memory",
        "device_class": "data_size",
        "unit": "MB",
        },
        "ccpm":{
        "name": "Compute Cluster Memory",
        "device_class": "data_size",
        "unit": "MB",
        },
        "sbecc":{
        "name": "Single Bit ECC Errors",
        "device_class": None,
        "unit": "errors",
        },
        "dbecc":{
        "name": "Double Bit ECC Errors",
        "device_class": None,
        "unit": "errors",
        },
        "pci":{
        "name": "PCI Throughput",
        "device_class": "data_rate",
        "unit": "MB/s",
        },
        "rxpci":{
        "name": "PCI Receive Throughput",
        "device_class": "data_rate",
        "unit": "MB/s",
        },
        "txpci":{
        "name": "PCI Transmit Throughput",
        "device_class": "data_rate",
        "unit": "MB/s",
        },
    }

    # first create the each message for the gpu_uuid/config topic
    for gpu_id in gpu_info:
        gpu_uuid = gpu_info[gpu_id]["uuid"]
        for column_key in column_json_description:
            topic = f'homeassistant/sensor/{gpu_uuid}_{column_key}/config'
            column_description = column_json_description[column_key]
            message = {
                "device": {
                    "name": "GPU",
                    "identifiers": [ gpu_uuid ],
                    "manufacturer": "NVIDIA",
                    "model": gpu_info[gpu_id]["name"]
                },
                "name": column_description["name"],
                "device_class": column_description["device_class"],
                "value_template": "{{ value_json."+column_key+" }}",
                "unit_of_measurement": column_description["unit"],
                "unique_id": gpu_uuid+"_"+column_key,
                "state_class": "measurement",
                "expire_after": 60,
                "enabled_by_default": True,
                "availability_topic": f"nvidia-smi/availability",
                "state_topic": f"nvidia-smi/{gpu_uuid}",
            }
            client.publish(topic, json.dumps(message, indent=4))

    # Publish our online status
    topic = f'nvidia-smi/availability'
    client.publish(topic, f'online', 1, True)


# Function to parse the CSV data
def parse_csv_data(headers, units, values):
    # Combine the headers, units and values into a dictionary
    data = {header: f"{value}" for header, unit, value in zip(headers, units, values)}
    gpu_id = data["gpu"]
    del data["gpu"]
    # filter the json object so that if a value is "-" then change it to 'null'
    for key in data:
        if data[key] == '-':
            data[key] = None
    
    # Convert the dictionary into a JSON object
    json_object = json.dumps(data, indent=4)

    return {"gpu_id": gpu_id, "json_object": json_object}

if __name__ == "__main__":
    print('*** Starting SIA2HA ***')
    print()
    sys.exit(main())
