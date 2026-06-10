#!/usr/bin/env python3
import json
import re
import os
import paho.mqtt.client as mqtt
from collections import defaultdict

# --- CONFIGURATION ---
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
OH_BROKER_ID = "mosquitto"  # MUST match your openHAB MQTT Broker Thing ID
THINGS_FILE = "/etc/openhab/things/mqtt_autodiscovered.things"
CACHE_FILE = "/var/db/mqtt_discovery_cache.json"

discovered_devices = defaultdict(lambda: {"name": "Unknown Device", "channels": {}})

# Load persistent cache if it exists on boot
if os.path.exists(CACHE_FILE):
    try:
        with open(CACHE_FILE, "r") as f:
            discovered_devices = json.load(f)
    except Exception:
        pass

def clean_id(s):
    """Sanitizes strings to obey openHAB's restrictive naming conventions."""
    return re.sub(r'[^a-zA-Z0-9_]', '_', s).strip('_')

def parse_jsonpath(value_template):
    """Translates Home Assistant Jinja templates to openHAB JSONPATH targets."""
    if not value_template:
        return ""
    match = re.search(r'value_json\.([a-zA-Z0-9_]+)', value_template)
    if match:
        return f"JSONPATH:$.{match.group(1)}"
    return ""

def write_openhab_config():
    """Generates the .things file atomically to avoid openHAB parsing half-written states."""
    tmp_file = THINGS_FILE + ".tmp"
    with open(tmp_file, "w") as f:
        f.write("// Automatically generated via Home Assistant MQTT Discovery Bridge\n")
        f.write("// Tailored natively for OpenBSD environments. Do not modify manually.\n\n")
        
        for thing_id, dev in discovered_devices.items():
            friendly_name = dev.get("label", thing_id)
            f.write(f'Thing mqtt:topic:{OH_BROKER_ID}:{thing_id} "{friendly_name}" (mqtt:broker:{OH_BROKER_ID}) {{\n')
            f.write("    Channels:\n")
            for channel_line in dev["channels"].values():
                f.write(f"{channel_line}\n")
            f.write("}\n\n")
            
    os.replace(tmp_file, THINGS_FILE)
    with open(CACHE_FILE, "w") as f:
        json.dump(discovered_devices, f)

