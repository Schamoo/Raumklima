from collections.abc import Mapping
from math import exp, isfinite, log

from .const import (
    HIGH_HUMIDITY_THRESHOLD,
    HUMIDITY_ADVISORY_RESET_THRESHOLD,
    HUMIDITY_OFF_THRESHOLD,
    HUMIDITY_ON_THRESHOLD,
    MOLD_CRITICAL_THRESHOLD,
    MOLD_WARNING_DURATION_MINUTES,
    MOLD_WARNING_THRESHOLD,
    MOLD_WARNING_HYSTERESIS,
    OUTDOOR_DRYING_HYSTERESIS_MARGIN,
    OUTDOOR_DRYING_MARGIN,
    STATUS_LABELS,
    DEFAULT_SETTINGS,
    WINDOW_OPEN_DURATION_MINUTES,
)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def find_window_sensor_entity(config_entry_data):
    """Find VTherm's configured window contact without assuming its storage section."""
    if isinstance(config_entry_data, Mapping):
        entity_id = config_entry_data.get("window_sensor_entity_id")
        if isinstance(entity_id, str) and entity_id.startswith("binary_sensor."):
            return entity_id
        for value in config_entry_data.values():
            entity_id = find_window_sensor_entity(value)
            if entity_id:
                return entity_id
    elif isinstance(config_entry_data, (list, tuple)):
        for value in config_entry_data:
            entity_id = find_window_sensor_entity(value)
            if entity_id:
                return entity_id
    return None


def climate_values_valid(temperature, relative_humidity):
    temperature = _number(temperature)
    relative_humidity = _number(relative_humidity)
    return (
        temperature is not None
        and -100 < temperature < 100
        and relative_humidity is not None
        and 0 <= relative_humidity <= 100
    )

def mold_model_applies(indoor_temperature, outdoor_temperature) -> bool:
    return (
        indoor_temperature is not None
        and outdoor_temperature is not None
        and outdoor_temperature < indoor_temperature
    )


def temperature_to_celsius(value, unit):
    temperature = _number(value)
    if temperature is None or unit is None:
        return None
    normalized_unit = str(unit).strip().lower()
    if normalized_unit in ("°c", "c", "celsius"):
        return temperature
    if normalized_unit in ("°f", "f", "fahrenheit"):
        return (temperature - 32) * 5 / 9
    if normalized_unit in ("k", "kelvin"):
        return temperature - 273.15
    return None


def absolute_humidity(temperature, relative_humidity):
    """Return absolute humidity in g/m³ using temperature in °C and RH in %."""
    temperature = _number(temperature)
    relative_humidity = _number(relative_humidity)
    if temperature is None or relative_humidity is None:
        return None
    if temperature <= -100 or temperature >= 100:
        return None
    saturation_hpa = 6.112 * exp((17.67 * temperature) / (temperature + 243.5))
    return round((saturation_hpa * relative_humidity * 2.1674) / (273.15 + temperature), 2)


def update_high_humidity_since(
    relative_humidity,
    indoor_valid,
    since,
    now,
    threshold=HIGH_HUMIDITY_THRESHOLD,
):
    """Track a strict continuous interval above the configured RH threshold."""
    humidity = _number(relative_humidity)
    if (
        not indoor_valid
        or humidity is None
        or humidity <= threshold
    ):
        return None
    return since or now


def update_wall_humidity_since(
    wall_humidity,
    since,
    now,
    threshold=MOLD_WARNING_THRESHOLD,
):
    """Track one continuous wall-humidity interval from warning level upward."""
    humidity = _number(wall_humidity)
    if humidity is None:
        return None
    if humidity < threshold:
        return None
    return since or now


def timer_sensor_value(value, active):
    """Show active timer duration or a localized off state."""
    return value if active else "off"


def timer_sensor_unit(unit, active):
    """Only expose the minutes unit while a timer is active."""
    return unit if active else None


