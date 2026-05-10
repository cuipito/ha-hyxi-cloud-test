"""Button platform for HYXI Cloud device control."""

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import HyxiApiClient

from .const import (
    DOMAIN,
    MANUFACTURER,
    get_raw_device_code,
    normalize_device_type,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI button entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[ButtonEntity] = []

    # Microinverter restart (controlId 3013)
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type == "micro_inverter":
            entities.append(HyxiMicroRestartButton(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiMicroRestartButton(CoordinatorEntity, ButtonEntity):
    """Button entity to restart a Microinverter (controlId 3013)."""

    _attr_has_entity_name = True
    _attr_translation_key = "micro_restart"
    _attr_icon = "mdi:restart"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the microinverter restart button."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_micro_restart"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_press(self) -> None:
        """Restart the microinverter."""
        client = self.coordinator.client
        try:
            await client.restart_device(self._sn)
            _LOGGER.info("Restart command sent to microinverter %s", self._sn)
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error("Failed to restart microinverter %s: %s", self._sn, err)
            raise
