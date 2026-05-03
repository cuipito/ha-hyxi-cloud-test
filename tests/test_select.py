"""Tests for the select platform entities."""
# pylint: disable=wrong-import-position

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from tests.conftest import ensure_mock  # noqa: E402


# Create proper base classes that don't cause metaclass conflicts
class _FakeSelectEntity:
    """Minimal stand-in for homeassistant SelectEntity."""
    _attr_has_entity_name = False
    _attr_translation_key = None
    _attr_options = []
    _attr_current_option = None
    _attr_icon = None

    @property
    def options(self):
        return self._attr_options

    def async_write_ha_state(self): ...


class _FakeCoordinatorEntity:
    """Minimal stand-in for CoordinatorEntity."""
    def __init__(self, coordinator):
        self.coordinator = coordinator


# Patch the HA modules with proper classes
mock_select_mod = MagicMock()
mock_select_mod.SelectEntity = _FakeSelectEntity
sys.modules["homeassistant.components.select"] = mock_select_mod

mock_number_mod = MagicMock()
sys.modules["homeassistant.components.number"] = mock_number_mod

mock_switch_mod = MagicMock()
sys.modules["homeassistant.components.switch"] = mock_switch_mod

# Ensure CoordinatorEntity is a proper class
mock_uc = sys.modules.get("homeassistant.helpers.update_coordinator")
if mock_uc:
    mock_uc.CoordinatorEntity = _FakeCoordinatorEntity

from custom_components.hyxi_cloud._vendor.hyxi_cloud_api import HyxiControlError
from custom_components.hyxi_cloud.select import (
    HyxiModeSelect,
    HyxiPeakShavingSelect,
    _get_power_value,
)


def _make_coordinator_and_client():
    """Create a mock coordinator with a mock API client."""
    client = MagicMock()
    client.set_mode_idle = AsyncMock()
    client.set_mode_charge = AsyncMock()
    client.set_mode_discharge = AsyncMock()
    client.set_mode_self_consume = AsyncMock()
    client.set_peak_shaving = AsyncMock()
    client.set_frequency_control = AsyncMock()

    coordinator = MagicMock()
    coordinator.client = client
    coordinator.async_request_refresh = AsyncMock()
    coordinator.data = {
        "SN001": {
            "device_name": "Test Inverter",
            "model": "Hybrid Inverter",
            "device_type_code": "HYBRID_INVERTER",
            "metrics": {
                "maxChargePower": 5000,
                "maxDischargePower": 5000,
            },
        }
    }
    return coordinator, client


class TestHyxiModeSelect:
    """Tests for HyxiModeSelect."""

    def test_options(self):
        """Verify the entity exposes all four mode options."""
        coordinator, _ = _make_coordinator_and_client()
        entity = HyxiModeSelect(coordinator, "SN001", coordinator.data["SN001"])
        assert entity.options == ["idle", "charge", "discharge", "self_consume"]

    def test_select_idle(self):
        """Selecting idle calls set_mode_idle."""
        coordinator, client = _make_coordinator_and_client()
        entity = HyxiModeSelect(coordinator, "SN001", coordinator.data["SN001"])
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()

        asyncio.run(
            entity.async_select_option("idle")
        )

        client.set_mode_idle.assert_awaited_once_with("SN001")
        assert entity._attr_current_option == "idle"
        coordinator.async_request_refresh.assert_awaited_once()

    def test_select_charge_uses_power_entity(self):
        """Selecting charge reads wattage from the number entity."""
        coordinator, client = _make_coordinator_and_client()
        entity = HyxiModeSelect(coordinator, "SN001", coordinator.data["SN001"])

        # Mock hass.states.get to return a power value
        mock_state = MagicMock()
        mock_state.state = "500"
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = mock_state
        entity.hass = mock_hass
        entity.async_write_ha_state = MagicMock()

        asyncio.run(
            entity.async_select_option("charge")
        )

        client.set_mode_charge.assert_awaited_once_with("SN001", 500)
        mock_hass.states.get.assert_called_with("number.hyxi_SN001_charge_power")

    def test_select_discharge(self):
        """Selecting discharge reads wattage from the discharge power entity."""
        coordinator, client = _make_coordinator_and_client()
        entity = HyxiModeSelect(coordinator, "SN001", coordinator.data["SN001"])

        mock_state = MagicMock()
        mock_state.state = "300"
        mock_hass = MagicMock()
        mock_hass.states.get.return_value = mock_state
        entity.hass = mock_hass
        entity.async_write_ha_state = MagicMock()

        asyncio.run(
            entity.async_select_option("discharge")
        )

        client.set_mode_discharge.assert_awaited_once_with("SN001", 300)

    def test_select_self_consume(self):
        """Selecting self_consume calls set_mode_self_consume."""
        coordinator, client = _make_coordinator_and_client()
        entity = HyxiModeSelect(coordinator, "SN001", coordinator.data["SN001"])
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()

        asyncio.run(
            entity.async_select_option("self_consume")
        )

        client.set_mode_self_consume.assert_awaited_once_with("SN001")


class TestHyxiPeakShavingSelect:
    """Tests for HyxiPeakShavingSelect."""

    def test_options(self):
        """Verify the entity exposes all peak shaving options."""
        coordinator, _ = _make_coordinator_and_client()
        entity = HyxiPeakShavingSelect(
            coordinator, "SN001", coordinator.data["SN001"]
        )
        assert entity.options == ["close", "charge", "discharge", "stop", "hold"]

    @pytest.mark.parametrize("option", ["close", "charge", "discharge", "stop", "hold"])
    def test_select_option(self, option):
        """Each option calls set_peak_shaving with the correct action."""
        coordinator, client = _make_coordinator_and_client()
        entity = HyxiPeakShavingSelect(
            coordinator, "SN001", coordinator.data["SN001"]
        )
        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()

        asyncio.run(
            entity.async_select_option(option)
        )

        client.set_peak_shaving.assert_awaited_once_with("SN001", option)
        assert entity._attr_current_option == option


class TestGetPowerValue:
    """Tests for the _get_power_value helper."""

    def test_reads_from_state(self):
        """Returns the numeric value from the state object."""
        mock_state = MagicMock()
        mock_state.state = "750"
        hass = MagicMock()
        hass.states.get.return_value = mock_state

        result = _get_power_value(hass, "SN001", "charge")
        assert result == 750

    def test_falls_back_when_unavailable(self):
        """Returns 100W default when entity state is unavailable."""
        hass = MagicMock()
        hass.states.get.return_value = None

        result = _get_power_value(hass, "SN001", "charge")
        assert result == 100

    def test_falls_back_when_unknown(self):
        """Returns 100W default when entity state is 'unknown'."""
        mock_state = MagicMock()
        mock_state.state = "unknown"
        hass = MagicMock()
        hass.states.get.return_value = mock_state

        result = _get_power_value(hass, "SN001", "charge")
        assert result == 100
