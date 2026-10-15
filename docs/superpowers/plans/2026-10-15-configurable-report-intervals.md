# Configurable Report Intervals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user configure, per sensor, in `config.yaml`, the report interval and aggregation type (last/mean/median/max/min) so Home Assistant no longer receives 1 Hz updates for every metric.

**Architecture:** Testable logic lives in a new importable module `smi2ha_core.py` (config loading/validation, aggregation, per-sensor scheduler, config file watcher). `nvidia-smi2ha.py` becomes a thin entry point: a reader thread parses `nvidia-smi dmon` rows into per-(GPU, sensor) buffers; a 0.2 s main loop asks the scheduler which sensors are due, publishes per-sensor MQTT topics, and hot-reloads the config when its mtime changes.

**Tech Stack:** Python 3 (repo venv), paho-mqtt (CallbackAPIVersion.VERSION2), PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-configurable-report-intervals-design.md`

## Global Constraints

- Sensor keys (exactly 14): `pwr gtemp mtemp sm mem enc dec jpg ofa mclk pclk pci rxpci txpci`
- Aggregation types (exactly 5): `last mean median max min`
- Built-in defaults: `interval: 10` seconds, `type: last`
- Config path: `<repo>/config.yaml`; watched by mtime; an invalid reload keeps the previous config
- State topic per sensor: `nvidia-smi/{uuid}/{key}`, plain numeric payload, no `value_template`
- HA config topic: `homeassistant/sensor/{uuid}_{key}/config`; `unique_id` stays `{uuid}_{key}`
- `expire_after` = `max(3 * interval, 60)` seconds
- dmon samples ~1 Hz; a window contains the samples with `timestamp >= now - interval`
- dmon `-` values are `None`, excluded from aggregation; an all-`None` window reports nothing
- All tests run from the repo root with the repo venv: `venv/bin/pytest`
- One commit per task, descriptive subject line

## File Structure

| File | Responsibility |
|---|---|
| `smi2ha_core.py` (new) | Pure logic: `load_config`, `built_in_config`, `ConfigError`, `aggregate`, `format_value`, `Scheduler`, `ConfigWatcher`. No MQTT, no subprocess, no threads — fully unit-testable |
| `nvidia-smi2ha.py` (rewritten) | Entry point: env/MQTT setup, HA auto-discovery publishing, dmon reader thread, main loop, wiring of the core units |
| `config.yaml.example` (new) | Commented example config for users |
| `requirements.txt` (modified) | Add `PyYAML` |
| `requirements-dev.txt` (new) | `pytest` (NOT installed by the service's ExecStartPre) |
| `pytest.ini` (new) | `pythonpath = .` so tests can import `smi2ha_core` |
| `tests/test_config.py`, `tests/test_aggregate.py`, `tests/test_scheduler.py`, `tests/test_watcher.py` (new) | Unit tests |
| `README.md` (modified) | Document configuration |

Note: `nvidia-smi2ha.py` has a hyphen and cannot be imported by tests, hence the new `smi2ha_core.py` module. This is a targeted improvement, not the full multi-file refactor listed in the README TODO.

---

### Task 1: Scaffolding + config loading (`load_config`)

**Files:**
- Create: `smi2ha_core.py`
- Create: `pytest.ini`
- Create: `requirements-dev.txt`
- Create: `tests/test_config.py`
- Modify: `requirements.txt`

**Interfaces:**
- Consumes: nothing (first task)
- Produces:
  - `SENSOR_KEYS: tuple` — the 14 sensor keys
  - `VALID_TYPES: tuple` — `("last", "mean", "median", "max", "min")`
  - `class ConfigError(Exception)`
  - `built_in_config() -> dict` — normalized config, all sensors at built-in defaults
  - `load_config(path: str) -> dict` — normalized config; raises `ConfigError` on missing file, invalid YAML, or non-mapping root; prints `WARNING: ...` for unknown/bad entries (which fall back to defaults)
  - Normalized config shape: `{"defaults": {"interval": float, "type": str}, "sensors": {key: {"interval": float, "type": str} for all 14 keys}}`

- [ ] **Step 1: Add dependencies and install into the venv**

Replace `requirements.txt` content with:

```
paho-mqtt
PyYAML
```

Create `requirements-dev.txt`:

```
pytest
```

Create `pytest.ini`:

```ini
[pytest]
pythonpath = .
```

Run:

```bash
venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

