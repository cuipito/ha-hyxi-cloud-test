"""Select platform for HYXI Cloud device control."""

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import HyxiApiClient

from .const import DOMAIN, MANUFACTURER, get_raw_device_code, normalize_device_type

_LOGGER = logging.getLogger(__name__)

MODE_OPTIONS = ["idle", "charge", "discharge", "self_consume"]
PEAK_SHAVING_OPTIONS = ["close", "charge", "discharge", "stop", "hold"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI select entities."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[SelectEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        # Mode control for hybrid inverters and all-in-one devices
        if device_type in ("hybrid_inverter", "all_in_one"):
            entities.append(HyxiModeSelect(coordinator, sn, dev_data))

        # Peak Shaving for hybrid inverters and all-in-one devices
        if device_type in ("hybrid_inverter", "all_in_one"):
            entities.append(HyxiPeakShavingSelect(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiModeSelect(CoordinatorEntity, SelectEntity):
    """Select entity for inverter operating mode (controlIds 1062-1065).

    The HYXI API does not return the current operating mode in its polling data,
    so the entity tracks state internally after successful writes. After a
    restart the state will show as 'unknown' until the user makes a selection.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "operating_mode"
    _attr_options = MODE_OPTIONS
    _attr_icon = "mdi:solar-power-variant-outline"
    _attr_current_option: str | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the mode select entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_operating_mode"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the operating mode."""
        client = self.coordinator.client
        try:
            if option == "idle":
                await client.set_mode_idle(self._sn)
            elif option == "charge":
                watts = _get_power_value(self.hass, self._sn, "charge")
                _LOGGER.debug("Setting %s to CHARGE at %dW", self._sn, watts)
                await client.set_mode_charge(self._sn, watts)
            elif option == "discharge":
                watts = _get_power_value(self.hass, self._sn, "discharge")
                _LOGGER.debug("Setting %s to DISCHARGE at %dW", self._sn, watts)
                await client.set_mode_discharge(self._sn, watts)
            elif option == "self_consume":
                await client.set_mode_self_consume(self._sn)
            else:
                _LOGGER.error("Unknown mode option: %s", option)
                return

            self._attr_current_option = option
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()

        except HyxiApiClient.ControlError as err:
            _LOGGER.error("Failed to set mode to %s for %s: %s", option, self._sn, err)
            raise


class HyxiPeakShavingSelect(CoordinatorEntity, SelectEntity):
    """Select entity for Peak Shaving control (controlId 1021).

    State is tracked internally after successful writes as the API does not
    return the current peak-shaving state in polling responses.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "peak_shaving"
    _attr_options = PEAK_SHAVING_OPTIONS
    _attr_icon = "mdi:chart-bell-curve-cumulative"
    _attr_current_option: str | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the peak shaving select entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_peak_shaving"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_select_option(self, option: str) -> None:
        """Change the peak shaving action."""
        client = self.coordinator.client
        try:
            await client.set_peak_shaving(self._sn, option)
            self._attr_current_option = option
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to set peak shaving to %s for %s: %s",
                option,
                self._sn,
                err,
            )
            raise


def _get_power_value(hass: HomeAssistant, sn: str, direction: str) -> int:
    """Read the wattage from the paired number entity.

    Looks up the entity by unique_id via the entity registry, since HA-assigned
    entity_ids don't follow a predictable pattern.
    Falls back to 100W if the number entity has not been set yet.
    """
    unique_id = f"hyxi_{sn}_{direction}_power"
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("number", DOMAIN, unique_id)
    if entity_id is None:
        _LOGGER.warning(
            "Power number entity (unique_id=%s) not found in registry, using default 100W",
            unique_id,
        )
        return 100
    state = hass.states.get(entity_id)
    if state is not None and state.state not in ("unknown", "unavailable"):
        try:
            return int(float(state.state))
        except ValueError, TypeError:
            pass
    _LOGGER.warning(
        "Power number entity %s not available, using default 100W", entity_id
    )
    return 100
