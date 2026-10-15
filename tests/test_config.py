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


def test_infinite_interval_falls_back_to_defaults(tmp_path, capsys):
    p = write(tmp_path, "sensors:\n  pwr: {interval: .inf, type: last}\n")
    cfg = load_config(p)
    assert cfg["sensors"]["pwr"] == {"interval": 10.0, "type": "last"}
    assert "invalid interval for 'pwr'" in capsys.readouterr().out


def test_invalid_defaults_field_falls_back(tmp_path, capsys):
    p = write(tmp_path, "defaults:\n  interval: -3\n  type: rms\n")
    cfg = load_config(p)
    assert cfg["defaults"] == {"interval": 10.0, "type": "last"}
    out = capsys.readouterr().out
    assert "invalid defaults.interval" in out
    assert "invalid defaults.type" in out
