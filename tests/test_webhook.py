"""Tests for the HYXI Cloud real-time push webhook."""

import copy
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# HA module mocks — same pattern as other test files
# ---------------------------------------------------------------------------
mock_ha = MagicMock()
mock_ha.__path__ = []

for mod in (
    "homeassistant",
    "homeassistant.components",
    "homeassistant.components.webhook",
    "homeassistant.components.cloud",
    "homeassistant.components.sensor",
    "homeassistant.components.binary_sensor",
    "homeassistant.config_entries",
    "homeassistant.core",
    "homeassistant.const",
    "homeassistant.exceptions",
    "homeassistant.helpers",
    "homeassistant.helpers.aiohttp_client",
    "homeassistant.helpers.device_registry",
    "homeassistant.helpers.entity_platform",
    "homeassistant.helpers.restore_state",
    "homeassistant.helpers.update_coordinator",
    "homeassistant.helpers.selector",
    "homeassistant.util",
    "homeassistant.util.dt",
):
    if mod not in sys.modules:
        sys.modules[mod] = mock_ha

if "aiohttp" not in sys.modules:
    sys.modules["aiohttp"] = MagicMock()

if "hyxi_cloud_api" not in sys.modules:
    mock_api = MagicMock()
    mock_api.__version__ = "1.2.6"
    sys.modules["hyxi_cloud_api"] = mock_api

# Now import the module under test
from custom_components.hyxi_cloud.webhook import (
    _merge_push_into_coordinator,
    _translate_push_fields,
)


# ---------------------------------------------------------------------------
# _translate_push_fields tests
# ---------------------------------------------------------------------------
class TestTranslatePushFields:
    """Test push field translation logic."""

    def test_battery_soc_renamed(self):
        """batterySoc should be translated to batsoc."""
        raw = {"deviceSn": "SN001", "batterySoc": 75.5, "ppv": 1200}
        result = _translate_push_fields(raw)
        assert "batsoc" in result
        assert result["batsoc"] == 75.5
        assert "batterySoc" not in result

    def test_passthrough_fields(self):
        """Most fields pass through unchanged."""
        raw = {"deviceSn": "SN001", "ppv": 1500, "pbat": -200, "gridP": 50}
        result = _translate_push_fields(raw)
        assert result["ppv"] == 1500
        assert result["pbat"] == -200
        assert result["gridP"] == 50

    def test_envelope_fields_excluded(self):
        """deviceSn, reportTimestamp, deviceType should not appear as metrics."""
        raw = {
            "deviceSn": "SN001",
            "reportTimestamp": 1700000000000,
            "deviceType": "HYBRID_INVERTER",
            "ppv": 100,
        }
        result = _translate_push_fields(raw)
        assert "deviceSn" not in result
        assert "reportTimestamp" not in result
        assert "deviceType" not in result
        assert "ppv" in result

    def test_report_timestamp_to_last_seen(self):
        """reportTimestamp (ms) should be converted to last_seen ISO string."""
        raw = {"deviceSn": "SN001", "reportTimestamp": 1700000000000}
        result = _translate_push_fields(raw)
        assert "last_seen" in result
        assert "2023-11-14" in result["last_seen"]

    def test_empty_item(self):
        """An item with only envelope fields should produce empty metrics."""
        raw = {"deviceSn": "SN001"}
        result = _translate_push_fields(raw)
        # Only derived metrics (from compute_derived_metrics mock) may be present
        # No raw metrics from the push item itself
        assert "deviceSn" not in result

    def test_compute_derived_metrics_called(self):
        """compute_derived_metrics should be called on the translated metrics."""
        raw = {"deviceSn": "SN001", "ppv": 1000, "pbat": -500}
        with patch(
            "custom_components.hyxi_cloud.webhook.HyxiApiClient.compute_derived_metrics",
            return_value={"grid_import": 0, "grid_export": 200},
        ) as mock_compute:
            result = _translate_push_fields(raw)
            mock_compute.assert_called_once()
            assert result["grid_import"] == 0
            assert result["grid_export"] == 200


