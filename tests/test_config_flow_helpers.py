import unittest

from custom_components.raumklima.config_flow import (
    _room_source_id,
    _validate_global_settings,
)
from custom_components.raumklima.const import (
    CONF_DRYING_HYSTERESIS,
    CONF_DRYING_MARGIN,
    CONF_HIGH_HUMIDITY,
    CONF_HUMIDITY_ADVISORY_RESET,
    CONF_HUMIDITY_OFF,
    CONF_HUMIDITY_ON,
    CONF_MOLD_CRITICAL_THRESHOLD,
    CONF_MOLD_WARNING_THRESHOLD,
    DEFAULT_SETTINGS,
    CONF_ROOM_HUMIDITY_ENTITY,
    CONF_ROOM_TEMP_ENTITY,
    CONF_VTHERM_ENTITY,
)


class ConfigFlowHelperTests(unittest.TestCase):
    def test_room_source_id_prefers_vtherm(self):
        self.assertEqual(
            _room_source_id(
                {
                    CONF_VTHERM_ENTITY: "climate.room",
                    CONF_ROOM_TEMP_ENTITY: "sensor.temperature",
                    CONF_ROOM_HUMIDITY_ENTITY: "sensor.humidity",
                }
            ),
            "climate.room",
        )

    def test_room_source_id_requires_both_sensor_entities(self):
        self.assertIsNone(_room_source_id({CONF_ROOM_TEMP_ENTITY: "sensor.temperature"}))
        self.assertEqual(
            _room_source_id(
                {
                    CONF_ROOM_TEMP_ENTITY: "sensor.temperature",
                    CONF_ROOM_HUMIDITY_ENTITY: "sensor.humidity",
                }
            ),
            "sensor.temperature|sensor.humidity",
        )

    def test_global_settings_returns_first_validation_error(self):
        invalid = dict(DEFAULT_SETTINGS)
        invalid[CONF_HUMIDITY_OFF] = 60
        invalid[CONF_HUMIDITY_ADVISORY_RESET] = 50
        invalid[CONF_HUMIDITY_ON] = 40
        invalid[CONF_HIGH_HUMIDITY] = 30
        invalid[CONF_DRYING_HYSTERESIS] = 2
        invalid[CONF_DRYING_MARGIN] = 1
        invalid[CONF_MOLD_WARNING_THRESHOLD] = 80
        invalid[CONF_MOLD_CRITICAL_THRESHOLD] = 70
        self.assertEqual(_validate_global_settings(invalid), {"base": "humidity_order"})

    def test_global_settings_accepts_defaults(self):
        self.assertEqual(_validate_global_settings(DEFAULT_SETTINGS), {})
