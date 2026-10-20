import unittest
from types import SimpleNamespace

from custom_components.raumklima.logic import timer_sensor_unit, timer_sensor_value


class TimerSensorDisplayTests(unittest.TestCase):
    def sensor_stub(self, key, data):
        return SimpleNamespace(
            _key=key,
            coordinator=SimpleNamespace(data=data),
            _attr_native_unit_of_measurement="min",
        )

    def test_inactive_timers_show_off_without_a_unit(self):
        timer_keys = (
            ("high_humidity_since", "high_humidity_timer_active"),
            ("mold_warning_duration", "mold_warning_timer_active"),
        )
        for key, active_key in timer_keys:
            with self.subTest(timer=key):
                sensor = self.sensor_stub(key, {active_key: False, key: 0})
                self.assertEqual(
                    timer_sensor_value(sensor.coordinator.data[key], False),
                    "off",
                )
                self.assertIsNone(timer_sensor_unit("min", False))

    def test_active_timers_show_elapsed_minutes_and_unit(self):
        timer_keys = (
            ("high_humidity_since", "high_humidity_timer_active"),
            ("mold_warning_duration", "mold_warning_timer_active"),
        )
        for key, active_key in timer_keys:
            with self.subTest(timer=key):
                sensor = self.sensor_stub(key, {active_key: True, key: 12})
                self.assertEqual(timer_sensor_value(sensor.coordinator.data[key], True), 12)
                self.assertEqual(timer_sensor_unit("min", True), "min")


if __name__ == "__main__":
    unittest.main()