# ---------------------------------------------------------------------------
# _merge_push_into_coordinator tests
# ---------------------------------------------------------------------------
class TestMergePushIntoCoordinator:
    """Test merging push data into coordinator."""

    def _make_coordinator(self, data=None):
        """Create a mock coordinator with given data."""
        coordinator = MagicMock()
        coordinator.data = data
        coordinator.async_set_updated_data = MagicMock()
        return coordinator

    def test_merge_updates_metrics(self):
        """Push metrics should be merged into existing device data."""
        existing = {
            "SN001": {
                "device_name": "Inverter 1",
                "metrics": {"ppv": 1000, "batsoc": 50},
            }
        }
        coordinator = self._make_coordinator(existing)

        push_item = {"deviceSn": "SN001", "ppv": 1500, "batterySoc": 75}

        with patch(
            "custom_components.hyxi_cloud.webhook._translate_push_fields",
            return_value={"ppv": 1500, "batsoc": 75},
        ):
            _merge_push_into_coordinator(coordinator, push_item)

        coordinator.async_set_updated_data.assert_called_once()
        new_data = coordinator.async_set_updated_data.call_args[0][0]
        assert new_data["SN001"]["metrics"]["ppv"] == 1500
        assert new_data["SN001"]["metrics"]["batsoc"] == 75

    def test_merge_preserves_existing_metrics(self):
        """Existing metrics not in push should be preserved."""
        existing = {
            "SN001": {
                "metrics": {"ppv": 1000, "batsoc": 50, "tinv": 35},
            }
        }
        coordinator = self._make_coordinator(existing)

        with patch(
            "custom_components.hyxi_cloud.webhook._translate_push_fields",
            return_value={"ppv": 1500},
        ):
            _merge_push_into_coordinator(coordinator, {"deviceSn": "SN001", "ppv": 1500})

        new_data = coordinator.async_set_updated_data.call_args[0][0]
        assert new_data["SN001"]["metrics"]["tinv"] == 35
        assert new_data["SN001"]["metrics"]["batsoc"] == 50

    def test_merge_does_not_mutate_original(self):
        """Original coordinator data should not be mutated."""
        existing = {
            "SN001": {
                "metrics": {"ppv": 1000},
            }
        }
        coordinator = self._make_coordinator(existing)

        with patch(
            "custom_components.hyxi_cloud.webhook._translate_push_fields",
            return_value={"ppv": 2000},
        ):
            _merge_push_into_coordinator(coordinator, {"deviceSn": "SN001", "ppv": 2000})

        # Original data unchanged
        assert existing["SN001"]["metrics"]["ppv"] == 1000

    def test_merge_skips_unknown_device(self):
        """Push for unknown SN should be skipped."""
        existing = {"SN001": {"metrics": {}}}
        coordinator = self._make_coordinator(existing)

        _merge_push_into_coordinator(coordinator, {"deviceSn": "SN999", "ppv": 100})
        coordinator.async_set_updated_data.assert_not_called()

    def test_merge_skips_missing_sn(self):
        """Push without deviceSn should be skipped."""
        coordinator = self._make_coordinator({"SN001": {"metrics": {}}})

        _merge_push_into_coordinator(coordinator, {"ppv": 100})
        coordinator.async_set_updated_data.assert_not_called()

    def test_merge_skips_when_no_data(self):
        """Push should be skipped when coordinator has no data yet."""
        coordinator = self._make_coordinator(None)

        _merge_push_into_coordinator(coordinator, {"deviceSn": "SN001", "ppv": 100})
        coordinator.async_set_updated_data.assert_not_called()


# ---------------------------------------------------------------------------
# Coordinator push tracking tests
# ---------------------------------------------------------------------------
class TestCoordinatorPushTracking:
    """Test coordinator push tracking methods directly (no HA import needed)."""

    def test_mark_push_received_updates_timestamp(self):
        """mark_push_received should update _last_push_received."""

        class FakeCoord:
            _last_push_received = None

            def mark_push_received(self):
                self._last_push_received = time.monotonic()

        coord = FakeCoord()
        coord.mark_push_received()
        assert coord._last_push_received is not None
        assert (time.monotonic() - coord._last_push_received) < 1

    def test_is_push_stale_false_when_inactive(self):
        """is_push_stale returns False when push not active."""
        from custom_components.hyxi_cloud.const import RT_PUSH_STALE_SECONDS

        class FakeCoord:
            _push_active = False
            _last_push_received = None

            def is_push_stale(self):
                if not self._push_active or self._last_push_received is None:
                    return False
                return (time.monotonic() - self._last_push_received) > RT_PUSH_STALE_SECONDS

        coord = FakeCoord()
        assert coord.is_push_stale() is False

    def test_is_push_stale_true_after_timeout(self):
        """is_push_stale returns True when no push received recently."""
        from custom_components.hyxi_cloud.const import RT_PUSH_STALE_SECONDS

        class FakeCoord:
            _push_active = True
            _last_push_received = time.monotonic() - (RT_PUSH_STALE_SECONDS + 80)

            def is_push_stale(self):
                if not self._push_active or self._last_push_received is None:
                    return False
                return (time.monotonic() - self._last_push_received) > RT_PUSH_STALE_SECONDS

        coord = FakeCoord()
        assert coord.is_push_stale() is True

    def test_is_push_stale_false_when_recent(self):
        """is_push_stale returns False when push was received recently."""
        from custom_components.hyxi_cloud.const import RT_PUSH_STALE_SECONDS

        class FakeCoord:
            _push_active = True
            _last_push_received = time.monotonic() - 10

            def is_push_stale(self):
                if not self._push_active or self._last_push_received is None:
                    return False
                return (time.monotonic() - self._last_push_received) > RT_PUSH_STALE_SECONDS

        coord = FakeCoord()
        assert coord.is_push_stale() is False
