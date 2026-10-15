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