def _resolve_status(
    *,
    indoor_valid,
    window_close_due_to_timeout,
    close_required,
    mold_critical_warning,
    mold_warning_elapsed,
    ventilation_needed,
    mold_ventilation_recommended,
    strong_warning,
):
    """Apply the status priority from closing and missing data through advice."""
    if window_close_due_to_timeout:
        return "close_recommended"
    if not indoor_valid:
        return "missing_data"
    if close_required:
        return "close_recommended"
    if mold_critical_warning or mold_warning_elapsed or strong_warning:
        return "ventilate"
    if ventilation_needed or mold_ventilation_recommended:
        return "ventilation_possible"
    return "ok"


def _outdoor_hint_key(ventilation_effective):
    if ventilation_effective is True:
        return "outdoor_effective"
    if ventilation_effective is False:
        return "outdoor_ineffective"
    return "outdoor_unknown"


def estimated_wall_humidity(
    indoor_temperature,
    outdoor_temperature,
    indoor_relative_humidity,
    calibration_factor,
):
    """Match HA mold_indicator's estimated critical-wall humidity calculation."""
    indoor_temperature = _number(indoor_temperature)
    outdoor_temperature = _number(outdoor_temperature)
    indoor_relative_humidity = _number(indoor_relative_humidity)
    calibration_factor = _number(calibration_factor)
    if (
        indoor_temperature is None
        or outdoor_temperature is None
        or indoor_relative_humidity is None
        or calibration_factor is None
        or not -100 < indoor_temperature < 100
        or not -100 < outdoor_temperature < 100
        or not 0 <= indoor_relative_humidity <= 100
        or calibration_factor < 1
    ):
        return None

    magnus_k2 = 17.62
    magnus_k3 = 243.12
    if indoor_relative_humidity == 0:
        dewpoint = -50
    else:
        alpha = magnus_k2 * indoor_temperature / (magnus_k3 + indoor_temperature)
        beta = magnus_k2 * magnus_k3 / (magnus_k3 + indoor_temperature)
        log_humidity = log(indoor_relative_humidity / 100)
        dewpoint = magnus_k3 * (alpha + log_humidity) / (beta - log_humidity)

    critical_temperature = outdoor_temperature + (
        indoor_temperature - outdoor_temperature
    ) / calibration_factor
    alpha = magnus_k2 * critical_temperature / (magnus_k3 + critical_temperature)
    beta = magnus_k2 * magnus_k3 / (magnus_k3 + critical_temperature)
    wall_humidity = exp(
        (dewpoint * beta - magnus_k3 * alpha) / (dewpoint + magnus_k3)
    ) * 100
    return min(100, max(0, int(wall_humidity)))


def resolve_room_settings(global_settings, room_settings):
    settings = dict(DEFAULT_SETTINGS)
    settings.update(global_settings or {})
    settings.update(
        {
            key: value
            for key, value in (room_settings or {}).items()
            if key in DEFAULT_SETTINGS
        }
    )
    return settings


