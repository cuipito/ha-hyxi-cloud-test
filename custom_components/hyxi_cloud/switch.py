"""Switch platform for HYXI Cloud device control."""

from __future__ import annotations

import logging
from typing import ClassVar

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from hyxi_cloud_api import HyxiApiClient

from .const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    DOMAIN,
    MANUFACTURER,
    detect_phase_type,
    get_raw_device_code,
    normalize_device_type,
)

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
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue

        phase = detect_phase_type(dev_data)

        # Frequency control (controlId 1020) — single-phase devices only
        if phase == "single_phase":
            entities.append(HyxiFrequencyControlSwitch(coordinator, sn, dev_data))

        # Always-on: grid charge allowed toggle
        entities.append(EMToggleSwitch(coordinator, sn, "grid_charge_allowed"))

        # EM-only switches — only when EM is enabled for this inverter
        if entry.options.get(CONF_EM_ENABLED) and em_sn == sn:
            entities.append(
                EMToggleSwitch(
                    coordinator, sn, "enabled", default_on=True, em_device=True
                )
            )
            entities.append(
                EMToggleSwitch(
                    coordinator,
                    sn,
                    "high_load_battery_assist",
                    default_on=False,
                    em_device=True,
                )
            )

    # Microinverter power on/off (controlId 3011)
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type == "micro_inverter":
            entities.append(HyxiMicroPowerSwitch(coordinator, sn, dev_data))

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
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the frequency control switch."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_frequency_control"
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
        except HyxiApiClient.ControlError as err:
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
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to disable frequency control for %s: %s", self._sn, err
            )
            raise


class HyxiMicroPowerSwitch(CoordinatorEntity, SwitchEntity):
    """Switch entity for Microinverter power on/off (controlId 3011).

    State is tracked internally after successful writes as the API does not
    return the current power state in polling responses.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "micro_power"
    _attr_icon = "mdi:power"
    _attr_is_on: bool | None = None

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the microinverter power switch."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_micro_power"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on the microinverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_on(self._sn)
            self._attr_is_on = True
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error("Failed to power on microinverter %s: %s", self._sn, err)
            raise

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off the microinverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_off(self._sn)
            self._attr_is_on = False
            self.async_write_ha_state()
            await self.coordinator.async_request_refresh()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error("Failed to power off microinverter %s: %s", self._sn, err)
            raise


class EMToggleSwitch(SwitchEntity, RestoreEntity):
    """Toggle switch for Energy Manager parameters.

    Stores state locally (RestoreEntity). The engine reads it each tick.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_is_on: bool | None = None

    _ICONS: ClassVar[dict[str, str]] = {
        "grid_charge_allowed": "mdi:transmission-tower-import",
        "enabled": "mdi:robot",
        "high_load_battery_assist": "mdi:flash-alert-outline",
    }

    def __init__(
        self,
        coordinator,
        sn: str,
        key: str,
        default_on: bool = False,
        em_device: bool = False,
    ) -> None:
        """Initialize the EM toggle switch."""
        self._sn = sn
        self._default_on = default_on
        self._attr_unique_id = f"hyxi_{sn}_em_{key}"
        self._attr_translation_key = f"em_{key}"
        self._attr_icon = self._ICONS.get(key, "mdi:toggle-switch")
        self._attr_is_on = default_on

        if em_device:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, f"{sn}_energy_manager")},
            }
        else:
            self._attr_device_info = {
                "identifiers": {(DOMAIN, sn)},
            }

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            self._attr_is_on = last_state.state == "on"

    async def async_turn_on(self, **kwargs) -> None:
        """Turn on."""
        self._attr_is_on = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        """Turn off."""
        self._attr_is_on = False
        self.async_write_ha_state()