Expected: install succeeds (paho-mqtt already satisfied; PyYAML and pytest installed).

- [ ] **Step 2: Write the failing tests**

Create `tests/test_config.py`:

```python
import pytest

from smi2ha_core import SENSOR_KEYS, ConfigError, built_in_config, load_config


def write(tmp_path, text):
    p = tmp_path / "config.yaml"
    p.write_text(text)
    return str(p)


def test_missing_file_raises():
    with pytest.raises(ConfigError):
        load_config("/nonexistent/config.yaml")


def test_invalid_yaml_raises(tmp_path):
    p = write(tmp_path, "sensors: [unclosed")
    with pytest.raises(ConfigError):
        load_config(p)


def test_non_mapping_root_raises(tmp_path):
    p = write(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(ConfigError):
        load_config(p)


def test_empty_file_gives_built_in_config(tmp_path):
    p = write(tmp_path, "")
    assert load_config(p) == built_in_config()


def test_built_in_config_shape():
    cfg = built_in_config()
    assert set(cfg["sensors"]) == set(SENSOR_KEYS)
    for setting in cfg["sensors"].values():
        assert setting == {"interval": 10.0, "type": "last"}


def test_full_config(tmp_path):
    p = write(tmp_path, """
defaults:
  interval: 5
  type: median
sensors:
  pwr:
    interval: 60
    type: mean
  gtemp:
    interval: 30
""")
    cfg = load_config(p)
    assert cfg["sensors"]["pwr"] == {"interval": 60.0, "type": "mean"}
    assert cfg["sensors"]["gtemp"] == {"interval": 30.0, "type": "median"}
    assert cfg["sensors"]["sm"] == {"interval": 5.0, "type": "median"}
    assert cfg["sensors"]["ofa"] == {"interval": 5.0, "type": "median"}


def test_unknown_sensor_key_ignored(tmp_path, capsys):
    p = write(tmp_path, "sensors:\n  foo: {interval: 5, type: last}\n")
    cfg = load_config(p)
    assert "foo" not in cfg["sensors"]
    assert "unknown sensor key 'foo'" in capsys.readouterr().out


def test_invalid_type_falls_back_to_defaults(tmp_path, capsys):
    p = write(tmp_path, "sensors:\n  pwr: {interval: 5, type: rms}\n")
    cfg = load_config(p)
    assert cfg["sensors"]["pwr"] == {"interval": 5.0, "type": "last"}
    assert "invalid type for 'pwr'" in capsys.readouterr().out


def test_invalid_interval_falls_back_to_defaults(tmp_path, capsys):
    p = write(tmp_path, "sensors:\n  pwr: {interval: 0, type: last}\n")
    cfg = load_config(p)
    assert cfg["sensors"]["pwr"] == {"interval": 10.0, "type": "last"}
    assert "invalid interval for 'pwr'" in capsys.readouterr().out


def test_non_mapping_sensor_entry_ignored(tmp_path, capsys):
    p = write(tmp_path, "sensors:\n  pwr: 60\n")
    cfg = load_config(p)
    assert cfg["sensors"]["pwr"] == {"interval": 10.0, "type": "last"}
    assert "sensor 'pwr' entry must be a mapping" in capsys.readouterr().out


def test_invalid_defaults_field_falls_back(tmp_path, capsys):
    p = write(tmp_path, "defaults:\n  interval: -3\n  type: rms\n")
    cfg = load_config(p)
    assert cfg["defaults"] == {"interval": 10.0, "type": "last"}
    out = capsys.readouterr().out
    assert "invalid defaults.interval" in out
    assert "invalid defaults.type" in out
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_config.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'smi2ha_core'`

