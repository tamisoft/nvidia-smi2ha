"""Core logic for nvidia-smi2ha: config, aggregation, scheduling, watching.

No MQTT, subprocess, or threading here so everything is unit-testable.
"""

import math
import os
import statistics
from collections import deque

import yaml

SENSOR_KEYS = (
    "pwr", "gtemp", "mtemp", "sm", "mem", "enc", "dec", "jpg", "ofa",
    "mclk", "pclk", "pci", "rxpci", "txpci",
    "pviol", "tviol", "fb", "bar1",
    "ccpm", "sbecc", "dbecc",
)

SENSOR_DESCRIPTIONS = {
    "pwr": {"name": "Power Usage", "device_class": "power", "unit": "W"},
    "gtemp": {"name": "GPU Temp", "device_class": "temperature", "unit": "°C"},
    "mtemp": {"name": "Memory Temp", "device_class": "temperature", "unit": "°C"},
    "sm": {"name": "SM Util", "unit": "%"},
    "mem": {"name": "Memory Util", "unit": "%"},
    "enc": {"name": "Encoder Util", "unit": "%"},
    "dec": {"name": "Decoder Util", "unit": "%"},
    "jpg": {"name": "JPEG Util", "unit": "%"},
    "ofa": {"name": "Optical Flow Util", "unit": "%"},
    "mclk": {"name": "Memory Clock", "device_class": "frequency", "unit": "MHz"},
    "pclk": {"name": "Processor Clock", "device_class": "frequency", "unit": "MHz"},
    "pci": {"name": "PCI Throughput", "device_class": "data_rate", "unit": "MB/s"},
    "rxpci": {"name": "PCI RX", "device_class": "data_rate", "unit": "MB/s"},
    "txpci": {"name": "PCI TX", "device_class": "data_rate", "unit": "MB/s"},
    "pviol": {"name": "Power Violation", "unit": "%"},
    "tviol": {"name": "Thermal Violation", "unit": "%"},
    "fb": {"name": "Frame Buffer", "unit": "MB"},
    "bar1": {"name": "BAR1 Memory", "unit": "MB"},
    "ccpm": {"name": "Compute Cluster Memory", "unit": "MB"},
    "sbecc": {"name": "Single Bit ECC Errors", "unit": "errors"},
    "dbecc": {"name": "Double Bit ECC Errors", "unit": "errors"},
}
VALID_TYPES = ("last", "mean", "median", "max", "min")
BUILT_IN_DEFAULTS = {"interval": 10.0, "type": "last"}


class ConfigError(Exception):
    pass


def built_in_config():
    return {
        "defaults": dict(BUILT_IN_DEFAULTS),
        "sensors": {key: dict(BUILT_IN_DEFAULTS) for key in SENSOR_KEYS},
        "disabled": frozenset(),
    }


def _valid_interval(value):
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and value >= 1
            and math.isfinite(value))


def _load_defaults(raw_defaults):
    defaults = dict(BUILT_IN_DEFAULTS)
    if not isinstance(raw_defaults, dict):
        return defaults
    if raw_defaults.get("interval") is not None:
        if _valid_interval(raw_defaults["interval"]):
            defaults["interval"] = float(raw_defaults["interval"])
        else:
            print(f"WARNING: invalid defaults.interval, "
                  f"using {defaults['interval']}")
    if raw_defaults.get("type") is not None:
        if raw_defaults["type"] in VALID_TYPES:
            defaults["type"] = raw_defaults["type"]
        else:
            print(f"WARNING: invalid defaults.type, "
                  f"using {defaults['type']}")
    return defaults


def _load_sensor(key, entry, defaults):
    setting = dict(defaults)
    if not isinstance(entry, dict):
        print(f"WARNING: sensor '{key}' entry must be a mapping, "
              f"using defaults")
        return setting
    if entry.get("interval") is not None:
        if _valid_interval(entry["interval"]):
            setting["interval"] = float(entry["interval"])
        else:
            print(f"WARNING: invalid interval for '{key}', "
                  f"using {defaults['interval']}")
    if entry.get("type") is not None:
        if entry["type"] in VALID_TYPES:
            setting["type"] = entry["type"]
        else:
            print(f"WARNING: invalid type for '{key}', "
                  f"using {defaults['type']}")
    return setting


