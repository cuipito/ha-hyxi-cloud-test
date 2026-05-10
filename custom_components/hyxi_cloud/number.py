"""Number platform for HYXI Cloud device control power settings."""

import logging
from typing import ClassVar, NamedTuple

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower
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
    EM_DEFAULTS,
    MANUFACTURER,
    detect_phase_type,
    get_raw_device_code,
    normalize_device_type,
)

_LOGGER = logging.getLogger(__name__)


class EMNumberDef(NamedTuple):
    """Definition for an Energy Manager number entity."""

    key: str
    unit: str
    min_val: float
    max_val: float
    step: float
    icon: str


# Always-on: created for any controllable inverter, EM NOT required
ALWAYS_ON_NUMBER_DEFS: list[EMNumberDef] = [
    EMNumberDef("soc_min", "%", 5, 50, 1, "mdi:battery-20"),
    EMNumberDef("soc_max", "%", 50, 100, 1, "mdi:battery-90"),
]

# EM-only: created only when Energy Manager is enabled in options
EM_NUMBER_DEFS: list[EMNumberDef] = [
    EMNumberDef("high_load_threshold", "W", 1000, 20000, 500, "mdi:flash-alert"),
    EMNumberDef("battery_capacity_wh", "Wh", 1000, 50000, 100, "mdi:battery"),
    EMNumberDef("max_charge_power", "W", 500, 15000, 100, "mdi:lightning-bolt"),
    EMNumberDef(
        "max_discharge_power", "W", 500, 15000, 100, "mdi:lightning-bolt-outline"
    ),
    EMNumberDef(
        "min_solar_for_charge", "W", 200, 3000, 100, "mdi:solar-power-variant-outline"
    ),
    EMNumberDef("mode_switch_cooldown", "s", 10, 300, 5, "mdi:timer-outline"),
    EMNumberDef("power_change_threshold", "W", 10, 500, 10, "mdi:delta"),
    EMNumberDef("power_adjust_cooldown", "s", 5, 120, 5, "mdi:timer-sand"),
    EMNumberDef("night_buffer_pct", "%", 0, 20, 1, "mdi:shield-half-full"),
    EMNumberDef("avg_night_consumption", "W", 100, 2000, 50, "mdi:weather-night"),
    EMNumberDef("charge_margin", "W", 0, 500, 25, "mdi:margin"),
    EMNumberDef(
        "charge_entry_threshold", "W", 100, 2000, 50, "mdi:solar-power-variant-outline"
    ),
    EMNumberDef("charge_reentry_delay", "s", 30, 600, 15, "mdi:timer-lock"),
    EMNumberDef("bottomout_cooldown", "s", 60, 900, 30, "mdi:timer-alert"),
]


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
    em_sn = entry.options.get(CONF_EM_INVERTER_SN)

    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))

        if device_type not in ("hybrid_inverter", "all_in_one"):
            continue

        phase = detect_phase_type(dev_data)

        # Power numbers pair with mode control (1062-1065) — three-phase only
        if phase == "three_phase":
            entities.append(HyxiPowerNumber(coordinator, sn, dev_data, "charge"))
            entities.append(HyxiPowerNumber(coordinator, sn, dev_data, "discharge"))

        # Always-on EM numbers (soc_min/soc_max) — useful without EM
        for numdef in ALWAYS_ON_NUMBER_DEFS:
            entities.append(EMParameterNumber(coordinator, sn, numdef))

        # EM-only numbers — only when EM is enabled for this inverter
        if entry.options.get(CONF_EM_ENABLED) and em_sn == sn:
            for numdef in EM_NUMBER_DEFS:
                entities.append(
                    EMParameterNumber(coordinator, sn, numdef, em_device=True)
                )

    # Microinverter power limit (controlId 3012)
    for sn, dev_data in coordinator.data.items():
        device_type = normalize_device_type(get_raw_device_code(dev_data))
        if device_type == "micro_inverter":
            entities.append(HyxiMicroPowerLimit(coordinator, sn, dev_data))

    if entities:
        async_add_entities(entities)


