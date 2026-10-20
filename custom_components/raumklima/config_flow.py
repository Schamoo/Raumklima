import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_DRYING_HYSTERESIS,
    CONF_DRYING_MARGIN,
    CONF_CALIBRATION_FACTOR,
    CONF_HIGH_HUMIDITY,
    CONF_HIGH_HUMIDITY_MINUTES,
    CONF_WINDOW_OPEN_DURATION_MINUTES,
    CONF_HUMIDITY_OFF,
    CONF_HUMIDITY_ON,
    CONF_HUMIDITY_ADVISORY_RESET,
    CONF_KIND,
    CONF_KIND_GLOBAL,
    CONF_KIND_ROOM,
    CONF_MOLD_CRITICAL_THRESHOLD,
    CONF_MOLD_WARNING_THRESHOLD,
    CONF_MOLD_WARNING_DURATION_MINUTES,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOM_NAME,
    CONF_ROOM_HUMIDITY_ENTITY,
    CONF_ROOM_TEMP_ENTITY,
    CONF_VTHERM_ENTITY,
    DEFAULT_SETTINGS,
    DEFAULT_CALIBRATION_FACTOR,
    DOMAIN,
)

def _room_source_fields(defaults):
    schema = {
        vol.Required(CONF_ROOM_NAME, default=defaults.get(CONF_ROOM_NAME, "")): selector.TextSelector(),
    }
    for key, domain in (
        (CONF_VTHERM_ENTITY, "climate"),
        (CONF_ROOM_TEMP_ENTITY, "sensor"),
        (CONF_ROOM_HUMIDITY_ENTITY, "sensor"),
    ):
        default = defaults.get(key)
        schema[
            vol.Optional(key, default=default)
            if default
            else vol.Optional(key)
        ] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
    return schema


def _room_source_id(values):
    vtherm_entity = values.get(CONF_VTHERM_ENTITY)
    if vtherm_entity:
        return vtherm_entity
    room_temp_entity = values.get(CONF_ROOM_TEMP_ENTITY)
    room_humidity_entity = values.get(CONF_ROOM_HUMIDITY_ENTITY)
    if room_temp_entity and room_humidity_entity:
        return f"{room_temp_entity}|{room_humidity_entity}"
    return None


def _number_field(key, default, *, minimum, maximum=None, step=1, unit=None):
    selector_config = {"min": minimum, "step": step}
    if maximum is not None:
        selector_config["max"] = maximum
    if unit is not None:
        selector_config["unit_of_measurement"] = unit
    return (
        vol.Required(key, default=default),
        selector.NumberSelector(
            selector.NumberSelectorConfig(**selector_config)
        ),
    )


def _room_input_errors(values, existing_source_ids=()):
    room_name = values[CONF_ROOM_NAME].strip()
    if not room_name:
        return room_name, {CONF_ROOM_NAME: "required"}

    source_id = _room_source_id(values)
    if source_id is None:
        return room_name, {"base": "room_source_required"}
    if source_id in existing_source_ids:
        return room_name, {"base": "room_source_already_configured"}
    return room_name, {}


def _room_options_schema(defaults):
    schema = _room_source_fields(defaults)
    field, number_selector = _number_field(
        CONF_CALIBRATION_FACTOR,
        defaults.get(CONF_CALIBRATION_FACTOR, DEFAULT_CALIBRATION_FACTOR),
        minimum=1,
        step=0.05,
    )
    schema[field] = number_selector
    return vol.Schema(schema)


_GLOBAL_NUMBER_FIELDS = (
    (CONF_HUMIDITY_ON, 0, 100, 1, "%"),
    (CONF_HUMIDITY_OFF, 0, 100, 1, "%"),
    (CONF_HUMIDITY_ADVISORY_RESET, 0, 100, 1, "%"),
    (CONF_HIGH_HUMIDITY, 0, 100, 1, "%"),
    (CONF_HIGH_HUMIDITY_MINUTES, 1, 240, 1, "min"),
    (CONF_WINDOW_OPEN_DURATION_MINUTES, 1, 240, 1, "min"),
    (CONF_DRYING_MARGIN, 0, 5, 0.1, "g/m³"),
    (CONF_DRYING_HYSTERESIS, 0, 5, 0.1, "g/m³"),
    (CONF_MOLD_WARNING_THRESHOLD, 0, 100, 1, "%"),
    (CONF_MOLD_CRITICAL_THRESHOLD, 0, 100, 1, "%"),
    (CONF_MOLD_WARNING_DURATION_MINUTES, 1, 240, 1, "min"),
)


