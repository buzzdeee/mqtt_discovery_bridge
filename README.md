# OpenBSD Home Assistant MQTT Discovery Bridge for openHAB

A lightweight, performant Python daemon designed to bridge Home Assistant (HA) MQTT Discovery topics directly into native openHAB `.things` configuration files.

While this script can run on any platform supporting Python 3 and MQTT, it has been tailored and optimized specifically for OpenBSD environments, prioritizing:

- File-system safety
- Atomic operations
- Minimal external dependencies
- Strict naming convention safety

---

## What It Does

Many modern smart-home applications (such as Zigbee2MQTT) publish autodiscovery data using the Home Assistant MQTT Discovery format.

openHAB does not natively parse this format without significant manual configuration.

This daemon listens to MQTT discovery topics, automatically decodes Home Assistant discovery payloads, and generates clean, fully mapped native openHAB text configuration files (`.things`) in real time.

---

## Key Features

### Atomic File Writing

Configuration files are written to a temporary file and then atomically moved into place using `os.replace()`.

This guarantees that openHAB never reads a partially written configuration file.

### Smart Transformation Chaining

Automatically chains RegEx and JSONPath transformations:

```text
REGEX(...)∩JSONPATH(...)
```

### Deep Hardware Device Profile Mapping

Supports smart plugs, relay controls, smart bulbs, safety features, power monitoring, color control, dynamic effects, and advanced Zigbee2MQTT device profiles.

### Robust String Sanitization

Automatically converts restrictive or unsafe hardware names into valid openHAB UIDs using only:

```text
[a-zA-Z0-9_]
```

### State Caching

Maintains a lightweight JSON cache file under `/var/db/` to preserve device discovery state across reboots.

---

## Prerequisites

### Python Dependencies

```bash
pip install paho-mqtt
```

### openHAB Transformations

Install:

- JSONPath Transformation
- RegEx Transformation

or configure:

```properties
transformation=jsonpath,regex
```

---

## Installation

```bash
chmod +x /usr/local/bin/mqtt_discovery_bridge.py
```

Configuration:

```python
BROKER_HOST = "127.0.0.1"
BROKER_PORT = 1883
OH_BROKER_ID = "mosquitto"
THINGS_FILE = "/etc/openhab/things/mqtt_autodiscovered.things"
CACHE_FILE = "/var/db/mqtt_discovery_cache.json"
```

---

## Running

```bash
python3 /usr/local/bin/mqtt_discovery_bridge.py
```

### OpenBSD Service

```ksh
#!/bin/ksh

daemon="/usr/local/bin/mqtt_discovery_bridge.py"
daemon_user="root"

. /etc/rc.d/rc.subr

rc_bg=YES
rc_reload=NO

rc_cmd $1
```

Enable:

```bash
rcctl enable mqtt_bridge
rcctl start mqtt_bridge
```

---

## License

MIT License.
