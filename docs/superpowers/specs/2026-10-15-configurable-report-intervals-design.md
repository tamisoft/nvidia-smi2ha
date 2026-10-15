# Design: Configurable per-sensor report intervals and aggregation types

## Problem

`nvidia-smi2ha.py` runs `nvidia-smi dmon` (1 sample/second/GPU) and publishes every
sample immediately to an all-in-one MQTT topic per GPU. This floods Home Assistant
with state updates (e.g. power usage 60×/min) and makes graphs noisy.

## Goal

Allow the user to configure, per sensor, in a YAML file:

- **Report interval** (seconds) — how often the sensor publishes.
- **Aggregation type** — how the published value is derived from the samples
  collected during the interval: `last`, `mean`, `median`, `max`, `min`.

Example: `pwr` with `interval: 60`, `type: mean` publishes the average power
consumption of the past 60 seconds, once per minute.

## Decisions (from brainstorming)

- Aggregation types: `last`, `mean`, `median`, `max`, `min` (user clarified
  "average/mean" meant `mean` and `median`).
- Sensors not listed in the YAML use sensible defaults (all stay visible in HA).
- Config lives at `$HOME/nvidia-stat/config.yaml` and is **hot-reloaded** (mtime
  watch) without restarting the service.
- Config is **global only** — no per-GPU overrides.
- Architecture: reader thread + scheduler main loop (approach A).

## 1. Config file: `config.yaml`

Location: `<repo>/config.yaml` (service `WorkingDirectory` is the repo). A
commented `config.yaml.example` ships in the repo.

```yaml
defaults:
  interval: 10        # seconds, applied to any sensor not listed
  type: last

sensors:
  pwr:    { interval: 60, type: mean }
  gtemp:  { interval: 60, type: max }
  mtemp:  { interval: 60, type: max }
  sm:     { interval: 10, type: last }
```

Sensor keys (the 14 reported today): `pwr gtemp mtemp sm mem enc dec jpg ofa
mclk pclk pci rxpci txpci`.

Validation rules:

| Situation | Behavior |
|---|---|
| File missing | Run with built-in defaults (`last`, 10s); log a hint |
| Unparseable YAML (startup) | Built-in defaults; log error |
| Unparseable YAML (hot reload) | Keep previous config; log error |
| Unknown sensor key | Warning; key ignored |
| Invalid `interval` / `type` for a sensor | Warning; that sensor falls back to `defaults` |

`defaults` itself is optional; each sensor entry may omit `interval` and/or
`type` (missing fields come from `defaults`, then built-ins).

## 2. Architecture (single script, organized units)

- **Reader thread**: starts `nvidia-smi dmon --format csv -s pucvmet` as today,
  parses rows, pushes each sample as `(timestamp, value)` into a per-GPU,
  per-sensor bounded deque.
  - On EOF (dmon died): log, exit non-zero. systemd `Restart=always` recovers.
    (Also fixes today's infinite busy-spin when `readline()` returns `''`.)
- **Main loop** (0.2 s tick): for each (GPU, sensor), when `now >= next_due`:
  aggregate the deque over the window, publish, set
  `next_due = now + interval`.
- **Config watcher**: mtime check in the main loop. On change: reload config;
  reset all `next_due` (one immediate report per sensor so the new settings
  show up at once); re-publish HA auto-discovery configs (§4).
- **MQTT**: existing paho usage (`loop_start()`, will, availability topic)
  unchanged.

## 3. Aggregation semantics

- Window = the sensor's interval, `[now - interval, now]`.
- `last`: newest sample in window.
- `mean` / `median` / `max` / `min`: computed over samples in the window.
- dmon reports `-` for unavailable values (e.g. `mtemp` on some GPUs); these
  are stored as `None` and **excluded** from aggregation. A window containing
  only `None` samples counts as 0 samples (report skipped).
- Exactly 1 sample in window: that sample's value.
- 0 samples (dmon gap): skip this report, retry next tick; HA `expire_after`
  eventually marks the entity unavailable.
- First sample after start or config reload: immediate report, then every
  interval.

## 4. HA auto-discovery changes

- Per-sensor config topic and `unique_id` (`{uuid}_{key}`) unchanged → existing
  HA entities are updated in place on re-publish.
- `state_topic` becomes `nvidia-smi/{uuid}/{key}` with a **plain numeric
  payload** (no `value_template`, no all-in-one JSON). The per-sensor topic is
  required because sensors now report on different schedules.
- `expire_after` = `max(3 * interval, 60)` per sensor, so a slow sensor is not
  flagged unavailable while waiting for its next report.
- `device_class`, `unit_of_measurement`, device info, availability topic:
  unchanged.

## 5. Error handling

- dmon EOF/crash → exit non-zero (systemd restarts).
- Invalid config → never crashes the service (table in §1).
- No new MQTT reconnect logic — out of scope, behavior unchanged.

## 6. Testing

- New `pytest` suite in `tests/`:
  - Config loading/validation: missing file, bad YAML, unknown keys, bad
    interval/type, partial entries (tmp files).
  - Aggregation: all 5 types, window filtering, empty window, single sample,
    `None` exclusion.
  - Hot-reload: mtime-change detection and keep-old-config-on-error, using a
    fake/injected config state.
- `requirements.txt`: add `PyYAML` (runtime). `pytest` goes in a separate
  `requirements-dev.txt` so the service's `ExecStartPre` pip step does not
  install dev dependencies.
- Manual verification: start service, `mosquitto_sub` on per-sensor topics,
  confirm cadence and aggregated values (e.g. mean power over 60 s).

## 7. Files touched

| File | Change |
|---|---|
| `nvidia-smi2ha.py` | Restructured: config load/watch, reader thread, scheduler, per-sensor publishing |
| `config.yaml.example` | New, commented example |
| `requirements.txt` | Add `PyYAML` |
| `requirements-dev.txt` | New: `pytest` |
| `README.md` | Document config schema + usage |
| `tests/` | New pytest suite |

## Non-goals

- Per-GPU config overrides.
- Changing dmon's sampling rate or the set of reported sensors.
- MQTT reconnection/robustness improvements.
- Refactoring into multiple files (README TODO, left as-is).
