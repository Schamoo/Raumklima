from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .entity import RoomClimateEntityMixin


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities):
    coordinator = hass.data[DOMAIN]["coordinators"][entry.entry_id]
    entities = [
        RoomClimateBinarySensor(coordinator, "ventilation_needed", "mdi:air-filter"),
        RoomClimateBinarySensor(coordinator, "ventilation_effective", "mdi:weather-windy"),
        RoomClimateBinarySensor(coordinator, "target_reached", "mdi:check-circle-outline"),
        RoomClimateBinarySensor(
            coordinator,
            "window_open",
            "mdi:window-open-variant",
            device_class=BinarySensorDeviceClass.WINDOW,
        ),
        RoomClimateBinarySensor(
            coordinator,
            "close_required",
            "mdi:window-shutter-alert",
        ),
    ]
    async_add_entities(
        sorted(
            entities,
            key=lambda entity: coordinator.entity_name_sort_key(
                entity, "binary_sensor"
            ),
        )
    )


class RoomClimateBinarySensor(RoomClimateEntityMixin, BinarySensorEntity):
    def __init__(
        self,
        coordinator,
        key: str,
        icon: str,
        *,
        device_class: BinarySensorDeviceClass | None = None,
    ) -> None:
        self._initialize_room_entity(coordinator, key)
        self._attr_translation_key = key
        self._attr_icon = icon
        self._attr_device_class = device_class

    @property
    def is_on(self) -> bool | None:
        return self.coordinator.data.get(self._key)
