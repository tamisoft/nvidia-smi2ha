import os
import time

from smi2ha_core import ConfigWatcher


def bump(path):
    st = os.stat(path)
    os.utime(path, (st.st_atime, max(st.st_mtime, time.time()) + 10))


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
