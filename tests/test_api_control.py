"""Tests for the device control API methods in the vendored SDK."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

from custom_components.hyxi_cloud._vendor.hyxi_cloud_api.api import (
    HyxiApiClient,
    HyxiControlError,
    _PEAK_SHAVING_VALUES,
)


def _make_client():
    """Create a HyxiApiClient with a mocked session."""
    session = MagicMock()
    client = HyxiApiClient("test_ak", "test_sk", "https://open.hyxicloud.com", session)
    # Pre-set a valid token so _refresh_token is a no-op
    client.token = "Bearer test_token"
    client.token_expires_at = 9999999999.0
    return client, session


def _mock_response(status=200, body=None):
    """Create a mock aiohttp response context manager."""
    if body is None:
        body = {"success": True, "code": 0, "msg": "ok", "data": {}}
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=body)
    resp.raise_for_status = MagicMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestSetDeviceControl:
    """Tests for set_device_control."""

    def test_correct_url_and_body(self):
        """Verify the POST goes to /api/device/v2/control with correct body."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_device_control("SN123", {1062: ""})
        )

        session.post.assert_called_once()
        call_args = session.post.call_args
        url = call_args[0][0]
        assert url == "https://open.hyxicloud.com/api/device/v2/control"

        # Verify body
        sent_data = json.loads(call_args[1]["data"])
        assert sent_data == {
            "deviceControlMap": {"SN123": {"1062": ""}}
        }

    def test_raises_on_failure(self):
        """Verify HyxiControlError is raised when API returns failure."""
        client, session = _make_client()
        mock_resp = _mock_response(
            body={"success": False, "code": "C000001", "msg": "Parameter error"}
        )
        session.post = MagicMock(return_value=mock_resp)

        with pytest.raises(HyxiControlError, match="controlMap write failed"):
            asyncio.run(
                client.set_device_control("SN123", {1062: ""})
            )


class TestModeWrappers:
    """Tests for the mode convenience wrappers."""

    def test_set_mode_idle(self):
        """Idle sends controlId 1062 with empty string."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_mode_idle("SN001")
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1062": ""}

    def test_set_mode_charge(self):
        """Charge sends controlId 1063 with wattage string."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_mode_charge("SN001", 500)
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1063": "500"}

    def test_set_mode_discharge(self):
        """Discharge sends controlId 1064 with wattage string."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_mode_discharge("SN001", 300)
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1064": "300"}

    def test_set_mode_self_consume(self):
        """Self-consumption sends controlId 1065 with empty string."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_mode_self_consume("SN001")
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1065": ""}


class TestPeakShaving:
    """Tests for set_peak_shaving."""

    @pytest.mark.parametrize(
        "action,expected_value",
        [
            ("close", "0"),
            ("charge", "1"),
            ("discharge", "2"),
            ("stop", "3"),
            ("hold", "4"),
        ],
    )
    def test_peak_shaving_values(self, action, expected_value):
        """Each peak shaving action maps to the correct value."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_peak_shaving("SN001", action)
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1021": expected_value}

    def test_invalid_action_raises(self):
        """Invalid peak shaving action raises ValueError."""
        client, session = _make_client()

        with pytest.raises(ValueError, match="Invalid peak shaving action"):
            asyncio.run(
                client.set_peak_shaving("SN001", "invalid")
            )


class TestFrequencyControl:
    """Tests for set_frequency_control."""

    def test_enable(self):
        """Enable sends controlId 1020 with value '1'."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_frequency_control("SN001", enabled=True)
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1020": "1"}

    def test_disable(self):
        """Disable sends controlId 1020 with value '0'."""
        client, session = _make_client()
        mock_resp = _mock_response()
        session.post = MagicMock(return_value=mock_resp)

        asyncio.run(
            client.set_frequency_control("SN001", enabled=False)
        )

        sent_data = json.loads(session.post.call_args[1]["data"])
        assert sent_data["deviceControlMap"]["SN001"] == {"1020": "0"}
