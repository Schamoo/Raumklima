import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.core import CoreState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from custom_components import raumklima as integration
from custom_components.raumklima import frontend
from custom_components.raumklima.binary_sensor import RoomClimateBinarySensor
from custom_components.raumklima.config_flow import RoomOptionsFlow
from custom_components.raumklima.const import (
    ATTR_CLIMATE_ENTITY,
    CONF_CALIBRATION_FACTOR,
    CONF_KIND,
    CONF_KIND_GLOBAL,
    CONF_KIND_ROOM,
    CONF_MOLD_WARNING_THRESHOLD,
    CONF_ROOM_NAME,
    CONF_VTHERM_ENTITY,
    DEFAULT_SETTINGS,
    DOMAIN,
    SERVICE_TOGGLE_CLIMATE_PRESET,
)
from custom_components.raumklima.frontend import _CARD_VERSION
from custom_components.raumklima.sensor import (
    RoomClimateCoordinator,
    RoomClimateValueSensor,
)


class HomeAssistantBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_room_options_flow_rejects_duplicate_source(self):
        current_entry = SimpleNamespace(
            entry_id="current",
            data={CONF_KIND: CONF_KIND_ROOM, CONF_ROOM_NAME: "Office"},
            options={},
        )
        other_entry = SimpleNamespace(
            entry_id="other",
            data={CONF_KIND: CONF_KIND_ROOM, CONF_VTHERM_ENTITY: "climate.room"},
            options={},
        )
        flow = SimpleNamespace(
            config_entry=current_entry,
            hass=SimpleNamespace(
                config_entries=SimpleNamespace(async_entries=Mock(return_value=[current_entry, other_entry]))
            ),
            async_show_form=Mock(side_effect=lambda **kwargs: kwargs),
            async_create_entry=Mock(),
        )

        result = await RoomOptionsFlow.async_step_init(
            flow,
            {
                CONF_ROOM_NAME: "Office",
                CONF_VTHERM_ENTITY: "climate.room",
                CONF_CALIBRATION_FACTOR: 1.4,
            },
        )

        self.assertEqual(result["errors"]["base"], "room_source_already_configured")
        flow.async_create_entry.assert_not_called()

    async def test_toggle_service_switches_eco_to_comfort(self):
        registered = {}
        climate_state = SimpleNamespace(
            attributes={"preset_mode": "eco", "preset_modes": ["eco", "comfort"]}
        )
        hass = SimpleNamespace(
            data={},
            states=SimpleNamespace(get=Mock(return_value=climate_state)),
            services=SimpleNamespace(
                async_register=Mock(side_effect=lambda domain, name, handler, **kwargs: registered.update({name: handler})),
                async_call=AsyncMock(),
            ),
        )

        with patch.object(integration, "async_setup_frontend", new_callable=AsyncMock):
            await integration.async_setup(hass, {})

        await registered[SERVICE_TOGGLE_CLIMATE_PRESET](
            SimpleNamespace(data={ATTR_CLIMATE_ENTITY: "climate.room"})
        )
        hass.services.async_call.assert_awaited_once_with(
            "climate",
            "set_preset_mode",
            {"entity_id": "climate.room", "preset_mode": "comfort"},
            blocking=True,
        )

    async def test_toggle_service_rejects_unsupported_preset(self):
        registered = {}
        climate_state = SimpleNamespace(
            attributes={"preset_mode": "eco", "preset_modes": ["eco"]}
        )
        hass = SimpleNamespace(
            data={},
            states=SimpleNamespace(get=Mock(return_value=climate_state)),
            services=SimpleNamespace(
                async_register=Mock(side_effect=lambda domain, name, handler, **kwargs: registered.update({name: handler})),
                async_call=AsyncMock(),
            ),
        )
        with patch.object(integration, "async_setup_frontend", new_callable=AsyncMock):
            await integration.async_setup(hass, {})

        with self.assertRaises(HomeAssistantError):
            await registered[SERVICE_TOGGLE_CLIMATE_PRESET](
                SimpleNamespace(data={ATTR_CLIMATE_ENTITY: "climate.room"})
            )
        hass.services.async_call.assert_not_awaited()

    async def test_toggle_service_rejects_non_climate_and_missing_entities(self):
        cases = (
            ("sensor.room", SimpleNamespace(attributes={})),
            ("climate.missing", None),
        )
        for entity_id, climate_state in cases:
            with self.subTest(entity_id=entity_id):
                registered = {}
                hass = SimpleNamespace(
                    data={},
                    states=SimpleNamespace(get=Mock(return_value=climate_state)),
                    services=SimpleNamespace(
                        async_register=Mock(
                            side_effect=lambda domain, name, handler, **kwargs: registered.update({name: handler})
                        ),
                        async_call=AsyncMock(),
                    ),
                )
                with patch.object(integration, "async_setup_frontend", new_callable=AsyncMock):
                    await integration.async_setup(hass, {})

                with self.assertRaises(HomeAssistantError):
                    await registered[SERVICE_TOGGLE_CLIMATE_PRESET](
                        SimpleNamespace(data={ATTR_CLIMATE_ENTITY: entity_id})
                    )
                hass.services.async_call.assert_not_awaited()

    async def test_room_entry_setup_and_unload_manage_coordinator(self):
        entry = SimpleNamespace(
            entry_id="room-entry",
            data={CONF_KIND: CONF_KIND_ROOM},
            options={},
            async_on_unload=Mock(),
            add_update_listener=Mock(return_value=Mock()),
        )
        coordinator = SimpleNamespace(async_setup=AsyncMock(), async_unload=Mock())
        hass = SimpleNamespace(
            data={DOMAIN: {"settings": dict(DEFAULT_SETTINGS), "coordinators": {}}},
            config=SimpleNamespace(language="en"),
            config_entries=SimpleNamespace(
                async_forward_entry_setups=AsyncMock(),
                async_unload_platforms=AsyncMock(return_value=True),
            ),
        )

        with (
            patch(
                "homeassistant.helpers.translation.async_get_translations",
                new_callable=AsyncMock,
                side_effect=[{}, {}],
            ),
            patch("custom_components.raumklima.sensor.RoomClimateCoordinator", return_value=coordinator),
        ):
            self.assertTrue(await integration.async_setup_entry(hass, entry))

        coordinator.async_setup.assert_awaited_once()
        self.assertIs(hass.data[DOMAIN]["coordinators"][entry.entry_id], coordinator)
        hass.config_entries.async_forward_entry_setups.assert_awaited_once()

        self.assertTrue(await integration.async_unload_entry(hass, entry))
        self.assertNotIn(entry.entry_id, hass.data[DOMAIN]["coordinators"])

    async def test_global_options_update_refreshes_loaded_rooms(self):
        coordinator = SimpleNamespace(async_refresh=Mock())
        hass = SimpleNamespace(
            data={DOMAIN: {"settings": dict(DEFAULT_SETTINGS), "coordinators": {"room": coordinator}}}
        )
        entry = SimpleNamespace(
            data={CONF_KIND: CONF_KIND_GLOBAL},
            options={CONF_MOLD_WARNING_THRESHOLD: 75},
        )

        await integration._async_update_listener(hass, entry)

        self.assertEqual(
            hass.data[DOMAIN]["settings"][CONF_MOLD_WARNING_THRESHOLD], 75
        )
        coordinator.async_refresh.assert_called_once()

    async def test_room_options_update_reloads_entry(self):
        hass = SimpleNamespace(
            config_entries=SimpleNamespace(async_reload=AsyncMock())
        )
        entry = SimpleNamespace(entry_id="room-entry", data={CONF_KIND: CONF_KIND_ROOM})

        await integration._async_update_listener(hass, entry)

        hass.config_entries.async_reload.assert_awaited_once_with("room-entry")

    async def test_restored_timers_are_loaded_and_listeners_started(self):
        saved = {
            "high_humidity_since": "2026-01-01T10:00:00+00:00",
            "high_wall_humidity_since": "2026-01-01T11:00:00+00:00",
            "previous_status": "ventilation_possible",
        }
        coordinator = object.__new__(RoomClimateCoordinator)
        coordinator._timer_store = SimpleNamespace(async_load=AsyncMock(return_value=saved))
        coordinator._high_humidity_since = None
        coordinator._high_humidity_timer_needs_validation = False
        coordinator._high_wall_humidity_since = None
        coordinator._previous_status = "ok"
        coordinator._update_source_listener = Mock()
        coordinator._refresh = Mock()
        coordinator.hass = SimpleNamespace()
        with patch("custom_components.raumklima.sensor.async_track_time_interval", return_value=Mock()):
            await RoomClimateCoordinator.async_setup(coordinator)

        self.assertIsNotNone(coordinator._high_humidity_since)
        self.assertTrue(coordinator._high_humidity_timer_needs_validation)
        self.assertIsNotNone(coordinator._high_wall_humidity_since)
        self.assertTrue(coordinator._high_wall_humidity_timer_needs_validation)
        self.assertEqual(coordinator._previous_status, "ventilation_possible")
        coordinator._update_source_listener.assert_called_once()
        coordinator._refresh.assert_called_once()

    def test_restored_wall_humidity_timer_survives_unavailable_first_refresh(self):
        saved_since = dt_util.parse_datetime("2026-01-01T11:00:00+00:00")
        coordinator = object.__new__(RoomClimateCoordinator)
        coordinator._data = {}
        coordinator._entry = SimpleNamespace(options={}, entry_id="room-entry")
        coordinator.hass = SimpleNamespace(
            data={DOMAIN: {"settings": dict(DEFAULT_SETTINGS)}},
            states=SimpleNamespace(get=Mock(return_value=None)),
        )
        coordinator._high_humidity_since = None
        coordinator._high_humidity_timer_needs_validation = False
        coordinator._high_wall_humidity_since = saved_since
        coordinator._high_wall_humidity_timer_needs_validation = True
        coordinator._previous_status = "ventilate"
        coordinator._timer_store = SimpleNamespace(async_delay_save=Mock())
        coordinator._update_source_listener = Mock()
        coordinator._translate = Mock(
            side_effect=lambda key, placeholders=None, fallback=None: fallback or ""
        )
        coordinator._listeners = []
        coordinator._window_entity = None

        RoomClimateCoordinator._refresh(coordinator)

        self.assertEqual(coordinator._high_wall_humidity_since, saved_since)
        self.assertTrue(coordinator._high_wall_humidity_timer_needs_validation)
        self.assertEqual(coordinator._previous_status, "ventilate")
        coordinator._timer_store.async_delay_save.assert_not_called()

    def test_vtherm_window_entity_change_replaces_listener(self):
        removed = Mock()
        register = Mock(side_effect=[removed, Mock()])
        coordinator = SimpleNamespace(
            _data={CONF_VTHERM_ENTITY: "climate.room"},
            _entry=SimpleNamespace(data={}, options={}),
            hass=SimpleNamespace(
                data={DOMAIN: {"outdoor_temp_entity": "sensor.outdoor_temp", "outdoor_humidity_entity": "sensor.outdoor_rh"}}
            ),
            _source_entities=[],
            _remove_source_listener=None,
            _handle_state_change=Mock(),
        )
        windows = iter(("binary_sensor.window_old", "binary_sensor.window_new"))
        coordinator._find_vtherm_window_entity = lambda: next(windows)

        with patch("custom_components.raumklima.sensor.async_track_state_change_event", register):
            RoomClimateCoordinator._update_source_listener(coordinator)
            RoomClimateCoordinator._update_source_listener(coordinator)

        removed.assert_called_once()
        self.assertIn("binary_sensor.window_new", coordinator._source_entities)
        self.assertEqual(register.call_count, 2)

    def test_unload_removes_source_and_minute_listeners(self):
        remove_source = Mock()
        remove_minute = Mock()
        coordinator = SimpleNamespace(
            _remove_source_listener=remove_source,
            _remove_minute_listener=remove_minute,
        )

        RoomClimateCoordinator.async_unload(coordinator)

        remove_source.assert_called_once()
        remove_minute.assert_called_once()

    def test_entity_registry_identity_migration_updates_legacy_unique_id(self):
        entity = SimpleNamespace(
            config_entry_id="room-entry",
            device_id="old-device",
            unique_id="climate.room_temperature",
            domain="sensor",
            platform=DOMAIN,
            entity_id="sensor.room_temperature",
        )
        entity_registry = _FakeEntityRegistry(entity)
        device = SimpleNamespace(
            id="old-device",
            identifiers={(DOMAIN, "climate.room")},
        )
        device_registry = _FakeDeviceRegistry(device)
        entry = SimpleNamespace(entry_id="room-entry")
        coordinator = SimpleNamespace(
            hass=SimpleNamespace(),
            _entry=entry,
        )

        with (
            patch("custom_components.raumklima.sensor.er.async_get", return_value=entity_registry),
            patch("custom_components.raumklima.sensor.dr.async_get", return_value=device_registry),
            patch(
                "custom_components.raumklima.sensor.dr.async_get_device_and_config_entry_for_domain",
                return_value=(device, entry),
            ),
        ):
            RoomClimateCoordinator._migrate_registry_identity(coordinator)

        self.assertEqual(entity.unique_id, "room-entry_temperature")
        self.assertIn((DOMAIN, "room-entry"), device.identifiers)

    async def test_frontend_registers_versioned_card_resource(self):
        resources = SimpleNamespace(
            async_get_info=AsyncMock(),
            async_items=Mock(return_value=[]),
            async_create_item=AsyncMock(),
        )
        hass = SimpleNamespace(
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            data={"lovelace": SimpleNamespace(resources=resources)},
            state=CoreState.running,
        )

        await frontend.async_setup_frontend(hass)

        resources.async_create_item.assert_awaited_once_with(
            {"url": f"/raumklima/raumklima-card.js?v={_CARD_VERSION}", "res_type": "module"}
        )

    async def test_frontend_updates_existing_card_resource_version(self):
        resources = SimpleNamespace(
            async_get_info=AsyncMock(),
            async_items=Mock(return_value=[
                {"id": "resource-id", "url": "/raumklima/raumklima-card.js?v=1.1.7", "type": "javascript"}
            ]),
            async_update_item=AsyncMock(),
            async_create_item=AsyncMock(),
        )
        hass = SimpleNamespace(
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            data={"lovelace": SimpleNamespace(resources=resources)},
            state=CoreState.running,
        )

        await frontend.async_setup_frontend(hass)

        resources.async_update_item.assert_awaited_once_with(
            "resource-id",
            {"url": f"/raumklima/raumklima-card.js?v={_CARD_VERSION}", "res_type": "module"},
        )
        resources.async_create_item.assert_not_awaited()

    async def test_frontend_leaves_yaml_resources_for_manual_configuration(self):
        resources = SimpleNamespace(async_get_info=AsyncMock())
        hass = SimpleNamespace(
            http=SimpleNamespace(async_register_static_paths=AsyncMock()),
            data={"lovelace": SimpleNamespace(resources=resources)},
            state=CoreState.running,
        )

        await frontend.async_setup_frontend(hass)

        resources.async_get_info.assert_not_awaited()

    def test_sensor_entity_timer_properties_expose_inactive_and_elapsed_values(self):
        sensor = object.__new__(RoomClimateValueSensor)
        sensor._key = "mold_warning_duration"
        sensor._attr_native_unit_of_measurement = "min"
        sensor.coordinator = SimpleNamespace(
            data={"mold_warning_duration": 0, "mold_warning_timer_active": False}
        )

        self.assertEqual(sensor.native_value, "off")
        self.assertIsNone(sensor.native_unit_of_measurement)

        sensor.coordinator.data.update(
            {"mold_warning_duration": 17, "mold_warning_timer_active": True}
        )
        self.assertEqual(sensor.native_value, 17)
        self.assertEqual(sensor.native_unit_of_measurement, "min")

    def test_binary_sensor_entity_property_reads_coordinator_state(self):
        entity = object.__new__(RoomClimateBinarySensor)
        entity._key = "close_required"
        entity.coordinator = SimpleNamespace(data={"close_required": True})
        self.assertTrue(entity.is_on)

    def test_card_cache_version_comes_from_manifest(self):
        integration_dir = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
        manifest = json.loads((integration_dir / "manifest.json").read_text(encoding="utf-8"))
        readme = (integration_dir.parents[1] / "README.md").read_text(encoding="utf-8")
        self.assertEqual(_CARD_VERSION, manifest["version"])
        self.assertIn("/raumklima/raumklima-card.js", readme)
        self.assertNotIn("raumklima-card.js?v=", readme)


class _FakeEntityRegistry:
    def __init__(self, entity):
        self.entities = {entity.entity_id: entity}

    def async_update_entity(self, entity_id, **updates):
        for key, value in updates.items():
            setattr(self.entities[entity_id], key.removeprefix("new_"), value)

    def async_remove(self, entity_id):
        self.entities.pop(entity_id, None)


class _FakeDeviceRegistry:
    def __init__(self, device):
        self.device = device

    def async_update_device(self, device_id, *, new_identifiers):
        self.device.identifiers = new_identifiers
        return self.device

    def async_remove_device(self, device_id):
        pass


if __name__ == "__main__":
    unittest.main()
