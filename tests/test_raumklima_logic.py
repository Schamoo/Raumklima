import unittest
from datetime import datetime, timedelta, timezone
from custom_components.raumklima.logic import (
    absolute_humidity,
    calculate_room_climate,
    find_window_sensor_entity,
    resolve_room_settings,
    temperature_to_celsius,
    update_high_humidity_since,
)


class RaumklimaLogicTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.values = {
            "temperature": 20.0,
            "relative_humidity": 60.0,
            "outdoor_temperature": 10.0,
            "outdoor_relative_humidity": 70.0,
            "window_state": "off",
            "previous_status": "ok",
            "high_humidity_seconds": 0,
        }

    def calculate(self, **overrides):
        return calculate_room_climate(**(self.values | overrides))

    def test_activation_and_release_hysteresis_boundaries(self):
        self.assertEqual(
            self.calculate(relative_humidity=60.0, wall_humidity_value=0)["status"],
            "ok",
        )
        self.assertEqual(
            self.calculate(
                relative_humidity=60.1,
                wall_humidity_value=0,
                humidity_on_threshold=60,
            )["status"],
            "ventilation_possible",
        )
        self.assertEqual(
            self.calculate(
                relative_humidity=58.0,
                previous_status="ventilation_possible",
                wall_humidity_value=0,
                humidity_advisory_reset_threshold=58,
            )["status"],
            "ok",
        )
        self.assertEqual(
            self.calculate(
                relative_humidity=58.1,
                previous_status="ventilation_possible",
                wall_humidity_value=0,
                humidity_advisory_reset_threshold=58,
            )["status"],
            "ventilation_possible",
        )

    def test_strict_high_humidity_duration(self):
        self.assertEqual(
            self.calculate(relative_humidity=65.0, high_humidity_seconds=1800)["status"],
            "ventilation_possible",
        )
        self.assertEqual(
            self.calculate(relative_humidity=65.1, high_humidity_seconds=1799)["status"],
            "ventilation_possible",
        )
        self.assertEqual(
            self.calculate(relative_humidity=65.1, high_humidity_seconds=1800)["status"],
            "ventilate",
        )

    def test_high_humidity_duration_escalates_an_active_possible_status(self):
        result = self.calculate(
            relative_humidity=65.1,
            previous_status="ventilation_possible",
            high_humidity_seconds=1800,
        )
        self.assertEqual(result["status"], "ventilate")

    def test_timer_starts_and_resets(self):
        self.assertEqual(
            update_high_humidity_since(66, True, None, self.now), self.now
        )
        earlier = self.now - timedelta(minutes=12)
        self.assertEqual(
            update_high_humidity_since(66, True, earlier, self.now), earlier
        )
        self.assertIsNone(update_high_humidity_since(65, True, earlier, self.now))
        self.assertIsNone(update_high_humidity_since(66, False, earlier, self.now))

    def test_temperature_units_normalize_to_celsius(self):
        self.assertAlmostEqual(temperature_to_celsius(68, "°F"), 20)
        self.assertEqual(temperature_to_celsius(20, "°C"), 20)
        self.assertAlmostEqual(temperature_to_celsius(293.15, "K"), 20)
        self.assertIsNone(temperature_to_celsius(20, None))

    def test_custom_global_thresholds_drive_status_and_timer(self):
        result = self.calculate(
            relative_humidity=62,
            wall_humidity_value=0,
            humidity_on_threshold=63,
            humidity_off_threshold=60,
            high_humidity_threshold=70,
            high_humidity_duration_minutes=20,
        )
        self.assertEqual(result["status"], "ok")
        self.assertIsNone(
            update_high_humidity_since(
                66, True, None, self.now, threshold=70
            )
        )

    def test_absolute_humidity_reference_value(self):
        self.assertEqual(absolute_humidity(20, 50), 8.64)

    def test_window_contact_is_found_in_data_or_options(self):
        expected = "binary_sensor.fenster_wohnzimmer_contact"
        self.assertEqual(
            find_window_sensor_entity(
                {"options": {"window_sensor_entity_id": expected}}
            ),
            expected,
        )
        self.assertEqual(
            find_window_sensor_entity(
                {"data": [{"window_sensor_entity_id": expected}]}
            ),
            expected,
        )
        self.assertIsNone(find_window_sensor_entity({"options": {}}))

    def test_missing_outdoor_values_do_not_claim_drying(self):
        result = self.calculate(relative_humidity=62, outdoor_temperature=None)
        self.assertEqual(result["status"], "ventilation_possible")
        self.assertEqual(result["status_text"], "Ventilation possible")
        self.assertIsNone(result["ventilation_effective"])
        self.assertEqual(
            result["ventilation_hint_base_key"], "base_humidity_elevated"
        )
        self.assertEqual(result["ventilation_hint_outdoor_key"], "outdoor_unknown")

    def test_unknown_window_state_does_not_add_open_window_prefix(self):
        result = self.calculate(relative_humidity=62, window_state=None)
        self.assertEqual(result["status"], "ventilation_possible")
        self.assertIsNone(result["window_open"])
        self.assertFalse(result["ventilation_hint_window_prefix"])

    def test_wetter_outdoor_air_does_not_hide_high_humidity(self):
        result = self.calculate(
            temperature=20.4,
            relative_humidity=66,
            outdoor_temperature=15.23,
            outdoor_relative_humidity=100,
            high_humidity_seconds=1800,
            wall_humidity_value=0,
        )
        self.assertEqual(result["status"], "ventilate")
        self.assertFalse(result["ventilation_effective"])
        self.assertEqual(
            result["ventilation_hint_base_key"], "base_humidity_high"
        )
        self.assertEqual(
            result["ventilation_hint_outdoor_key"], "outdoor_ineffective"
        )

    def test_absolute_humidity_drying_margin_and_hysteresis(self):
        indoor_absolute = absolute_humidity(20, 61)
        outdoor_saturation = absolute_humidity(20, 100)
        outdoor_rh_at_margin = (indoor_absolute - 0.5) / outdoor_saturation * 100
        self.assertTrue(
            self.calculate(
                relative_humidity=61,
                outdoor_temperature=20,
                outdoor_relative_humidity=outdoor_rh_at_margin - 0.1,
            )["ventilation_effective"]
        )
        self.assertFalse(
            self.calculate(
                relative_humidity=61,
                outdoor_temperature=20,
                outdoor_relative_humidity=outdoor_rh_at_margin + 0.1,
            )["ventilation_effective"]
        )

        active_indoor_absolute = absolute_humidity(20, 59)
        outdoor_rh_at_hysteresis = (
            active_indoor_absolute - 0.3
        ) / outdoor_saturation * 100
        self.assertTrue(
            self.calculate(
                relative_humidity=59,
                previous_status="ventilation_possible",
                outdoor_temperature=20,
                outdoor_relative_humidity=outdoor_rh_at_hysteresis - 0.1,
            )["ventilation_effective"]
        )

    def test_open_window_is_attribute_and_hint_only(self):
        result = self.calculate(relative_humidity=62, window_state="on")
        self.assertEqual(result["status"], "ventilation_possible")
        self.assertTrue(result["window_open"])
        self.assertTrue(result["ventilation_hint_window_prefix"])

    def test_close_required_when_target_reached_or_outdoor_air_unsuitable(self):
        result = self.calculate(
            relative_humidity=57,
            wall_humidity_value=0,
            window_state="on",
            previous_status="ventilation_possible",
            humidity_off_threshold=58,
        )
        self.assertTrue(result["target_reached"])
        self.assertTrue(result["close_required"])
        self.assertEqual(result["status"], "close_recommended")

    def test_close_is_not_recommended_without_an_active_ventilation_cycle(self):
        result = self.calculate(relative_humidity=57, window_state="on")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["target_reached"])
        self.assertFalse(result["close_required"])

    def test_configured_window_open_duration_requires_closing(self):
        before_timeout = self.calculate(
            relative_humidity=50,
            wall_humidity_value=0,
            window_state="on",
            window_open_seconds=599,
            window_open_duration_minutes=10,
        )
        self.assertFalse(before_timeout["close_required"])

        timeout = self.calculate(
            relative_humidity=50,
            wall_humidity_value=0,
            window_state="on",
            window_open_seconds=600,
            window_open_duration_minutes=10,
        )
        self.assertTrue(timeout["close_required"])
        self.assertEqual(timeout["status"], "close_recommended")
        self.assertEqual(
            timeout["ventilation_hint_single_key"], "window_open_too_long"
        )
        self.assertEqual(timeout["ventilation_hint_placeholders"]["duration"], "10")

    def test_default_window_open_duration_is_10_minutes(self):
        before_timeout = self.calculate(
            relative_humidity=50,
            wall_humidity_value=0,
            window_state="on",
            window_open_seconds=599,
        )
        self.assertFalse(before_timeout["close_required"])

        timeout = self.calculate(
            relative_humidity=50,
            wall_humidity_value=0,
            window_state="on",
            window_open_seconds=600,
        )
        self.assertTrue(timeout["close_required"])
        self.assertEqual(
            timeout["ventilation_hint_single_key"], "window_open_too_long"
        )
        self.assertEqual(timeout["ventilation_hint_placeholders"]["duration"], "10")

    def test_window_open_timeout_still_asks_to_close_without_climate_data(self):
        result = self.calculate(
            temperature=None,
            window_state="on",
            window_open_seconds=600,
        )
        self.assertEqual(result["status"], "close_recommended")
        self.assertTrue(result["close_required"])
        self.assertEqual(
            result["ventilation_hint_single_key"], "window_open_too_long"
        )
        self.assertEqual(result["ventilation_hint_placeholders"]["duration"], "10")

    def test_room_settings_override_global_defaults_selectively(self):
        global_settings = {
            "humidity_on_threshold": 60,
            "humidity_off_threshold": 58,
            "high_humidity_threshold": 65,
            "high_humidity_duration_minutes": 30,
            "outdoor_drying_margin": 1,
            "outdoor_drying_hysteresis_margin": 0.5,
        }
        settings = resolve_room_settings(
            global_settings,
            {"humidity_on_threshold": 55, "humidity_off_threshold": 53},
        )
        self.assertEqual(settings["humidity_on_threshold"], 55)
        self.assertEqual(settings["humidity_off_threshold"], 53)
        self.assertEqual(settings["high_humidity_threshold"], 65)

        result = self.calculate(
            relative_humidity=66,
            window_state="on",
            outdoor_temperature=20,
            outdoor_relative_humidity=100,
            previous_status="ventilation_possible",
        )
        self.assertTrue(result["ventilation_needed"])
        self.assertFalse(result["ventilation_effective"])
        self.assertTrue(result["close_required"])
        self.assertEqual(result["status"], "close_recommended")

    def test_hysteresis_keeps_cycle_stable_when_outdoor_conditions_flip(self):
        indoor = 20
        outdoor = 20
        base = self.calculate(
            temperature=indoor,
            relative_humidity=60.5,
            outdoor_temperature=outdoor,
            outdoor_relative_humidity=100,
            window_state="on",
            previous_status="ventilation_possible",
            humidity_off_threshold=55,
            humidity_advisory_reset_threshold=59,
            humidity_on_threshold=60,
        )
        self.assertTrue(base["ventilation_needed"])
        self.assertFalse(base["ventilation_effective"])
        self.assertTrue(base["close_required"])

        recovery = self.calculate(
            temperature=indoor,
            relative_humidity=59.5,
            outdoor_temperature=outdoor,
            outdoor_relative_humidity=40,
            previous_status="ventilation_possible",
            humidity_advisory_reset_threshold=59,
        )
        self.assertTrue(recovery["ventilation_needed"])
        self.assertEqual(recovery["status"], "ventilation_possible")
        self.assertFalse(recovery["close_required"])

    def test_window_close_resets_close_hint(self):
        result = self.calculate(
            relative_humidity=59,
            wall_humidity_value=0,
            window_state="off",
            previous_status="close_recommended",
        )
        self.assertFalse(result["close_required"])
        self.assertEqual(result["status"], "ok")

    def test_invalid_indoor_values_report_missing_data(self):
        for values in (
            {"temperature": None},
            {"relative_humidity": "unavailable"},
            {"relative_humidity": 101},
        ):
            with self.subTest(values=values):
                result = self.calculate(**values)
                self.assertEqual(result["status"], "missing_data")
                self.assertIsNone(result["temperature"] if "temperature" in values else result["relative_humidity"])


if __name__ == "__main__":
    unittest.main()