def on_message(client, userdata, msg):
    # Topic layout: homeassistant/<component>/<node_id>/<object_id>/config
    parts = msg.topic.split('/')
    if len(parts) < 5 or parts[-1] != 'config':
        return

    component = parts[1]
    node_id = clean_id(parts[2])
    object_id = clean_id(parts[3])

    # If payload is empty, HA handles this as a deletion notice
    if not msg.payload:
        if node_id in discovered_devices and object_id in discovered_devices[node_id]["channels"]:
            del discovered_devices[node_id]["channels"][object_id]
            if not discovered_devices[node_id]["channels"]:
                del discovered_devices[node_id]
            write_openhab_config()
        return

    try:
        payload = json.loads(msg.payload.decode('utf-8'))
    except Exception:
        return

    device_info = payload.get("device", {})
    
    # Securely retrieve string values, defaulting safely if they are None or missing
    raw_name = payload.get("name")
    if raw_name is None:
        raw_name = object_id

    thing_label = device_info.get("name") or raw_name or node_id
    
    state_topic = payload.get("state_topic", "")
    command_topic = payload.get("command_topic", "")
    
    # Defaults
    oh_type = "string"
    props = [f'stateTopic="{state_topic}"']
    
    # Extract JSON extraction parameters
    json_transform = parse_jsonpath(payload.get("value_template"))
    if json_transform:
        props.append(f'transformationPattern="{json_transform}"')

    # Component Translation Mapping Logic
    if component == "binary_sensor":
        oh_type = "switch"
        # Safely convert to string and escape internal double quotes
        on_val = str(payload.get("payload_on", "true")).replace('"', '\\"')
        off_val = str(payload.get("payload_off", "false")).replace('"', '\\"')
        props.append(f'on="{on_val}"')
        props.append(f'off="{off_val}"')
        
    elif component == "sensor":
        # Identify measurements and numeric metrics vs raw textual streams
        if "unit_of_measurement" in payload or any(x in object_id.lower() for x in ["battery", "temp", "humidity", "volt", "power", "illuminance", "linkquality"]):
            oh_type = "number"
        else:
            oh_type = "string"
            
    elif component in ["switch", "light", "fan", "siren"]:
        oh_type = "switch"
        if command_topic:
            props.append(f'commandTopic="{command_topic}"')
        on_val = str(payload.get("payload_on", "ON")).replace('"', '\\"')
        off_val = str(payload.get("payload_off", "OFF")).replace('"', '\\"')
        props.append(f'on="{on_val}"')
        props.append(f'off="{off_val}"')

        # --- EXTRACT ADVANCED LIGHT BULB CONTROLS ---
        if component == "light":
            # 1. Handle Brightness Dimmer Slider
            if payload.get("brightness") or "brightness_command_topic" in payload:
                b_state = payload.get("brightness_state_topic", state_topic)
                b_cmd = payload.get("brightness_command_topic", f"{state_topic}/set")
                b_trans = parse_jsonpath(payload.get("brightness_value_template")) or "JSONPATH:$.brightness"
                
                b_props = [f'stateTopic="{b_state}"', f'commandTopic="{b_cmd}"', f'transformationPattern="{b_trans}"', 'min="0"', 'max="254"']
                b_entry = f'        Type dimmer : {object_id}_brightness "Brightness" [ {", ".join(b_props)} ]'
                discovered_devices[node_id]["channels"][f"{object_id}_brightness"] = b_entry

            # 2. Handle Color Temperature Slider (Mireds)
            if any(k in payload for k in ["color_temp", "color_temp_command_topic", "max_mireds", "min_mireds"]):
                ct_state = payload.get("color_temp_state_topic", state_topic)
                ct_cmd = payload.get("color_temp_command_topic", f"{state_topic}/set")
                ct_trans = parse_jsonpath(payload.get("color_temp_value_template")) or "JSONPATH:$.color_temp"
                
                # Dynamic extraction of mired limits if provided by the hardware profile
                min_m = payload.get("min_mireds", 150)
                max_m = payload.get("max_mireds", 500)
                
                ct_props = [f'stateTopic="{ct_state}"', f'commandTopic="{ct_cmd}"', f'transformationPattern="{ct_trans}"', f'min="{min_m}"', f'max="{max_m}"']
                ct_entry = f'        Type dimmer : {object_id}_color_temp "Color Temperature" [ {", ".join(ct_props)} ]'
                discovered_devices[node_id]["channels"][f"{object_id}_color_temp"] = ct_entry

            # 3. Handle Full RGB / XY Color Control
            color_modes = payload.get("supported_color_modes", [])
            if "color" in payload or any(mode in color_modes for mode in ["xy", "hs", "rgb"]):
                c_state = payload.get("color_state_topic", state_topic)
                c_cmd = payload.get("color_command_topic", f"{state_topic}/set")
                
                # openHAB's MQTT binding expects a specialized JSON format for color transitions
                # formatBeforePublish sends: {"color":{"r":X,"g":Y,"b":Z}} or {"color":{"x":X,"y":Y}}
                # Zigbee2MQTT natively accepts {"color":{"h":H,"s":S,"b":B}} (HSB) which openHAB supplies
                c_props = [
                    f'stateTopic="{c_state}"',
                    f'commandTopic="{c_cmd}"',
                    'transformationPattern="JSONPATH:$.color"',
                    'formatBeforePublish="{\\"color\\":{\\"hsb\\":\\"%s\\"}}"'
                ]
                c_entry = f'        Type color : {object_id}_color "Color Control" [ {", ".join(c_props)} ]'
                discovered_devices[node_id]["channels"][f"{object_id}_color"] = c_entry

        # --- UNIVERSAL DYNAMIC EFFECTS EXTRACTOR ---
        if "effect" in payload or "effect_list" in payload:
            eff_state = payload.get("effect_state_topic", state_topic)
            eff_cmd = payload.get("effect_command_topic", f"{state_topic}/set")
            raw_effect_list = payload.get("effect_list", [])
            
            if raw_effect_list:
                options_array = []
                for eff in raw_effect_list:
                    ui_label = str(eff).replace('_', ' ').title()
                    options_array.append(f"{eff}={ui_label}")
                options_string = ",".join(options_array)
                
                eff_props = [
                    f'stateTopic="{eff_state}"',
                    f'commandTopic="{eff_cmd}"',
                    # This tells openHAB: wrap the string %s in the JSON wrapper before publishing
                    'formatBeforePublish="{\\"effect\\":\\"%s\\"}"',
                    f'commandOptions="{options_string}"'
                ]
            else:
                eff_props = [f'stateTopic="{eff_state}"', f'commandTopic="{eff_cmd}"']
            
            eff_entry = f'        Type string : {object_id}_effect "Effect Control" [ {", ".join(eff_props)} ]'
            discovered_devices[node_id]["channels"][f"{object_id}_effect"] = eff_entry

        # =====================================================================
        # 1. COMPOSITE DETECTOR: INCHING CONTROL SET
        # =====================================================================
        if "inching_control_set" in object_id:
            cmd_topic = f"{state_topic}/set"
            
            # Sub-Control: Inching State Trigger Toggle (ENABLE/DISABLE)
            discovered_devices[node_id]["channels"]["inching_control"] = (
                f'        Type switch : inching_control "Inching Master Switch" [\n'
                f'            stateTopic="{state_topic}", transformationPattern="JSONPATH:$.inching_control_set.inching_control",\n'
                f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"inching_control_set\\": {{\\"inching_control\\": \\"%s\\"}}}}",\n'
                f'            on="ENABLE", off="DISABLE"\n'
                f'        ]'
            )
            # Sub-Control: Inching Mode Direction (ON/OFF)
            discovered_devices[node_id]["channels"]["inching_mode"] = (
                f'        Type switch : inching_mode "Inching Target Mode" [\n'
                f'            stateTopic="{state_topic}", transformationPattern="JSONPATH:$.inching_control_set.inching_mode",\n'
                f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"inching_control_set\\": {{\\"inching_mode\\": \\"%s\\"}}}}",\n'
                f'            on="ON", off="OFF"\n'
                f'        ]'
            )
            # Sub-Control: Inching Auto-off Duration Timer (Numeric Seconds)
            discovered_devices[node_id]["channels"]["inching_time"] = (
                f'        Type number : inching_time "Inching Time Delay" [\n'
                f'            stateTopic="{state_topic}", transformationPattern="JSONPATH:$.inching_control_set.inching_time",\n'
                f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"inching_control_set\\": {{\\"inching_time\\": %s}}}}"\n'
                f'        ]'
            )

        # =====================================================================
        # 2. COMPOSITE DETECTOR: OVERLOAD/OUTLET PROTECTION
        # =====================================================================
        elif "overload_protection" in object_id or "outlet_control_protect" in object_id:
            cmd_topic = f"{state_topic}/set"
            
            # Whitelist of fields actually supported by Sonoff hardware profiles
            supported_switches = ["enable_min_power", "enable_min_voltage", "enable_min_current"]
            supported_numbers = [
                ("max_power", "W"), ("min_power", "W"),
                ("max_voltage", "V"), ("min_voltage", "V"),
                ("max_current", "A"), ("min_current", "A")
            ]

            # 1. Generate Supported Switch Toggles
            for enable_name in supported_switches:
                # Add a REGEX guard directly to the channel to cleanly ignore the key if missing
                discovered_devices[node_id]["channels"][enable_name] = (
                    f'        Type switch : {enable_name} "Protect {enable_name.replace("enable_", "").replace("_", " ").title()} Enable" [\n'
                    f'            stateTopic="{state_topic}", transformationPattern="REGEX:(.*{enable_name}.*)∩JSONPATH:$.overload_protection.{enable_name}",\n'
                    f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"overload_protection\\": {{\\"{enable_name}\\": \\"%s\\"}}}}",\n'
                    f'            on="ENABLE", off="DISABLE"\n'
                    f'        ]'
                )

            # 2. Generate Supported Numeric Threshold Sliders
            for field_name, unit in supported_numbers:
                discovered_devices[node_id]["channels"][field_name] = (
                    f'        Type number : {field_name} "Protect {field_name.replace("_", " ").title()} Threshold" [\n'
                    f'            stateTopic="{state_topic}", transformationPattern="REGEX:(.*{field_name}.*)∩JSONPATH:$.overload_protection.{field_name}",\n'
                    f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"overload_protection\\": {{\\"{field_name}\\": %s}}}}",\n'
                    f'            unit="{unit}"\n'
                    f'        ]'
                )
            
            # 3. Master Protection Safety Switch
            discovered_devices[node_id]["channels"]["outlet_control_protect"] = (
                f'        Type switch : outlet_control_protect "Master Protection Safety" [\n'
                f'            stateTopic="{state_topic}", transformationPattern="JSONPATH:$.outlet_control_protect",\n'
                f'            commandTopic="{cmd_topic}", formatBeforePublish="{{\\"outlet_control_protect\\": %s}}",\n'
                f'            on="true", off="false"\n'
                f'        ]'
            )


    # Guard string cleaning transformations against NoneType exceptions
    chan_label = str(raw_name).replace(str(thing_label), "").strip().title()
    if not chan_label:
        chan_label = object_id.title()
        
    channel_entry = f'        Type {oh_type} : {object_id} "{chan_label}" [ {", ".join(props)} ]'

    # Commit layout to runtime dictionary
    if node_id not in discovered_devices:
        discovered_devices[node_id] = {"label": thing_label, "channels": {}}
    
    discovered_devices[node_id]["channels"][object_id] = channel_entry
    write_openhab_config()
    print(f"Discovered and mapped channel: {thing_label} -> {chan_label}")

# Initialize and run MQTT listener using the updated Paho MQTT v2 Callback API
client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
client.on_message = on_message
client.connect(BROKER_HOST, BROKER_PORT, 60)
client.subscribe("homeassistant/+/+/+/config")
print("OpenBSD HA-Discovery Bridge listening on Mosquitto...")
client.loop_forever()