def calculate_room_climate(
    temperature,
    relative_humidity,
    outdoor_temperature,
    outdoor_relative_humidity,
    window_state,
    previous_status,
    high_humidity_seconds,
    *,
    window_open_seconds=0,
    humidity_on_threshold=HUMIDITY_ON_THRESHOLD,
    humidity_off_threshold=HUMIDITY_OFF_THRESHOLD,
    humidity_advisory_reset_threshold=HUMIDITY_ADVISORY_RESET_THRESHOLD,
    high_humidity_threshold=HIGH_HUMIDITY_THRESHOLD,
    high_humidity_duration_minutes=30,
    window_open_duration_minutes=WINDOW_OPEN_DURATION_MINUTES,
    outdoor_drying_margin=OUTDOOR_DRYING_MARGIN,
    outdoor_drying_hysteresis_margin=OUTDOOR_DRYING_HYSTERESIS_MARGIN,
    calibration_factor=1.4,
    mold_warning_threshold=MOLD_WARNING_THRESHOLD,
    mold_critical_threshold=MOLD_CRITICAL_THRESHOLD,
    mold_warning_duration_minutes=MOLD_WARNING_DURATION_MINUTES,
    high_wall_humidity_seconds=0,
    wall_humidity_value=None,
):
    temperature = _number(temperature)
    relative_humidity = _number(relative_humidity)
    outdoor_temperature = _number(outdoor_temperature)
    outdoor_relative_humidity = _number(outdoor_relative_humidity)

    indoor_valid = climate_values_valid(temperature, relative_humidity)
    outdoor_valid = climate_values_valid(outdoor_temperature, outdoor_relative_humidity)
    wall_humidity = wall_humidity_value
    if wall_humidity is None:
        wall_humidity = estimated_wall_humidity(
            temperature,
            outdoor_temperature,
            relative_humidity,
            calibration_factor,
        )
    mold_model_active = mold_model_applies(temperature, outdoor_temperature)
    if outdoor_temperature is None or wall_humidity is None:
        mold_risk = "unknown"
    elif not mold_model_active:
        mold_risk = "not_relevant"
    elif wall_humidity >= mold_critical_threshold:
        mold_risk = "high"
    elif wall_humidity >= mold_warning_threshold:
        mold_risk = "elevated"
    else:
        mold_risk = "low"

    window_open = window_state == "on" if window_state in ("on", "off") else None
    cycle_active = window_open is True
    mold_limit = (
        mold_warning_threshold - MOLD_WARNING_HYSTERESIS
        if cycle_active
        else mold_warning_threshold
    )
    mold_risk_requires_ventilation = (
        mold_model_active
        and wall_humidity is not None
        and wall_humidity >= mold_limit
    )
    advisory_was_active = previous_status in ("ventilation_possible", "ventilate")
    if cycle_active:
        humidity_limit = humidity_off_threshold
    elif advisory_was_active:
        humidity_limit = humidity_advisory_reset_threshold
    else:
        humidity_limit = humidity_on_threshold
    ventilation_needed = indoor_valid and relative_humidity > humidity_limit
    target_reached = (
        cycle_active
        and indoor_valid
        and relative_humidity <= humidity_off_threshold
        and not mold_risk_requires_ventilation
    )
    absolute_humidity_indoor = None
    absolute_humidity_outdoor = None
    if indoor_valid:
        absolute_humidity_indoor = absolute_humidity(temperature, relative_humidity)
    if outdoor_valid:
        absolute_humidity_outdoor = absolute_humidity(
            outdoor_temperature, outdoor_relative_humidity
        )
    delta = (
        absolute_humidity_indoor - absolute_humidity_outdoor
        if absolute_humidity_indoor is not None and absolute_humidity_outdoor is not None
        else None
    )
    margin = (
        outdoor_drying_hysteresis_margin
        if cycle_active
        else outdoor_drying_margin
    )
    ventilation_effective = delta >= margin if delta is not None else None

    strong_warning = (
        ventilation_needed
        and relative_humidity > high_humidity_threshold
        and high_humidity_seconds >= high_humidity_duration_minutes * 60
    )
    mold_critical_duration_minutes = mold_warning_duration_minutes / 2
    mold_critical_warning = (
        mold_risk == "high"
        and high_wall_humidity_seconds >= mold_critical_duration_minutes * 60
    )
    mold_warning_elapsed = (
        mold_model_active
        and wall_humidity is not None
        and mold_warning_threshold <= wall_humidity < mold_critical_threshold
        and high_wall_humidity_seconds >= mold_warning_duration_minutes * 60
    )
    mold_ventilation_recommended = (
        mold_risk_requires_ventilation
        and ventilation_effective is True
    )
    cycle_close_required = cycle_active and (
        ventilation_needed
        and not mold_risk_requires_ventilation
        and ventilation_effective is False
    )
    window_open_seconds = max(0, _number(window_open_seconds) or 0)
    window_close_due_to_timeout = (
        window_open is True
        and window_open_duration_minutes > 0
        and window_open_seconds >= window_open_duration_minutes * 60
        and not cycle_close_required
    )
    persistent_mold_warning = mold_critical_warning or mold_warning_elapsed
    close_required = window_open is True and (
        target_reached
        or cycle_close_required
        or window_close_due_to_timeout
        or (persistent_mold_warning and ventilation_effective is False)
    )

    status = _resolve_status(
        indoor_valid=indoor_valid,
        window_close_due_to_timeout=window_close_due_to_timeout,
        close_required=close_required,
        mold_critical_warning=mold_critical_warning,
        mold_warning_elapsed=mold_warning_elapsed,
        ventilation_needed=ventilation_needed,
        mold_ventilation_recommended=mold_ventilation_recommended,
        strong_warning=strong_warning,
    )

    # Hint priorities differ from status priorities, so resolve their keys here.
    hint_base_key = None
    hint_outdoor_key = None
    hint_single_key = None
    hint_placeholders = {}

    if not indoor_valid:
        hint_single_key = "data_missing"
    elif mold_critical_warning:
        hint_base_key = "base_mold_critical"
        hint_placeholders = {
            "threshold": f"{mold_critical_threshold:g}",
            "duration": f"{mold_critical_duration_minutes:g}",
        }
        hint_outdoor_key = (
            "outdoor_effective_critical"
            if ventilation_effective is True
            else _outdoor_hint_key(ventilation_effective)
        )
    elif mold_warning_elapsed:
        hint_base_key = "base_mold_warning"
        hint_placeholders = {
            "threshold": f"{mold_warning_threshold:g}",
            "duration": f"{mold_warning_duration_minutes:g}",
        }
        hint_outdoor_key = _outdoor_hint_key(ventilation_effective)
    elif mold_risk_requires_ventilation:
        hint_base_key = "base_mold_current"
        hint_placeholders = {"humidity": wall_humidity}
        hint_outdoor_key = (
            "outdoor_effective"
            if mold_ventilation_recommended
            else _outdoor_hint_key(ventilation_effective)
        )
    elif not ventilation_needed:
        hint_single_key = "humidity_ok"
    elif not outdoor_valid or ventilation_effective is None:
        hint_base_key = "base_humidity_high" if strong_warning else "base_humidity_elevated"
        hint_outdoor_key = "outdoor_unknown"
    elif ventilation_effective is False:
        hint_base_key = "base_humidity_high" if strong_warning else "base_humidity_elevated"
        hint_outdoor_key = "outdoor_ineffective"
    elif strong_warning:
        hint_base_key = "base_humidity_high"
        hint_placeholders = {
            "threshold": f"{high_humidity_threshold:g}",
            "duration": f"{high_humidity_duration_minutes:g}",
        }
        hint_outdoor_key = "outdoor_effective"
    else:
        hint_base_key = "base_humidity_elevated"
        hint_outdoor_key = "outdoor_effective"

    if status == "close_recommended":
        if target_reached:
            hint_single_key = "window_target_reached"
        elif window_close_due_to_timeout:
            hint_single_key = "window_open_too_long"
            hint_placeholders = {"duration": f"{window_open_duration_minutes:g}"}
        elif not persistent_mold_warning:
            if ventilation_effective is False:
                hint_single_key = "window_outdoor_unsuitable"
            elif window_open is True:
                hint_single_key = "window_close"

    status_text = STATUS_LABELS[status]

    return {
        "status": status,
        "status_text": status_text,
        "ventilation_needed": ventilation_needed or mold_ventilation_recommended,
        "mold_ventilation_recommended": mold_ventilation_recommended,
        "target_reached": target_reached,
        "close_required": close_required,
        "high_humidity_seconds": max(0, int(high_humidity_seconds)),
        "high_wall_humidity_seconds": max(0, int(high_wall_humidity_seconds)),
        "temperature": temperature if indoor_valid else None,
        "relative_humidity": relative_humidity if indoor_valid else None,
        "absolute_humidity_indoor": absolute_humidity_indoor,
        "absolute_humidity_outdoor": absolute_humidity_outdoor,
        "estimated_wall_humidity": wall_humidity,
        "mold_risk": mold_risk,
        "ventilation_hint_base_key": hint_base_key,
        "ventilation_hint_outdoor_key": hint_outdoor_key,
        "ventilation_hint_single_key": hint_single_key,
        "ventilation_hint_placeholders": hint_placeholders,
        "ventilation_hint_window_prefix": (
            window_open is True and status not in ("ok", "close_recommended")
        ),
        "window_open": window_open,
        "ventilation_effective": ventilation_effective,
    }