- [ ] **Step 4: Implement the config section of `smi2ha_core.py`**

Create `smi2ha_core.py`:

```python
"""Core logic for nvidia-smi2ha: config, aggregation, scheduling, watching.

No MQTT, subprocess, or threading here so everything is unit-testable.
"""

import os

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_config.py -v`
Expected: all 11 tests PASS

- [ ] **Step 6: Commit**

```bash
git add requirements.txt requirements-dev.txt pytest.ini smi2ha_core.py tests/test_config.py
git commit -m "Add YAML config loading with validation and fallbacks"
```

---

### Task 2: Aggregation (`aggregate`, `format_value`)

**Files:**
- Modify: `smi2ha_core.py` (append)
- Create: `tests/test_aggregate.py`

**Interfaces:**
- Consumes: nothing from Task 1 (standalone functions)
- Produces:
  - `aggregate(samples, now, interval, agg_type) -> float | None` — `samples` is an iterable of `(timestamp: float, value: float | None)`; considers only entries where `value is not None` and `timestamp >= now - interval`; returns `None` when no usable sample remains
  - `format_value(v: float) -> str` — at most 2 decimals, trailing zeros stripped (`60.0` → `"60"`, `57.5` → `"57.5"`, `0.0` → `"0"`)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_aggregate.py`:

```python
import pytest

from smi2ha_core import aggregate, format_value

S = [(1.0, 10.0), (2.0, 20.0), (3.0, None), (4.0, 30.0)]


def test_last_returns_newest_sample():
    assert aggregate(S, now=5.0, interval=10, agg_type="last") == 30.0


def test_last_skips_none():
    assert aggregate([(1.0, 5.0), (2.0, None)], now=3.0,
                     interval=10, agg_type="last") == 5.0


def test_mean_ignores_none_and_uses_window():
    # all of S is inside [5-10, 5]; the None is excluded
    assert aggregate(S, now=5.0, interval=10, agg_type="mean") == pytest.approx(20.0)


def test_median():
    samples = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 4.0)]
    assert aggregate(samples, now=4.0, interval=10, agg_type="median") == pytest.approx(2.5)


def test_max_and_min():
    assert aggregate(S, now=5.0, interval=10, agg_type="max") == 30.0
    assert aggregate(S, now=5.0, interval=10, agg_type="min") == 10.0


def test_window_excludes_old_samples():
    samples = [(0.0, 100.0), (5.0, 10.0)]
    assert aggregate(samples, now=6.0, interval=2, agg_type="max") == 10.0


def test_empty_samples_returns_none():
    assert aggregate([], now=5.0, interval=10, agg_type="mean") is None


def test_all_none_returns_none():
    assert aggregate([(1.0, None), (2.0, None)], now=5.0,
                     interval=10, agg_type="max") is None


def test_format_value():
    assert format_value(60.0) == "60"
    assert format_value(57.5) == "57.5"
    assert format_value(57.345) in ("57.34", "57.35")
    assert format_value(0.0) == "0"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_aggregate.py -v`
Expected: FAIL with `ImportError: cannot import name 'aggregate'`

- [ ] **Step 3: Implement aggregation in `smi2ha_core.py`**

Add `import statistics` to the top of `smi2ha_core.py` (after `import os`) and append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_aggregate.py -v`
Expected: all 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add smi2ha_core.py tests/test_aggregate.py
git commit -m "Add windowed aggregation: last, mean, median, max, min"
```

---

### Task 3: Scheduler (per-sensor buffers, due-times, reload)

**Files:**
- Modify: `smi2ha_core.py` (append)
- Create: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: normalized config shape from Task 1, `aggregate` from Task 2
- Produces:
  - `Scheduler(config, uuids)` — one sample buffer per `(uuid, sensor_key)`
  - `Scheduler.on_sample(uuid, key, ts, value)` — append `(ts, value)` (value may be `None`)
  - `Scheduler.tick(now) -> list` of `(uuid, key, value)` — sensors whose interval has elapsed (or that never reported) AND that have a usable sample in the window; `next_due` is set to `now + interval` only when a value is actually produced, so gaps retry on the next tick
  - `Scheduler.reload_config(new_config)` — swaps the config and resets all due-times so every sensor with buffered samples reports once immediately

- [ ] **Step 1: Write the failing tests**

Create `tests/test_scheduler.py`:

```python
from smi2ha_core import Scheduler, built_in_config


