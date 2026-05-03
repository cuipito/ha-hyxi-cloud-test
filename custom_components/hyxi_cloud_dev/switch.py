"""Switch platform for HYXI Cloud device control."""

import logging

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from ._vendor.hyxi_cloud_api import HyxiControlError
from .const import DOMAIN, MANUFACTURER, normalize_device_type, get_raw_device_code

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI switch entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[SwitchEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        # Frequency control for hybrid inverters
        if device_type == "hybrid_inverter":
            entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiFrequencyControlSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for Frequency Control enable/disable (controlId 1020).

    State is tracked internally after successful writes as the API does not
    return the current frequency control state in polling responses.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "frequency_control"
    _attr_icon = "mdi:sine-wave"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the frequency control switch."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_frequency_control"
        self._attr_is_on = None
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Enable frequency control."""
        client = self.coordinator.client
        try:
            await client.set_frequency_control(self._sn, enabled=True)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiControlError as err:
            _LOGGER.error(
                "Failed to enable frequency control for %s: %s", self._sn, err
            )
            raise

    async def async_turn_off(self, **kwargs) -> None:
        """Disable frequency control."""
        client = self.coordinator.client
        try:
            await client.set_frequency_control(self._sn, enabled=False)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiControlError as err:
            _LOGGER.error(
                "Failed to disable frequency control for %s: %s", self._sn, err
            )
            raise
