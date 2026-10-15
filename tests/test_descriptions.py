from smi2ha_core import SENSOR_DESCRIPTIONS, SENSOR_KEYS


def test_descriptions_cover_all_sensor_keys():
    assert set(SENSOR_DESCRIPTIONS) == set(SENSOR_KEYS)


def test_new_sensors_have_names_and_units():
    for key in ("pviol", "tviol", "fb", "bar1"):
        desc = SENSOR_DESCRIPTIONS[key]
        assert desc["name"]
        assert desc["unit"]
        assert "device_class" not in desc