def make_cfg(**overrides):
    cfg = built_in_config()
    for key, setting in overrides.items():
        cfg["sensors"][key] = setting
    return cfg


def test_no_samples_no_reports():
    s = Scheduler(make_cfg(), ["u1"])
    assert s.tick(1.0) == []


def test_first_sample_reports_immediately():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1"])
    s.on_sample("u1", "pwr", 1.5, 50.0)
    assert s.tick(2.0) == [("u1", "pwr", 50.0)]


def test_no_report_before_interval_elapsed():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1"])
    s.on_sample("u1", "pwr", 1.0, 50.0)
    s.tick(1.5)  # first report, next due 11.5
    s.on_sample("u1", "pwr", 5.0, 60.0)
    assert s.tick(5.5) == []
    s.on_sample("u1", "pwr", 11.0, 70.0)
    assert s.tick(11.6) == [("u1", "pwr", 70.0)]


def test_mean_over_window():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "mean"}), ["u1"])
    for i in range(11):
        s.on_sample("u1", "pwr", float(i), 100.0)
    out = s.tick(11.0)
    # window [1, 11]: samples i=1..10 (10 x 100.0); i=0 is outside
    assert out == [("u1", "pwr", 100.0)]


def test_gap_skips_and_retries_next_tick():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1"])
    s.on_sample("u1", "pwr", 1.0, 50.0)
    assert s.tick(1.5) == [("u1", "pwr", 50.0)]
    assert s.tick(12.0) == []  # window empty: no report, still due
    s.on_sample("u1", "pwr", 13.0, 80.0)
    assert s.tick(13.5) == [("u1", "pwr", 80.0)]


def test_all_none_samples_report_nothing():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1"])
    s.on_sample("u1", "pwr", 1.0, None)
    s.on_sample("u1", "pwr", 2.0, None)
    assert s.tick(2.5) == []


def test_multiple_gpus_independent():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1", "u2"])
    s.on_sample("u1", "pwr", 1.0, 10.0)
    s.on_sample("u2", "pwr", 1.0, 20.0)
    assert s.tick(1.5) == [("u1", "pwr", 10.0), ("u2", "pwr", 20.0)]


def test_reload_reports_immediately_with_new_settings():
    s = Scheduler(make_cfg(pwr={"interval": 10.0, "type": "last"}), ["u1"])
    s.on_sample("u1", "pwr", 1.0, 50.0)
    s.tick(1.5)  # reported, next due 11.5
    s.reload_config(make_cfg(pwr={"interval": 60.0, "type": "mean"}))
    for i in range(2, 62):
        s.on_sample("u1", "pwr", float(i), 100.0)
    out = s.tick(62.0)
    assert ("u1", "pwr", 100.0) in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_scheduler.py -v`
Expected: FAIL with `ImportError: cannot import name 'Scheduler'`

- [ ] **Step 3: Implement `Scheduler` in `smi2ha_core.py`**

Add `from collections import deque` to the top imports and append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_scheduler.py -v`
Expected: all 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add smi2ha_core.py tests/test_scheduler.py
git commit -m "Add per-sensor scheduler with intervals, windows and hot reload"
```

---

### Task 4: Config file watcher (`ConfigWatcher`)

**Files:**
- Modify: `smi2ha_core.py` (append)
- Create: `tests/test_watcher.py`

**Interfaces:**
- Consumes: `load_config`, `ConfigError` from Task 1
- Produces:
  - `ConfigWatcher(path)` — records the file's mtime at construction (absent file: `None`)
  - `ConfigWatcher.check() -> dict | None` — `None` when unchanged or when the load failed (caller keeps the previous config); the new normalized config when the mtime changed and the file is valid. A failed load adopts the new mtime (no per-tick retry spam) and prints the error.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_watcher.py`:

```python
import os

from smi2ha_core import ConfigWatcher


def bump(path):
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 10))


def test_no_change_returns_none(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("sensors:\n  pwr: {interval: 60, type: mean}\n")
    w = ConfigWatcher(str(p))
    assert w.check() is None


def test_change_returns_new_config(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    w = ConfigWatcher(str(p))
    p.write_text("sensors:\n  pwr: {interval: 60, type: mean}\n")
    bump(p)
    cfg = w.check()
    assert cfg is not None
    assert cfg["sensors"]["pwr"] == {"interval": 60.0, "type": "mean"}


def test_invalid_change_returns_none_and_stops_retrying(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    w = ConfigWatcher(str(p))
    p.write_text("sensors: [bad")
    bump(p)
    assert w.check() is None
    assert w.check() is None  # mtime adopted, not re-attempted every tick


def test_valid_change_after_invalid_one_is_picked_up(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("")
    w = ConfigWatcher(str(p))
    p.write_text("sensors: [bad")
    bump(p)
    w.check()
    p.write_text("sensors:\n  pwr: {interval: 30, type: max}\n")
    bump(p)
    cfg = w.check()
    assert cfg is not None
    assert cfg["sensors"]["pwr"] == {"interval": 30.0, "type": "max"}


def test_missing_file_returns_none(tmp_path):
    w = ConfigWatcher(str(tmp_path / "does-not-exist.yaml"))
    assert w.check() is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/pytest tests/test_watcher.py -v`
Expected: FAIL with `ImportError: cannot import name 'ConfigWatcher'`

- [ ] **Step 3: Implement `ConfigWatcher` in `smi2ha_core.py`**

Append:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `venv/bin/pytest tests/test_watcher.py -v`
Expected: all 5 tests PASS

- [ ] **Step 5: Run the whole suite**

Run: `venv/bin/pytest -v`
Expected: all 33 tests PASS

- [ ] **Step 6: Commit**

```bash
git add smi2ha_core.py tests/test_watcher.py
git commit -m "Add mtime-based config watcher with keep-old-on-error reload"
```

---

### Task 5: Rewire the entry point (`nvidia-smi2ha.py`)

**Files:**
- Modify: `nvidia-smi2ha.py` (full rewrite)

**Interfaces:**
- Consumes: `SENSOR_KEYS`, `ConfigError`, `ConfigWatcher`, `Scheduler`, `built_in_config`, `format_value`, `load_config` from `smi2ha_core` (Tasks 1-4)
- Produces: working service. Per-sensor state topics `nvidia-smi/{uuid}/{key}` with plain numeric payloads; HA config with per-sensor `expire_after`.

This task is integration-level; it is verified by running against the real broker, not by unit tests.

- [ ] **Step 1: Rewrite `nvidia-smi2ha.py`**

Replace the entire file with:

```python
#!/usr/bin/env python3

import json
import os
import subprocess
import sys
import threading
import time

import paho.mqtt.client as mqtt

from smi2ha_core import (
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
}


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
```

- [ ] **Step 2: Syntax check**

Run: `venv/bin/python -m py_compile nvidia-smi2ha.py smi2ha_core.py`
Expected: no output (success)

- [ ] **Step 3: Create a temporary test config**

Create `config.yaml` (this file is user-local, not committed):

```yaml
defaults:
  interval: 10
  type: last

sensors:
  pwr:
    interval: 10
    type: mean
  gtemp:
    interval: 30
    type: max
```

- [ ] **Step 4: Run against the real broker and verify cadence**

```bash
set -a; source .env; set +a
venv/bin/python nvidia-smi2ha.py
```

Let it run ~45 s. Expected on stdout: startup lines, `Connected with result code 0`, `Monitoring GPUs and sending MQTT updates...`, no tracebacks.

If `mosquitto_sub` is installed, run in a second terminal (with the same env):

