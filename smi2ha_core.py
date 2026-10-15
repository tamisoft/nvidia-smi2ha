"""Core logic for nvidia-smi2ha: config, aggregation, scheduling, watching.

No MQTT, subprocess, or threading here so everything is unit-testable.
"""

import os
import statistics

import yaml

SENSOR_KEYS = (
    "pwr", "gtemp", "mtemp", "sm", "mem", "enc", "dec", "jpg", "ofa",
    "mclk", "pclk", "pci", "rxpci", "txpci",
)
VALID_TYPES = ("last", "mean", "median", "max", "min")
BUILT_IN_DEFAULTS = {"interval": 10.0, "type": "last"}


class ConfigError(Exception):
    pass


def built_in_config():
    return {
        "defaults": dict(BUILT_IN_DEFAULTS),
        "sensors": {key: dict(BUILT_IN_DEFAULTS) for key in SENSOR_KEYS},
    }


def _valid_interval(value):
    return (isinstance(value, (int, float))
            and not isinstance(value, bool) and value >= 1)


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
    for key, entry in (raw_sensors or {}).items():
        if key not in sensors:
            print(f"WARNING: unknown sensor key '{key}' ignored")
            continue
        sensors[key] = _load_sensor(key, entry, defaults)

    return {"defaults": defaults, "sensors": sensors}


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
