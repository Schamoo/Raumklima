from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_CALIBRATION_FACTOR,
    CONF_HIGH_HUMIDITY,
    CONF_HIGH_HUMIDITY_MINUTES,
    CONF_WINDOW_OPEN_DURATION_MINUTES,
    CONF_HUMIDITY_OFF,
    CONF_HUMIDITY_ON,
    CONF_HUMIDITY_ADVISORY_RESET,
    CONF_OUTDOOR_HUMIDITY_ENTITY,
    CONF_OUTDOOR_TEMP_ENTITY,
    CONF_ROOM_NAME,
    CONF_ROOM_HUMIDITY_ENTITY,
    CONF_ROOM_TEMP_ENTITY,
    CONF_VTHERM_ENTITY,
    DEFAULT_CALIBRATION_FACTOR,
    CONF_DRYING_HYSTERESIS,
    CONF_DRYING_MARGIN,
    CONF_MOLD_CRITICAL_THRESHOLD,
    CONF_MOLD_WARNING_THRESHOLD,
    CONF_MOLD_WARNING_DURATION_MINUTES,
    DEFAULT_SETTINGS,
    DOMAIN,
    STATUS_LABELS,
)
from .entity import RoomClimateEntityMixin
from .logic import (
    calculate_room_climate,
    estimated_wall_humidity,
    find_window_sensor_entity,
    climate_values_valid,
    resolve_room_settings,
    temperature_to_celsius,
    timer_sensor_unit,
    timer_sensor_value,
    update_high_humidity_since,
    update_wall_humidity_since,
    mold_model_applies,
)


TIMER_SENSOR_ACTIVE_KEYS = {
    "high_humidity_since": "high_humidity_timer_active",
    "mold_warning_duration": "mold_warning_timer_active",
}

_ROOM_ENTITY_SUFFIXES = (
    "temperature",
    "relative_humidity",
    "absolute_humidity_indoor",
    "absolute_humidity_outdoor",
    "estimated_wall_humidity_value",
    "estimated_wall_humidity",
    "mold_warning_duration",
    "high_humidity_since",
    "ventilation_hint",
    "ventilation_needed",
    "ventilation_effective",
    "target_reached",
    "window_open",
    "close_required",
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN]["coordinators"][entry.entry_id]
    entities = [
        RoomClimateAdviceSensor(coordinator),
        RoomClimateValueSensor(coordinator, "temperature"),
        RoomClimateValueSensor(
            coordinator,
            "relative_humidity",
            device_class=SensorDeviceClass.HUMIDITY,
            unit=PERCENTAGE,
        ),
        RoomClimateValueSensor(
            coordinator,
            "absolute_humidity_indoor",
            unit="g/m³",
        ),
        RoomClimateValueSensor(
            coordinator,
            "absolute_humidity_outdoor",
            unit="g/m³",
        ),
        RoomClimateMoldRiskSensor(coordinator),
        RoomClimateValueSensor(
            coordinator,
            "mold_warning_duration",
            unit="min",
        ),
        RoomClimateValueSensor(
            coordinator,
            "high_humidity_since",
            unit="min",
        ),
        RoomClimateValueSensor(
            coordinator,
            "estimated_wall_humidity",
            unit=PERCENTAGE,
        ),
        RoomClimateTextSensor(
            coordinator,
            "ventilation_hint",
        ),
    ]
    async_add_entities(
        sorted(
            entities,
            key=lambda entity: coordinator.entity_name_sort_key(entity, "sensor"),
        )
    )


