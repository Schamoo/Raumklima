import json
import unittest
from datetime import datetime, timedelta, timezone
from math import exp, log
from pathlib import Path
from random import Random

from custom_components.raumklima.logic import (
    calculate_room_climate,
    estimated_wall_humidity,
    update_high_humidity_since,
    update_wall_humidity_since,
)


class MoldVentilationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.values = {
            "temperature": 20.4,
            "outdoor_temperature": 0,
            "outdoor_relative_humidity": 80,
            "relative_humidity": 45,
            "window_state": "off",
            "previous_status": "ok",
            "high_humidity_seconds": 0,
        }

    def calculate(self, **overrides):
        return calculate_room_climate(**(self.values | overrides))

    def test_expected_wall_humidity_values(self):
        expected = {
            45: 64,
            49: 70,
            50: 72,
            51: 73,
            52: 75,
            53: 76,
            57: 82,
        }
        for relative_humidity, wall_humidity in expected.items():
            with self.subTest(relative_humidity=relative_humidity):
                result = self.calculate(relative_humidity=relative_humidity)
                self.assertEqual(
                    result["estimated_wall_humidity"],
                    wall_humidity,
                )

    def test_warning_threshold_starts_ventilation(self):
        result = self.calculate(relative_humidity=45, previous_status="ok")
        self.assertEqual(result["status"], "ok")

        result = self.calculate(
            relative_humidity=49,
            previous_status="ok",
            humidity_on_threshold=48,
        )
        self.assertEqual(result["status"], "ventilation_possible")

    def test_warning_hysteresis_keeps_active_cycle(self):
        for relative_humidity in (51, 50):
            with self.subTest(relative_humidity=relative_humidity):
                result = self.calculate(
                    relative_humidity=relative_humidity,
                    previous_status="ventilation_possible",
                    humidity_advisory_reset_threshold=49,
                )
                self.assertEqual(result["status"], "ventilation_possible")

    def test_hysteresis_release_depends_on_window_state(self):
        result = self.calculate(
            relative_humidity=45,
            previous_status="ventilation_possible",
        )
        self.assertEqual(result["status"], "ok")

        result = self.calculate(
            relative_humidity=45,
            previous_status="ventilation_possible",
            window_state="on",
        )
        self.assertEqual(result["status"], "close_recommended")

    def test_target_is_not_reached_while_wall_risk_remains(self):
        result = self.calculate(
            relative_humidity=57,
            previous_status="ventilation_possible",
            window_state="on",
        )
        self.assertEqual(result["status"], "ventilation_possible")
        self.assertFalse(result["target_reached"])
        self.assertFalse(result["close_required"])

    def test_ineffective_outdoor_air_does_not_close_window_with_wall_need(self):
        result = self.calculate(
            relative_humidity=57,
            previous_status="ventilation_possible",
            window_state="on",
            outdoor_drying_hysteresis_margin=10,
        )
        self.assertEqual(result["estimated_wall_humidity"], 82)
        self.assertFalse(result["ventilation_effective"])
        self.assertFalse(result["target_reached"])
        self.assertFalse(result["close_required"])
        self.assertNotEqual(result["status"], "close_recommended")

    def test_critical_wall_risk_after_duration_sets_ventilate_status(self):
        result = self.calculate(
            relative_humidity=57,
            high_wall_humidity_seconds=1800,
        )
        self.assertEqual(result["status"], "ventilate")

    def test_elevated_wall_risk_sets_ventilate_status_after_warning_duration(self):
        before = self.calculate(
            relative_humidity=52,
            high_wall_humidity_seconds=3599,
        )
        self.assertEqual(before["estimated_wall_humidity"], 75)
        self.assertEqual(before["status"], "ventilation_possible")

        at_boundary = self.calculate(
            relative_humidity=52,
            high_wall_humidity_seconds=3600,
        )
        self.assertEqual(at_boundary["estimated_wall_humidity"], 75)
        self.assertEqual(at_boundary["status"], "ventilate")
        self.assertEqual(
            at_boundary["ventilation_hint_base_key"], "base_mold_warning"
        )

    def test_critical_wall_risk_uses_half_warning_duration_boundary(self):
        before = self.calculate(
            relative_humidity=57,
            high_wall_humidity_seconds=1799,
        )
        self.assertNotEqual(before["status"], "ventilate")

        at_boundary = self.calculate(
            relative_humidity=57,
            high_wall_humidity_seconds=1800,
        )
        self.assertEqual(at_boundary["status"], "ventilate")
        self.assertEqual(
            at_boundary["ventilation_hint_placeholders"]["duration"], "30"
        )

    def test_critical_wall_timer_is_independent_of_room_humidity_timer(self):
        result = self.calculate(
            relative_humidity=57,
            high_humidity_duration_minutes=90,
            high_wall_humidity_seconds=1800,
        )
        self.assertEqual(result["status"], "ventilate")

    def test_critical_wall_duration_is_half_the_warning_duration(self):
        result = self.calculate(
            relative_humidity=57,
            mold_warning_duration_minutes=40,
            high_wall_humidity_seconds=1200,
        )
        self.assertEqual(result["status"], "ventilate")
        self.assertEqual(
            result["ventilation_hint_placeholders"]["duration"], "20"
        )

    def test_critical_wall_risk_with_ineffective_air_keeps_ventilation_status(self):
        result = self.calculate(
            relative_humidity=57,
            outdoor_drying_margin=10,
            high_wall_humidity_seconds=1800,
        )
        self.assertFalse(result["ventilation_effective"])
        self.assertFalse(result["mold_ventilation_recommended"])
        self.assertFalse(result["close_required"])
        self.assertEqual(result["mold_risk"], "high")
        self.assertEqual(result["status"], "ventilate")
        self.assertEqual(
            result["ventilation_hint_base_key"], "base_mold_critical"
        )
        self.assertEqual(
            result["ventilation_hint_outdoor_key"], "outdoor_ineffective"
        )

    def test_critical_wall_risk_closes_an_open_window_after_timeout(self):
        result = self.calculate(
            relative_humidity=57,
            previous_status="ventilation_possible",
            window_state="on",
            window_open_seconds=3600,
            outdoor_drying_hysteresis_margin=10,
            high_wall_humidity_seconds=1800,
        )
        self.assertEqual(result["estimated_wall_humidity"], 82)
        self.assertFalse(result["ventilation_effective"])
        self.assertFalse(result["mold_ventilation_recommended"])
        self.assertTrue(result["close_required"])
        self.assertEqual(result["mold_risk"], "high")
        self.assertEqual(result["status"], "close_recommended")
        self.assertEqual(
            result["ventilation_hint_single_key"], "window_open_too_long"
        )

    def test_ineffective_air_closes_open_window_when_wall_warning_elapsed(self):
        result = self.calculate(
            relative_humidity=57,
            previous_status="ventilation_possible",
            window_state="on",
            window_open_seconds=60,
            outdoor_drying_margin=10,
            outdoor_drying_hysteresis_margin=10,
            high_wall_humidity_seconds=1800,
        )
        self.assertFalse(result["ventilation_effective"])
        self.assertTrue(result["close_required"])
        self.assertEqual(result["status"], "close_recommended")

    def test_elapsed_wall_warning_status_reflects_outdoor_effectiveness(self):
        ineffective = self.calculate(
            relative_humidity=52,
            outdoor_drying_margin=10,
            high_wall_humidity_seconds=3600,
        )
        self.assertEqual(ineffective["status"], "ventilate")
        self.assertFalse(ineffective["ventilation_effective"])
        self.assertEqual(
            ineffective["ventilation_hint_base_key"], "base_mold_warning"
        )
        self.assertEqual(
            ineffective["ventilation_hint_outdoor_key"], "outdoor_ineffective"
        )

        unknown = self.calculate(
            relative_humidity=52,
            outdoor_relative_humidity=None,
            high_wall_humidity_seconds=3600,
        )
        self.assertEqual(unknown["status"], "ventilate")
        self.assertIsNone(unknown["ventilation_effective"])
        self.assertEqual(
            unknown["ventilation_hint_base_key"], "base_mold_warning"
        )
        self.assertEqual(
            unknown["ventilation_hint_outdoor_key"], "outdoor_unknown"
        )

    def test_mold_risk_state_translations(self):
        integration_path = (
            Path(__file__).resolve().parents[1]
            / "custom_components"
            / "raumklima"
        )
        expected = {
            "de.json": {
                "unknown": "Unbekannt",
                "not_relevant": "Nicht relevant",
            },
            "en.json": {
                "unknown": "Unknown",
                "not_relevant": "Not relevant",
            },
        }
        for filename, expected_states in expected.items():
            with self.subTest(filename=filename):
                translation_path = integration_path / "translations" / filename
                translations = json.loads(
                    translation_path.read_text(encoding="utf-8")
                )
                states = translations["entity"]["sensor"]["mold_risk"]["state"]
                for state, label in expected_states.items():
                    self.assertEqual(states[state], label)

    def test_mold_risk_states(self):
        cases = (
            (15, 52, "low"),
            (None, 52, "unknown"),
            (0, 45, "low"),
            (0, 57, "high"),
            (0, 52, "elevated"),
        )
        for outdoor_temperature, relative_humidity, risk in cases:
            with self.subTest(outdoor_temperature=outdoor_temperature, relative_humidity=relative_humidity):
                result = self.calculate(
                    outdoor_temperature=outdoor_temperature,
                    relative_humidity=relative_humidity,
                )
                self.assertEqual(result["mold_risk"], risk)

    def test_wall_humidity_is_shown_independently_of_model_risk(self):
        result = self.calculate(
            temperature=19.1,
            relative_humidity=66.66,
            outdoor_temperature=11.9,
            mold_warning_threshold=75,
            calibration_factor=1.4,
        )
        self.assertEqual(result["estimated_wall_humidity"], 75)
        self.assertEqual(result["mold_risk"], "elevated")

        result = self.calculate(
            temperature=19.1,
            relative_humidity=66.66,
            outdoor_temperature=15,
            mold_warning_threshold=75,
            calibration_factor=1.4,
        )
        self.assertEqual(result["estimated_wall_humidity"], 71)
        self.assertEqual(result["mold_risk"], "low")

        result = self.calculate(
            temperature=24,
            relative_humidity=66.66,
            outdoor_temperature=28,
        )
        self.assertEqual(result["mold_risk"], "not_relevant")
        self.assertIsNotNone(result["estimated_wall_humidity"])

        result = self.calculate(outdoor_temperature=None)
        self.assertEqual(result["mold_risk"], "unknown")
        self.assertIsNone(result["estimated_wall_humidity"])

    def test_open_window_without_active_cycle_is_not_a_close_recommendation(self):
        result = self.calculate(
            relative_humidity=50,
            outdoor_temperature=15,
            previous_status="ok",
            window_state="on",
        )
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["close_required"])

    def test_critical_wall_humidity_timer_includes_threshold(self):
        self.assertEqual(
            update_high_humidity_since(
                80,
                True,
                None,
                self.now,
                threshold=80,
                inclusive=True,
            ),
            self.now,
        )
        self.assertIsNone(
            update_high_humidity_since(
                79,
                True,
                None,
                self.now,
                threshold=80,
                inclusive=True,
            )
        )

    def test_wall_humidity_timer_starts_at_warning_and_survives_critical_step(self):
        started = update_wall_humidity_since(70, None, self.now, threshold=70)
        self.assertEqual(started, self.now)

        warning_elapsed = self.now + timedelta(minutes=29)
        continued = update_wall_humidity_since(
            79, started, warning_elapsed, threshold=70
        )
        self.assertEqual(continued, started)

        critical_reached = self.now + timedelta(minutes=30)
        continued = update_wall_humidity_since(
            80, continued, critical_reached, threshold=70
        )
        self.assertEqual(continued, started)
        self.assertEqual(
            (critical_reached - continued).total_seconds(),
            30 * 60,
        )

        continued = update_wall_humidity_since(
            None, continued, self.now + timedelta(minutes=45), threshold=70
        )
        self.assertIsNone(continued)

        restarted = update_wall_humidity_since(
            75, continued, self.now + timedelta(minutes=46), threshold=70
        )
        self.assertEqual(restarted, self.now + timedelta(minutes=46))

    def test_wall_humidity_timer_resets_only_below_warning_threshold(self):
        started = update_wall_humidity_since(70, None, self.now, threshold=70)
        self.assertEqual(
            update_wall_humidity_since(
                69.99, started, self.now + timedelta(minutes=20), threshold=70
            ),
            None,
        )

        restarted = update_wall_humidity_since(
            70, None, self.now + timedelta(minutes=21), threshold=70
        )
        self.assertEqual(restarted, self.now + timedelta(minutes=21))

    def test_estimated_wall_humidity_matches_documented_winter_reference(self):
        self.assertEqual(estimated_wall_humidity(20.4, 0, 55, 1.4), 79)
        self.assertGreaterEqual(estimated_wall_humidity(20.4, 0, 56, 1.4), 80)
        self.assertGreaterEqual(estimated_wall_humidity(20.4, 0, 52, 1.4), 75)
        self.assertGreater(
            estimated_wall_humidity(20.4, 0, 56, 2.0),
            estimated_wall_humidity(20.4, 0, 56, 1.4),
        )
        self.assertIsNone(estimated_wall_humidity(20.4, None, 55, 1.4))
        self.assertIsNone(estimated_wall_humidity(20.4, 0, 55, 0.99))

    def test_estimated_wall_humidity_matches_mold_indicator_for_3000_samples(self):
        random = Random(20261017)
        for _ in range(3000):
            indoor_temperature = random.uniform(-10, 40)
            outdoor_temperature = random.uniform(-30, 30)
            indoor_humidity = random.uniform(0, 100)
            calibration_factor = random.uniform(1, 5)

            magnus_k2 = 17.62
            magnus_k3 = 243.12
            if indoor_humidity == 0:
                dewpoint = -50
            else:
                alpha = magnus_k2 * indoor_temperature / (magnus_k3 + indoor_temperature)
                beta = magnus_k2 * magnus_k3 / (magnus_k3 + indoor_temperature)
                log_humidity = log(indoor_humidity / 100)
                dewpoint = magnus_k3 * (alpha + log_humidity) / (beta - log_humidity)
            critical_temperature = outdoor_temperature + (
                indoor_temperature - outdoor_temperature
            ) / calibration_factor
            alpha = magnus_k2 * critical_temperature / (magnus_k3 + critical_temperature)
            beta = magnus_k2 * magnus_k3 / (magnus_k3 + critical_temperature)
            reference = int(
                exp(
                    (dewpoint * beta - magnus_k3 * alpha)
                    / (dewpoint + magnus_k3)
                )
                * 100
            )
            reference = min(100, max(0, reference))
            self.assertEqual(
                estimated_wall_humidity(
                    indoor_temperature,
                    outdoor_temperature,
                    indoor_humidity,
                    calibration_factor,
                ),
                reference,
            )

    def test_wall_humidity_risk_depends_on_indoor_outdoor_delta(self):
        self.assertEqual(
            self.calculate(
                relative_humidity=55,
                outdoor_temperature=None,
            )["mold_risk"],
            "unknown",
        )
        self.assertEqual(
            self.calculate(
                relative_humidity=45,
                outdoor_temperature=10,
            )["mold_risk"],
            "low",
        )
        result = self.calculate(
            relative_humidity=55,
            outdoor_temperature=0,
            outdoor_relative_humidity=None,
        )
        self.assertEqual(result["mold_risk"], "elevated")
        self.assertEqual(result["ventilation_hint_base_key"], "base_mold_current")
        self.assertEqual(result["ventilation_hint_outdoor_key"], "outdoor_unknown")

        effective = self.calculate(
            temperature=20.4,
            relative_humidity=52,
            outdoor_temperature=0,
            outdoor_relative_humidity=70,
        )
        self.assertEqual(effective["estimated_wall_humidity"], 75)
        self.assertTrue(effective["ventilation_effective"])
        self.assertTrue(effective["mold_ventilation_recommended"])
        self.assertTrue(effective["ventilation_needed"])
        self.assertEqual(effective["status"], "ventilation_possible")


if __name__ == "__main__":
    unittest.main()
