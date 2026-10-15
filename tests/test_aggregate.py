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