class HyxiPowerNumber(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity for setting the wattage used by charge/discharge mode commands.

    This entity stores the desired power level locally. The value is sent to
    the inverter when the user presses the Charge or Discharge mode button.
    """

    _attr_has_entity_name = True
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_min_value = 1
    _attr_icon = "mdi:flash"

    _MAX_POWER_KEYS: ClassVar[dict[str, str]] = {
        "charge": "maxChargePower",
        "discharge": "maxDischargePower",
    }

    def __init__(
        self,
        coordinator,
        sn: str,
        dev_data: dict,
        direction: str,
    ) -> None:
        """Initialize the power number entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._direction = direction
        self._attr_unique_id = f"hyxi_{sn}_{direction}_power"
        self._attr_translation_key = f"{direction}_power"
        metrics = dev_data.get("metrics") or {}
        metric_key = self._MAX_POWER_KEYS.get(direction, "")
        self._attr_native_max_value = int(_safe_int(metrics.get(metric_key), 10000))
        self._attr_native_value = 100
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
                self._attr_native_value = int(float(last_state.state))
            except ValueError, TypeError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Set the power value."""
        self._attr_native_value = int(value)
        self.async_write_ha_state()


class HyxiMicroPowerLimit(CoordinatorEntity, NumberEntity, RestoreEntity):
    """Number entity for microinverter power limit (controlId 3012).

    Sets the maximum power output as a percentage of rated power.
    The value is sent to the inverter immediately on change.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "micro_power_limit"
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER
    _attr_native_step = 1.0
    _attr_native_min_value = 0.0
    _attr_native_max_value = 100.0
    _attr_native_value = 100.0
    _attr_icon = "mdi:speedometer"

    def __init__(self, coordinator, sn: str, dev_data: dict) -> None:
        """Initialize the micro power limit entity."""
        super().__init__(coordinator)
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_micro_power_limit"
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
            except ValueError, TypeError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Set the power limit and send to inverter."""
        client = self.coordinator.client
        try:
            await client.set_micro_power_limit(self._sn, int(value))
            self._attr_native_value = value
            self.async_write_ha_state()
        except HyxiApiClient.ControlError as err:
            _LOGGER.error(
                "Failed to set power limit to %d%% for %s: %s",
                int(value),
                self._sn,
                err,
            )
            raise


class EMParameterNumber(NumberEntity, RestoreEntity):
    """Number entity for an Energy Manager parameter.

    Stores a tunable value locally (RestoreEntity). The engine reads it
    each tick via _get_param().
    """

    _attr_has_entity_name = True
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator,
        sn: str,
        numdef: EMNumberDef,
        em_device: bool = False,
    ) -> None:
        """Initialize the EM parameter number entity."""
        self._sn = sn
        self._attr_unique_id = f"hyxi_{sn}_em_{numdef.key}"
        self._attr_translation_key = f"em_{numdef.key}"
        self._attr_native_unit_of_measurement = numdef.unit
        self._attr_native_min_value = numdef.min_val
        self._attr_native_max_value = numdef.max_val
        self._attr_native_step = numdef.step
        self._attr_icon = numdef.icon
        self._attr_native_value = float(EM_DEFAULTS.get(numdef.key, 0))

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
            try:
                self._attr_native_value = float(last_state.state)
            except ValueError, TypeError:
                pass

    async def async_set_native_value(self, value: float) -> None:
        """Set the parameter value."""
        self._attr_native_value = value
        self.async_write_ha_state()


def _safe_int(val, default: int) -> int:
    """Safely convert a value to int with a fallback default."""
    if val is None:
        return default
    try:
        result = int(float(val))
        return result if result > 0 else default
    except ValueError, TypeError:
        return default