def _global_schema(defaults):
    defaults = {**DEFAULT_SETTINGS, **(defaults or {})}
    schema = {
        vol.Required(
            CONF_OUTDOOR_TEMP_ENTITY,
            default=defaults.get(CONF_OUTDOOR_TEMP_ENTITY),
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
        vol.Required(
            CONF_OUTDOOR_HUMIDITY_ENTITY,
            default=defaults.get(CONF_OUTDOOR_HUMIDITY_ENTITY),
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
    }
    for key, minimum, maximum, step, unit in _GLOBAL_NUMBER_FIELDS:
        field, number_selector = _number_field(
            key,
            defaults[key],
            minimum=minimum,
            maximum=maximum,
            step=step,
            unit=unit,
        )
        schema[field] = number_selector
    return vol.Schema(schema)


def _validate_global_settings(values):
    if not (
        values[CONF_HUMIDITY_OFF]
        < values[CONF_HUMIDITY_ADVISORY_RESET]
        < values[CONF_HUMIDITY_ON]
        < values[CONF_HIGH_HUMIDITY]
    ):
        return {"base": "humidity_order"}
    if values[CONF_DRYING_HYSTERESIS] > values[CONF_DRYING_MARGIN]:
        return {"base": "drying_order"}
    if not values[CONF_MOLD_WARNING_THRESHOLD] < values[CONF_MOLD_CRITICAL_THRESHOLD]:
        return {"base": "mold_order"}
    return {}


class RaumklimaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if any(
            entry.data.get(CONF_KIND) == CONF_KIND_GLOBAL
            for entry in self._async_current_entries()
        ):
            return await self.async_step_room()
        return await self.async_step_global()

    async def async_step_room(self, user_input=None):
        errors = {}
        if user_input is not None:
            room_name, errors = _room_input_errors(user_input)
            if not errors:
                source_id = _room_source_id(user_input)
                await self.async_set_unique_id(source_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=room_name,
                    data={**user_input, CONF_KIND: CONF_KIND_ROOM, CONF_ROOM_NAME: room_name},
                )

        return self.async_show_form(
            step_id="room",
            data_schema=vol.Schema(_room_source_fields({})),
            errors=errors,
        )

    async def async_step_global(self, user_input=None):
        errors = {}
        if user_input is not None:
            errors = _validate_global_settings(user_input)
            if not errors:
                await self.async_set_unique_id("global_settings")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="Raumklima",
                    data={CONF_KIND: CONF_KIND_GLOBAL, **user_input},
                )

        return self.async_show_form(
            step_id="global",
            data_schema=_global_schema(DEFAULT_SETTINGS),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        if config_entry.data.get(CONF_KIND) == CONF_KIND_GLOBAL:
            return GlobalSettingsOptionsFlow()
        return RoomOptionsFlow()


class GlobalSettingsOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        defaults = dict(DEFAULT_SETTINGS)
        defaults.update(self.config_entry.data)
        defaults.update(self.config_entry.options)
        errors = _validate_global_settings(user_input) if user_input is not None else {}
        if user_input is not None and not errors:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init", data_schema=_global_schema(defaults), errors=errors
        )


class RoomOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        defaults = dict(self.config_entry.data)
        defaults.update(self.config_entry.options)
        defaults[CONF_CALIBRATION_FACTOR] = self.config_entry.options.get(
            CONF_CALIBRATION_FACTOR,
            DEFAULT_CALIBRATION_FACTOR,
        )
        errors = {}
        if user_input is not None:
            existing_sources = {
                _room_source_id({**entry.data, **entry.options})
                for entry in self.hass.config_entries.async_entries(DOMAIN)
                if entry.entry_id != self.config_entry.entry_id
                and entry.data.get(CONF_KIND) == CONF_KIND_ROOM
            }
            room_name, errors = _room_input_errors(user_input, existing_sources)
            if not errors:
                factor = user_input[CONF_CALIBRATION_FACTOR]
                if factor < 1:
                    errors[CONF_CALIBRATION_FACTOR] = "calibration_factor_min"
                else:
                    return self.async_create_entry(
                        title="",
                        data={**user_input, CONF_ROOM_NAME: room_name},
                    )
        return self.async_show_form(
            step_id="init", data_schema=_room_options_schema(defaults), errors=errors
        )
