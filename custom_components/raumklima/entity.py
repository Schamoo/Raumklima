from homeassistant.core import callback

from .const import CONF_ROOM_NAME, DOMAIN


class RoomClimateEntityMixin:
    """Share device identity and coordinator updates across room entities."""

    _attr_has_entity_name = True

    def _initialize_room_entity(
        self,
        coordinator,
        key: str,
        *,
        unique_id_key: str | None = None,
    ) -> None:
        self.coordinator = coordinator
        self._key = key
        self._attr_unique_id = (
            f"{coordinator._entry.entry_id}_{unique_id_key or key}"
        )
        self._attr_device_info = {
            "identifiers": {(DOMAIN, coordinator._entry.entry_id)},
            "name": coordinator._data[CONF_ROOM_NAME],
            "manufacturer": "Raumklima",
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.async_add_listener(self._handle_update))
        self._handle_update()

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
