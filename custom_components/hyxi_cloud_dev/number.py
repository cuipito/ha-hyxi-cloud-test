"""Number platform for HYXI Cloud device control power settings."""

import logging

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, normalize_device_type, get_raw_device_code

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up HYXI number entities for charge/discharge power settings."""
    coordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.data:
        return

    entities: list[NumberEntity] = []

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        if device_type in ("hybrid_inverter", "micro_ess", "all_in_one"):
            # Determine max power from device info, fall back to 10000W
            metrics = dev_data.get("metrics") or {}
            max_charge = _safe_int(metrics.get("maxChargePower"), 10000)
            max_discharge = _safe_int(metrics.get("maxDischargePower"), 10000)

            entities.append(
                HyxiPowerNumber(
                    coordinator, sn, dev_data,
                    direction="charge",
                    max_value=max_charge,
                )
            )
            entities.append(
                HyxiPowerNumber(
                    coordinator, sn, dev_data,
                    direction="discharge",
                    max_value=max_discharge,
                )
            )

    if entities:
        async_add_entities(entities)


class HyxiPowerNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity for setting the wattage used by charge/discharge mode commands.

    This entity stores the desired power level locally. The value is sent to
    the inverter when the user selects 'charge' or 'discharge' in the
    operating mode select entity.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_native_step = 100.0
    _attr_native_min_value = 0.0
    _attr_icon = "mdi:flash"

    def __init__(
        self,
        coordinator,
        sn: str,
        dev_data: dict,
        direction: str,
        max_value: int,
    ) -> None:
        """Initialize the power number entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._direction = direction
        self._attr_unique_id = f"hyxi_{sn}_{direction}_power"
        self._attr_translation_key = f"{direction}_power"
        self._attr_native_max_value = float(max_value)
        self._attr_native_value = 100.0
        self._attr_device_info = {
            "identifiers": {(DOMAIN, sn)},
            "name": dev_data.get("device_name") or f"Device {sn}",
            "manufacturer": MANUFACTURER,
            "model": dev_data.get("model"),
            "serial_number": sn,
        }

    async def async_added_to_hass(self) -> None:
        """Restore last known value on startup."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            try:
                self._attr_native_value = float(last_state.state)
            except (ValueError, TypeError):
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Set the power value."""
        self._attr_native_value = value
        self.async_write_ha_state()


def _safe_int(val, default: int) -> int:
    """Safely convert a value to int with a fallback default."""
    if val is None:
        return default
    try:
        result = int(float(val))
        return result if result > 0 else default
    except (ValueError, TypeError):
        return default