def load_config(path):
    """Load and validate the YAML config; return the normalized config dict.

    Raises ConfigError if the file is missing, is not valid YAML, or the
    root is not a mapping. Individual bad entries print a WARNING and fall
    back to defaults instead of raising.
    """
    if not os.path.exists(path):
        raise ConfigError(f"config file not found: {path}")
    try:
        with open(path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in {path}: {e}")
    if raw is None:
        return built_in_config()
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a mapping in {path}")

    defaults = _load_defaults(raw.get("defaults"))
    sensors = {key: dict(defaults) for key in SENSOR_KEYS}

    raw_sensors = raw.get("sensors")
    if raw_sensors is not None and not isinstance(raw_sensors, dict):
        raise ConfigError("'sensors' must be a mapping")
    disabled = set()
    for key, entry in (raw_sensors or {}).items():
        if key not in sensors:
            print(f"WARNING: unknown sensor key '{key}' ignored")
            continue
        sensors[key] = _load_sensor(key, entry, defaults)
        if isinstance(entry, dict) and entry.get("enabled") is not None:
            if entry["enabled"] is False:
                disabled.add(key)
            elif entry["enabled"] is not True:
                print(f"WARNING: invalid enabled for '{key}', "
                      f"sensor stays enabled")

    return {"defaults": defaults, "sensors": sensors,
            "disabled": frozenset(disabled)}


def aggregate(samples, now, interval, agg_type):
    """Aggregate (timestamp, value) samples over [now - interval, now].

    None values (dmon '-') are excluded. Returns None when no usable
    sample remains in the window.
    """
    window = [(ts, v) for ts, v in samples
              if v is not None and ts >= now - interval]
    if not window:
        return None
    if agg_type == "last":
        return max(window, key=lambda pair: pair[0])[1]
    values = [v for _, v in window]
    if agg_type == "mean":
        return sum(values) / len(values)
    if agg_type == "median":
        return statistics.median(values)
    if agg_type == "max":
        return max(values)
    return min(values)


def format_value(v):
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return s if s else "0"


class Scheduler:
    """Tracks per-(GPU, sensor) sample buffers and reports due sensors."""

    def __init__(self, config, uuids):
        self._config = config
        self._buffers = {
            (uuid, key): deque()
            for uuid in uuids
            for key in config["sensors"]
        }
        self._next_due = {k: None for k in self._buffers}

    def on_sample(self, uuid, key, ts, value):
        self._buffers[(uuid, key)].append((ts, value))

    def _prune(self, buf, now, interval):
        cutoff = now - interval
        while buf and buf[0][0] < cutoff:
            buf.popleft()

    def tick(self, now):
        due = []
        for (uuid, key), buf in self._buffers.items():
            setting = self._config["sensors"][key]
            self._prune(buf, now, setting["interval"])
            if key in self._config["disabled"]:
                continue
            next_due = self._next_due[(uuid, key)]
            if next_due is not None and now < next_due:
                continue
            value = aggregate(buf, now, setting["interval"], setting["type"])
            if value is not None:
                due.append((uuid, key, value))
                self._next_due[(uuid, key)] = now + setting["interval"]
        return due

    def reload_config(self, config):
        self._config = config
        for key in self._next_due:
            self._next_due[key] = None


class ConfigWatcher:
    """Detects config file changes by mtime. Single-threaded use."""

    def __init__(self, path):
        self._path = path
        self._mtime = os.path.getmtime(path) if os.path.exists(path) else None

    def check(self):
        if not os.path.exists(self._path):
            return None
        mtime = os.path.getmtime(self._path)
        if mtime == self._mtime:
            return None
        try:
            config = load_config(self._path)
        except ConfigError as e:
            print(f"Config reload failed, keeping previous config: {e}")
            self._mtime = mtime
            return None
        self._mtime = mtime
        return config