```bash
mosquitto_sub -h "$MQTT_BROKER" -p "$MQTT_PORT" -u "$MQTT_USERNAME" -P "$MQTT_PASSWORD" -t 'nvidia-smi/#' -v
```

Expected: `nvidia-smi/<uuid>/pwr` messages every ~10 s, `.../gtemp` every ~30 s, remaining sensors every ~10 s (default); payloads are plain numbers.

- [ ] **Step 5: Verify hot reload**

While the process is running, change `config.yaml` so `pwr` has `interval: 20`. Within a few seconds stdout shows `Config changed, reloading`, and pwr messages drop to every ~20 s. Then Ctrl-C the process; it should exit cleanly (dmon terminated, offline published).

- [ ] **Step 6: Commit**

```bash
git add nvidia-smi2ha.py
git commit -m "Rewrite entry point with per-sensor scheduling and config hot reload"
```

---

### Task 6: Example config, docs, gitignore

**Files:**
- Create: `config.yaml.example`
- Modify: `README.md`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: the schema implemented in Task 1
- Produces: user-facing documentation

- [ ] **Step 1: Create `config.yaml.example`**

```yaml
# nvidia-smi2ha configuration
#
# Copy to config.yaml (same directory) and edit. The file is watched and
# reloaded automatically - no service restart needed.
#
# Every sensor accepts:
#   interval: seconds between reports (integer >= 1)
#   type:     last | mean | median | max | min
#
# Sensors not listed under `sensors` use `defaults`.
# Without a config.yaml the built-in defaults are: interval 10, type last.
#
# Available sensors:
#   pwr gtemp mtemp sm mem enc dec jpg ofa mclk pclk pci rxpci txpci

defaults:
  interval: 10
  type: last

sensors:
  # Average power draw over the past minute, reported once a minute
  pwr:
    interval: 60
    type: mean
  # Peak GPU / memory temperature over the past minute
  gtemp:
    interval: 60
    type: max
  mtemp:
    interval: 60
    type: max
```

- [ ] **Step 2: Add a Configuration section to `README.md`**

Insert after the "## Usage" section:

```markdown
## Configuration

Reporting is controlled by `config.yaml` in the repository directory
(copy `config.yaml.example` to start). The file is watched and reloaded
automatically; no service restart is needed.

Each of the 14 sensors (`pwr gtemp mtemp sm mem enc dec jpg ofa mclk pclk
pci rxpci txpci`) can set:

- `interval` (seconds, >= 1) — how often the sensor reports
- `type` — `last`, `mean`, `median`, `max` or `min`; the value published is
  this aggregation over the samples collected during the interval

Sensors not listed use `defaults` (built-in: `interval: 10`, `type: last`).
Example: reporting the average power draw once per minute:

```yaml
sensors:
  pwr:
    interval: 60
    type: mean
```
```

Also update the "## TODO" list: mark nothing (refactor item stays), but add a line under "## Features":

```markdown
- Per-sensor configurable report interval and aggregation (last/mean/median/max/min) via `config.yaml`, hot-reloaded
```

- [ ] **Step 3: Ignore the user-local config**

Append to `.gitignore`:

```
config.yaml
```

- [ ] **Step 4: Commit**

```bash
git add config.yaml.example README.md .gitignore
git commit -m "Document config.yaml and add example config"
```

---

### Task 7: Final verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `venv/bin/pytest -v`
Expected: all 33 tests PASS

- [ ] **Step 2: Verify the systemd service end-to-end**

The service was stopped (`systemctl --user stop nvidia-smi2ha.service`). Start it:

```bash
systemctl --user start nvidia-smi2ha.service
sleep 70
journalctl --user -u nvidia-smi2ha -n 40 --no-pager
```

Expected: clean startup, no warnings about config (or only intended WARNINGs if `config.yaml` has issues), no crashes. Then leave the service in the state the user wants (running, or `systemctl --user stop nvidia-smi2ha.service` to match the pre-task state).

- [ ] **Step 3: Confirm git status is clean**

Run: `git status --short`
Expected: only untracked user-local files (`config.yaml`, `venv/` ignored). All work committed.