class RoomClimateCoordinator:
    """Track room inputs and share one calculated snapshot across its entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        translations: dict[str, str],
    ) -> None:
        self.hass = hass
        self._data = {**entry.data, **entry.options}
        self.identity = self._data.get(CONF_VTHERM_ENTITY) or (
            f"{self._data.get(CONF_ROOM_TEMP_ENTITY, 'room')}|"
            f"{self._data.get(CONF_ROOM_HUMIDITY_ENTITY, 'humidity')}"
        )
        domain_data = self.hass.data.get(DOMAIN, {})
        if not self._data.get(CONF_OUTDOOR_TEMP_ENTITY):
            self._data[CONF_OUTDOOR_TEMP_ENTITY] = domain_data.get("outdoor_temp_entity")
        if not self._data.get(CONF_OUTDOOR_HUMIDITY_ENTITY):
            self._data[CONF_OUTDOOR_HUMIDITY_ENTITY] = domain_data.get(
                "outdoor_humidity_entity"
            )
        self._entry = entry
        self._translations = translations
        self._timer_store = Store(
            hass,
            1,
            f"{DOMAIN}_{entry.entry_id}_timers",
        )
        self._high_humidity_since = None
        self._high_humidity_timer_needs_validation = False
        self._high_wall_humidity_since = None
        self._previous_status = "ok"
        self.data = {}
        self._listeners = []
        self._remove_source_listener = None
        self._remove_minute_listener = None
        self._window_entity = self._find_vtherm_window_entity()
        self._source_entities = []
        self._migrate_registry_identity()

    def _migrate_registry_identity(self) -> None:
        """Keep room devices and entities stable when source sensors change."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        stable_identifier = (DOMAIN, self._entry.entry_id)

        all_registry_entries = list(entity_registry.entities.values())
        registry_entries = [
            registry_entry
            for registry_entry in all_registry_entries
            if registry_entry.config_entry_id == self._entry.entry_id
        ]
        room_devices = {}
        for device_id in {
            registry_entry.device_id
            for registry_entry in registry_entries
            if registry_entry.device_id
        }:
            device, config_entry = dr.async_get_device_and_config_entry_for_domain(
                self.hass, device_id, domain=DOMAIN
            )
            if (
                device is not None
                and config_entry is not None
                and config_entry.entry_id == self._entry.entry_id
                and any(domain == DOMAIN for domain, _ in device.identifiers)
            ):
                room_devices[device.id] = device

        device = next(
            (
                item
                for item in room_devices.values()
                if stable_identifier in item.identifiers
            ),
            next(iter(room_devices.values()), None),
        )
        if device is not None and stable_identifier not in device.identifiers:
            identifiers = {
                identifier
                for identifier in device.identifiers
                if identifier[0] != DOMAIN
            }
            identifiers.add(stable_identifier)
            device = device_registry.async_update_device(
                device.id, new_identifiers=identifiers
            )

        registered_unique_ids = {
            (registry_entry.domain, registry_entry.platform, registry_entry.unique_id):
                registry_entry.entity_id
            for registry_entry in all_registry_entries
        }
        for registry_entry in registry_entries:
            if registry_entry.config_entry_id != self._entry.entry_id:
                continue

            suffix = next(
                (
                    candidate
                    for candidate in _ROOM_ENTITY_SUFFIXES
                    if registry_entry.unique_id.endswith(f"_{candidate}")
                ),
                None,
            )
            if suffix is None:
                continue

            unique_id = f"{self._entry.entry_id}_{suffix}"
            unique_key = (registry_entry.domain, DOMAIN, unique_id)
            existing_entity_id = registered_unique_ids.get(unique_key)
            if existing_entity_id and existing_entity_id != registry_entry.entity_id:
                entity_registry.async_remove(registry_entry.entity_id)
                continue

            updates = {}
            if registry_entry.unique_id != unique_id:
                updates["new_unique_id"] = unique_id
                registered_unique_ids.pop(
                    (
                        registry_entry.domain,
                        registry_entry.platform,
                        registry_entry.unique_id,
                    ),
                    None,
                )
                registered_unique_ids[unique_key] = registry_entry.entity_id
            if device is not None and registry_entry.device_id != device.id:
                updates["device_id"] = device.id
            if updates:
                entity_registry.async_update_entity(registry_entry.entity_id, **updates)

        if device is not None:
            for duplicate_id in room_devices:
                if duplicate_id == device.id:
                    continue
                for registry_entry in list(entity_registry.entities.values()):
                    if registry_entry.device_id == duplicate_id:
                        entity_registry.async_update_entity(
                            registry_entry.entity_id, device_id=device.id
                        )
                device_registry.async_remove_device(duplicate_id)

    async def async_setup(self) -> None:
        stored_timers = await self._timer_store.async_load()
        if stored_timers:
            self._high_humidity_since = self._parse_saved_datetime(
                stored_timers.get("high_humidity_since")
            )
            self._high_humidity_timer_needs_validation = (
                self._high_humidity_since is not None
            )
            self._high_wall_humidity_since = self._parse_saved_datetime(
                stored_timers.get("high_wall_humidity_since")
            )
        self._update_source_listener()
        self._remove_minute_listener = async_track_time_interval(
            self.hass, self._handle_interval, timedelta(minutes=1)
        )
        self._refresh()

    @staticmethod
    def _parse_saved_datetime(value):
        if not isinstance(value, str):
            return None
        return dt_util.parse_datetime(value)

    @callback
    def _timer_store_data(self) -> dict[str, str | None]:
        return {
            "high_humidity_since": (
                self._high_humidity_since.isoformat()
                if self._high_humidity_since is not None
                else None
            ),
            "high_wall_humidity_since": (
                self._high_wall_humidity_since.isoformat()
                if self._high_wall_humidity_since is not None
                else None
            ),
        }

    def async_unload(self) -> None:
        if self._remove_source_listener:
            self._remove_source_listener()
        if self._remove_minute_listener:
            self._remove_minute_listener()

    def async_add_listener(self, listener):
        self._listeners.append(listener)

        def remove_listener():
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove_listener

    def entity_ids(self) -> dict[str, str | None]:
        """Return registered entity IDs for this room's dashboard card."""
        registry = er.async_get(self.hass)
        identity = self._entry.entry_id
        entity_ids = {
            "room_climate": registry.async_get_entity_id(
                "sensor", DOMAIN, self._entry.entry_id
            ),
            "temperature": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{self._entry.entry_id}_temperature"
            ),
            "relative_humidity": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{self._entry.entry_id}_relative_humidity"
            ),
            "absolute_humidity_indoor": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{self._entry.entry_id}_absolute_humidity_indoor"
            ),
            "absolute_humidity_outdoor": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{self._entry.entry_id}_absolute_humidity_outdoor"
            ),
            "estimated_wall_humidity": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{identity}_estimated_wall_humidity_value"
            ),
            "mold_warning_duration": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{identity}_mold_warning_duration"
            ),
            "mold_risk": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{identity}_estimated_wall_humidity"
            ),
            "ventilation_hint": registry.async_get_entity_id(
                "sensor", DOMAIN, f"{identity}_ventilation_hint"
            ),
        }
        for key in (
            "ventilation_needed",
            "ventilation_effective",
            "target_reached",
            "window_open",
            "close_required",
        ):
            entity_ids[key] = registry.async_get_entity_id(
                "binary_sensor", DOMAIN, f"{identity}_{key}"
            )
        entity_ids["climate"] = self._data.get(CONF_VTHERM_ENTITY)
        return entity_ids

    def entity_name_sort_key(self, entity, platform: str) -> str:
        explicit_name = getattr(entity, "_attr_name", None)
        if explicit_name is not None:
            return explicit_name.casefold()
        translation_key = entity._attr_translation_key or ""
        translated_name = self._translations.get(
            f"component.{DOMAIN}.entity.{platform}.{translation_key}.name",
            translation_key,
        )
        return translated_name.casefold()

    def _find_vtherm_window_entity(self):
        vtherm_entity = self._data.get(CONF_VTHERM_ENTITY)
        if not vtherm_entity:
            return None
        registry_entry = er.async_get(self.hass).async_get(vtherm_entity)
        if registry_entry is None or registry_entry.config_entry_id is None:
            return None
        vtherm_entry = self.hass.config_entries.async_get_entry(
            registry_entry.config_entry_id
        )
        if vtherm_entry is None:
            return None
        return find_window_sensor_entity(
            {"data": vtherm_entry.data, "options": vtherm_entry.options}
        )

    @callback
    def _handle_state_change(self, event: Event) -> None:
        if event.data["entity_id"] == self._data.get(CONF_VTHERM_ENTITY):
            old_state = event.data["old_state"]
            new_state = event.data["new_state"]
            if old_state is not None and new_state is not None and all(
                old_state.attributes.get(attribute)
                == new_state.attributes.get(attribute)
                for attribute in ("current_temperature", "current_humidity", "temperature_unit")
            ):
                return
        self._refresh()

    @callback
    def _handle_interval(self, now) -> None:
        self._refresh()

    def _update_source_listener(self) -> None:
        # VTherm can change its window contact while this room entry stays loaded.
        self._window_entity = self._find_vtherm_window_entity()
        domain_data = self.hass.data.get(DOMAIN, {})
        global_temp = domain_data.get("outdoor_temp_entity")
        global_humidity = domain_data.get("outdoor_humidity_entity")
        self._data[CONF_OUTDOOR_TEMP_ENTITY] = global_temp or self._entry.data.get(
            CONF_OUTDOOR_TEMP_ENTITY
        )
        self._data[CONF_OUTDOOR_HUMIDITY_ENTITY] = global_humidity or self._entry.data.get(
            CONF_OUTDOOR_HUMIDITY_ENTITY
        )
        source_entities = list(
            dict.fromkeys(
                entity_id
                for entity_id in (
                    self._data.get(CONF_VTHERM_ENTITY),
                    self._data.get(CONF_ROOM_TEMP_ENTITY),
                    self._data.get(CONF_ROOM_HUMIDITY_ENTITY),
                    self._data.get(CONF_OUTDOOR_TEMP_ENTITY),
                    self._data.get(CONF_OUTDOOR_HUMIDITY_ENTITY),
                    self._window_entity,
                )
                if entity_id
            )
        )
        if source_entities == self._source_entities:
            return
        if self._remove_source_listener:
            self._remove_source_listener()
        self._source_entities = source_entities
        self._remove_source_listener = async_track_state_change_event(
            self.hass, self._source_entities, self._handle_state_change
        )

    @callback
    def _refresh(self) -> None:
        self._update_source_listener()
        now = dt_util.utcnow()
        previous_timer_state = (
            self._high_humidity_since,
            self._high_wall_humidity_since,
        )
        vtherm_entity = self._data.get(CONF_VTHERM_ENTITY)
        room_temp_entity = self._data.get(CONF_ROOM_TEMP_ENTITY)
        room_humidity_entity = self._data.get(CONF_ROOM_HUMIDITY_ENTITY)
        outdoor_temperature_entity = self._data.get(CONF_OUTDOOR_TEMP_ENTITY)
        outdoor_humidity_entity = self._data.get(CONF_OUTDOOR_HUMIDITY_ENTITY)
        vtherm = self.hass.states.get(vtherm_entity) if vtherm_entity else None
        room_temperature_sensor = (
            self.hass.states.get(room_temp_entity) if room_temp_entity else None
        )
        room_humidity_sensor = (
            self.hass.states.get(room_humidity_entity) if room_humidity_entity else None
        )
        outdoor_temperature = (
            self.hass.states.get(outdoor_temperature_entity)
            if outdoor_temperature_entity
            else None
        )
        outdoor_humidity = (
            self.hass.states.get(outdoor_humidity_entity)
            if outdoor_humidity_entity
            else None
        )
        window = self.hass.states.get(self._window_entity) if self._window_entity else None
        window_open_seconds = (
            max(0, (now - window.last_changed).total_seconds())
            if window is not None and window.state == "on"
            else 0
        )

        if vtherm:
            temperature = temperature_to_celsius(
                vtherm.attributes.get("current_temperature"),
                vtherm.attributes.get("temperature_unit")
                or self.hass.config.units.temperature_unit,
            )
            relative_humidity = vtherm.attributes.get("current_humidity")
        else:
            temperature = temperature_to_celsius(
                room_temperature_sensor.state if room_temperature_sensor else None,
                room_temperature_sensor.attributes.get("unit_of_measurement")
                if room_temperature_sensor
                else None,
            )
            relative_humidity = (
                room_humidity_sensor.state if room_humidity_sensor else None
            )
        indoor_valid = climate_values_valid(temperature, relative_humidity)
        domain_data = self.hass.data.get(DOMAIN, {})
        settings = resolve_room_settings(
            domain_data.get("settings", DEFAULT_SETTINGS),
            self._entry.options,
        )
        calibration_factor = self._entry.options.get(
            CONF_CALIBRATION_FACTOR,
            DEFAULT_CALIBRATION_FACTOR,
        )
        outdoor_temp_value = temperature_to_celsius(
            outdoor_temperature.state if outdoor_temperature else None,
            outdoor_temperature.attributes.get("unit_of_measurement")
            if outdoor_temperature
            else None,
        )
        outdoor_humidity_value = (
            outdoor_humidity.state if outdoor_humidity else None
        )
        if indoor_valid:
            self._high_humidity_since = update_high_humidity_since(
                relative_humidity,
                True,
                self._high_humidity_since,
                now,
                threshold=settings[CONF_HIGH_HUMIDITY],
            )
            self._high_humidity_timer_needs_validation = False
        elif not self._high_humidity_timer_needs_validation:
            self._high_humidity_since = update_high_humidity_since(
                relative_humidity,
                False,
                self._high_humidity_since,
                now,
                threshold=settings[CONF_HIGH_HUMIDITY],
            )
        wall_humidity = estimated_wall_humidity(
            temperature,
            outdoor_temp_value,
            relative_humidity,
            calibration_factor,
        )
        # Only track wall humidity timer when mold model applies (outdoor < indoor)
        # Otherwise reset the timer - no condensation risk when it's warmer outside
        timer_wall_humidity = (
            wall_humidity
            if mold_model_applies(temperature, outdoor_temp_value)
            else None
        )
        self._high_wall_humidity_since = update_wall_humidity_since(
            timer_wall_humidity,
            self._high_wall_humidity_since,
            now,
            threshold=settings[CONF_MOLD_WARNING_THRESHOLD],
        )
        if previous_timer_state != (
            self._high_humidity_since,
            self._high_wall_humidity_since,
        ):
            self._timer_store.async_delay_save(self._timer_store_data, 1)
        high_humidity_seconds = (
            (now - self._high_humidity_since).total_seconds()
            if self._high_humidity_since is not None
            else 0
        )

        self.data = calculate_room_climate(
            temperature=temperature,
            relative_humidity=relative_humidity,
            outdoor_temperature=outdoor_temp_value,
            outdoor_relative_humidity=outdoor_humidity_value,
            window_state=window.state if window else None,
            previous_status=self._previous_status,
            high_humidity_seconds=high_humidity_seconds,
            window_open_seconds=window_open_seconds,
            humidity_on_threshold=settings[CONF_HUMIDITY_ON],
            humidity_off_threshold=settings[CONF_HUMIDITY_OFF],
            humidity_advisory_reset_threshold=settings[CONF_HUMIDITY_ADVISORY_RESET],
            high_humidity_threshold=settings[CONF_HIGH_HUMIDITY],
            high_humidity_duration_minutes=settings[CONF_HIGH_HUMIDITY_MINUTES],
            window_open_duration_minutes=settings[CONF_WINDOW_OPEN_DURATION_MINUTES],
            outdoor_drying_margin=settings[CONF_DRYING_MARGIN],
            outdoor_drying_hysteresis_margin=settings[CONF_DRYING_HYSTERESIS],
            calibration_factor=calibration_factor,
            mold_warning_threshold=settings[CONF_MOLD_WARNING_THRESHOLD],
            mold_critical_threshold=settings[CONF_MOLD_CRITICAL_THRESHOLD],
            mold_warning_duration_minutes=settings[CONF_MOLD_WARNING_DURATION_MINUTES],
            wall_humidity_value=wall_humidity,
            high_wall_humidity_seconds=(
                (now - self._high_wall_humidity_since).total_seconds()
                if self._high_wall_humidity_since is not None
                else 0
            ),
        )
        self.data["high_humidity_since"] = int(high_humidity_seconds // 60)
        self.data["high_humidity_timer_active"] = self._high_humidity_since is not None
        self.data["high_wall_humidity_since"] = (
            self._high_wall_humidity_since.isoformat()
            if self._high_wall_humidity_since is not None
            else None
        )
        self.data["mold_warning_duration"] = int(
            self.data["high_wall_humidity_seconds"] // 60
        )
        self.data["mold_warning_timer_active"] = (
            self._high_wall_humidity_since is not None
        )
        self.data["mold_warning_duration_target"] = settings[
            CONF_MOLD_WARNING_DURATION_MINUTES
        ]
        self.data["mold_critical_duration"] = (
            settings[CONF_MOLD_WARNING_DURATION_MINUTES] / 2
        )
        # Build ventilation hint using new flat key structure (base_* + outdoor_*)
        hint_base_key = self.data.get("ventilation_hint_base_key")
        hint_outdoor_key = self.data.get("ventilation_hint_outdoor_key")
        hint_single_key = self.data.get("ventilation_hint_single_key")
        hint_values = self.data.get("ventilation_hint_placeholders", {})

        if hint_single_key:
            # Simple key (data_missing, humidity_ok, window_*, etc.)
            self.data["ventilation_hint"] = self._translate(
                f"common.{hint_single_key}",
                hint_values,
                "",
            )
        elif hint_base_key and hint_outdoor_key:
            # Combine base + outdoor keys
            base_text = self._translate(f"common.{hint_base_key}", hint_values, "")
            outdoor_text = self._translate(f"common.{hint_outdoor_key}", {}, "")
            self.data["ventilation_hint"] = f"{base_text} {outdoor_text}".strip()
        else:
            self.data["ventilation_hint"] = ""
        if self.data.get("ventilation_hint_window_prefix"):
            self.data["ventilation_hint"] = self._translate(
                "common.window_open_prefix",
                {"hint": self.data["ventilation_hint"]},
                self.data["ventilation_hint"],
            )
        self.data["status_text"] = self._translate(
            f"entity.sensor.status.state.{self.data['status']}",
            fallback=self.data["status_text"],
        )
        # Don't overwrite previous_status when data is missing - preserves ventilation cycle
        if self.data["status"] != "missing_data":
            self._previous_status = self.data["status"]
        for listener in tuple(self._listeners):
            listener()

    @callback
    def async_refresh(self) -> None:
        self._refresh()

    def _translate(
        self,
        key: str,
        placeholders: dict | None = None,
        fallback: str | None = None,
    ) -> str:
        value = self._translations.get(f"component.{DOMAIN}.{key}")
        if value is None:
            return fallback or ""
        return value.format(**(placeholders or {}))


class RoomClimateSensorBase(RoomClimateEntityMixin, SensorEntity):
    def __init__(
        self,
        coordinator: RoomClimateCoordinator,
        key: str,
        *,
        unique_id_key: str | None = None,
    ) -> None:
        self._initialize_room_entity(
            coordinator,
            key,
            unique_id_key=unique_id_key,
        )


class RoomClimateAdviceSensor(RoomClimateSensorBase):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(STATUS_LABELS)
    _attr_icon = "mdi:home-thermometer"
    _attr_translation_key = "status"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(coordinator, "status")
        self._attr_unique_id = coordinator._entry.entry_id

    @property
    def native_value(self):
        return self.coordinator.data.get("status", "missing_data")

    @property
    def extra_state_attributes(self):
        return {
            "room_name": self.coordinator._data[CONF_ROOM_NAME],
            "room_entities": self.coordinator.entity_ids(),
            "status_text": self.coordinator.data.get("status_text"),
            "ventilation_hint": self.coordinator.data.get("ventilation_hint"),
            "window_open": self.coordinator.data.get("window_open"),
            "ventilation_needed": self.coordinator.data.get("ventilation_needed"),
            "ventilation_effective": self.coordinator.data.get(
                "ventilation_effective"
            ),
            "target_reached": self.coordinator.data.get("target_reached"),
            "close_required": self.coordinator.data.get("close_required"),
            "mold_risk": self.coordinator.data.get("mold_risk"),
        }


class RoomClimateValueSensor(RoomClimateSensorBase):
    _attr_entity_registry_enabled_default = True
    _attr_entity_registry_visible_default = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: RoomClimateCoordinator,
        key: str,
        *,
        device_class=None,
        unit=None,
    ) -> None:
        super().__init__(
            coordinator,
            key,
            unique_id_key=(
                "estimated_wall_humidity_value"
                if key == "estimated_wall_humidity"
                else None
            ),
        )
        self._attr_translation_key = key
        self._attr_device_class = device_class
        self._attr_native_unit_of_measurement = unit
        if key in TIMER_SENSOR_ACTIVE_KEYS:
            self._attr_state_class = None
            self._attr_icon = "mdi:timer-outline"
        if key == "temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    @property
    def native_value(self):
        active_key = TIMER_SENSOR_ACTIVE_KEYS.get(self._key)
        if active_key:
            return timer_sensor_value(
                self.coordinator.data.get(self._key),
                self.coordinator.data.get(active_key, False),
            )
        return self.coordinator.data.get(self._key)

    @property
    def native_unit_of_measurement(self):
        active_key = TIMER_SENSOR_ACTIVE_KEYS.get(self._key)
        if active_key:
            return timer_sensor_unit(
                self._attr_native_unit_of_measurement,
                self.coordinator.data.get(active_key, False),
            )
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        if self._key == "mold_warning_duration":
            return {
                "warning_duration_minutes": self.coordinator.data.get(
                    "mold_warning_duration_target"
                ),
                "critical_duration_minutes": self.coordinator.data.get(
                    "mold_critical_duration"
                )
            }
        return None


class RoomClimateMoldRiskSensor(RoomClimateSensorBase):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["low", "elevated", "high", "unknown", "not_relevant"]
    _attr_translation_key = "mold_risk"
    _attr_icon = "mdi:home-alert-outline"

    def __init__(self, coordinator: RoomClimateCoordinator) -> None:
        super().__init__(
            coordinator,
            "mold_risk",
            unique_id_key="estimated_wall_humidity",
        )

    @property
    def native_value(self):
        return self.coordinator.data.get("mold_risk", "unknown")


class RoomClimateTextSensor(RoomClimateSensorBase):
    def __init__(
        self,
        coordinator: RoomClimateCoordinator,
        key: str,
    ) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key
    @property
    def native_value(self):
        return self.coordinator.data.get(self._key)

