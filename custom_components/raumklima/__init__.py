from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.exceptions import HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

from .const import (
    ATTR_CLIMATE_ENTITY,
    CONF_KIND,
    CONF_KIND_GLOBAL,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    DEFAULT_SETTINGS,
    DOMAIN,
    PLATFORMS,
    SERVICE_TOGGLE_CLIMATE_PRESET,
)
from .frontend import async_setup_frontend


def _settings_from_entry(entry: ConfigEntry) -> dict:
    values = dict(DEFAULT_SETTINGS)
    values.update(entry.data)
    values.update(entry.options)
    return {key: values[key] for key in DEFAULT_SETTINGS}


def _domain_data(hass: HomeAssistant) -> dict:
    return hass.data.setdefault(
        DOMAIN,
        {"settings": dict(DEFAULT_SETTINGS), "coordinators": {}},
    )


def _refresh_room_coordinators(hass: HomeAssistant) -> None:
    domain_data = hass.data.get(DOMAIN, {})
    for coordinator in domain_data.get("coordinators", {}).values():
        coordinator.async_refresh()


def _apply_global_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    domain_data = _domain_data(hass)
    domain_data["settings"] = _settings_from_entry(entry)
    for key in (CONF_OUTDOOR_TEMP_ENTITY, CONF_OUTDOOR_HUMIDITY_ENTITY):
        domain_data[key] = entry.options.get(key, entry.data.get(key))
    _refresh_room_coordinators(hass)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    _domain_data(hass)

    async def async_toggle_climate_preset(call) -> None:
        climate_entity = call.data[ATTR_CLIMATE_ENTITY]
        if not climate_entity.startswith("climate."):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="invalid_climate_entity",
            )

        climate_state = hass.states.get(climate_entity)
        if climate_state is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="climate_entity_not_found",
                translation_placeholders={"climate_entity": climate_entity},
            )

        current_preset = climate_state.attributes.get("preset_mode")
        available_presets = climate_state.attributes.get("preset_modes")
        target_preset = "comfort" if current_preset == "eco" else "eco"
        if not available_presets or target_preset not in available_presets:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="preset_not_supported",
                translation_placeholders={
                    "climate_entity": climate_entity,
                    "preset": target_preset,
                },
            )

        await hass.services.async_call(
            "climate",
            "set_preset_mode",
            {"entity_id": climate_entity, "preset_mode": target_preset},
            blocking=True,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_TOGGLE_CLIMATE_PRESET,
        async_toggle_climate_preset,
        schema=vol.Schema({vol.Required(ATTR_CLIMATE_ENTITY): str}),
    )
    await async_setup_frontend(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = _domain_data(hass)
    if entry.data.get(CONF_KIND) == CONF_KIND_GLOBAL:
        _apply_global_entry(hass, entry)
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
        return True

    from homeassistant.helpers.translation import async_get_translations

    translations = await async_get_translations(
        hass, hass.config.language, "common", {DOMAIN}
    )
    translations.update(
        await async_get_translations(hass, hass.config.language, "entity", {DOMAIN})
    )

    from .sensor import RoomClimateCoordinator

    coordinator = RoomClimateCoordinator(hass, entry, translations)
    await coordinator.async_setup()
    domain_data["coordinators"][entry.entry_id] = coordinator
    entry.async_on_unload(coordinator.async_unload)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    domain_data = hass.data.get(DOMAIN, {})
    if entry.data.get(CONF_KIND) == CONF_KIND_GLOBAL:
        domain_data["settings"] = dict(DEFAULT_SETTINGS)
        domain_data["outdoor_temp_entity"] = None
        domain_data["outdoor_humidity_entity"] = None
        _refresh_room_coordinators(hass)
        return True

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        domain_data.get("coordinators", {}).pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    if entry.data.get(CONF_KIND) == CONF_KIND_GLOBAL:
        _apply_global_entry(hass, entry)
        return
    await hass.config_entries.async_reload(entry.entry_id)
