"""Real-time push webhook for HYXI Cloud integration."""

import copy
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

from aiohttp import web
from homeassistant.components import webhook
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from hyxi_cloud_api import HyxiApiClient

from .const import (
    CONF_ACCESS_KEY,
    CONF_RT_ENABLED,
    CONF_RT_EXTERNAL_URL,
    CONF_RT_PUSH_RATE_MS,
    CONF_RT_SUBSCRIBE_CODE,
    CONF_RT_URL_MODE,
    CONF_RT_WEBHOOK_ID,
    DOMAIN,
    PUSH_TO_METRICS_MAP,
    RT_PUSH_RATE_MS_DEFAULT,
    RT_URL_MODE_MANUAL,
    RT_URL_MODE_NABU_CASA,
    get_raw_device_code,
    normalize_device_type,
)
from .coordinator import HyxiDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

WEBHOOK_NAME = "HYXI Cloud Real-Time Push"


async def async_setup_webhook(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Set up webhook for real-time push subscription.

    Returns an async cleanup function to be called on unload.
    """
    webhook_id = entry.options.get(CONF_RT_WEBHOOK_ID)
    if not webhook_id:
        webhook_id = webhook.async_generate_id()
        _async_update_entry_options(hass, entry, {CONF_RT_WEBHOOK_ID: webhook_id})

    callback_url = _resolve_callback_url(hass, entry, webhook_id)
    if not callback_url:
        _LOGGER.error("Cannot determine callback URL for real-time push")
        return _noop_cleanup

    # Register HA webhook handler
    webhook.async_register(
        hass,
        DOMAIN,
        WEBHOOK_NAME,
        webhook_id,
        _make_webhook_handler(hass, entry, coordinator),
    )

    # Cancel previous subscription if any
    old_code = entry.options.get(CONF_RT_SUBSCRIBE_CODE)
    if old_code:
        try:
            await coordinator.client.cancel_subscription(old_code)
            _LOGGER.debug("Cancelled previous RT subscription %s", old_code)
        except Exception:
            _LOGGER.debug("Old subscription %s already expired or invalid", old_code)

    # Subscribe to real-time data
    device_sns = list(coordinator.data.keys())
    push_rate = entry.options.get(CONF_RT_PUSH_RATE_MS, RT_PUSH_RATE_MS_DEFAULT)

    subscribe_code = None
    try:
        result = await coordinator.client.subscribe_real_time_data(
            callback_url, device_sns, push_rate
        )
        subscribe_code = result.get("data", {}).get("subscribeCode")
        if subscribe_code:
            _async_update_entry_options(
                hass, entry, {CONF_RT_SUBSCRIBE_CODE: subscribe_code}
            )
            _LOGGER.info(
                "HYXI real-time push active: %s devices, %sms interval, webhook %s",
                len(device_sns),
                push_rate,
                webhook_id[:8],
            )
        else:
            _LOGGER.warning("Subscribe succeeded but no subscribeCode in response")
    except Exception as err:
        _LOGGER.error("Failed to subscribe to real-time push: %s", err)
        webhook.async_unregister(hass, webhook_id)
        return _noop_cleanup

    async def _async_cleanup() -> None:
        """Cancel subscription and unregister webhook."""
        webhook.async_unregister(hass, webhook_id)
        if subscribe_code:
            try:
                await coordinator.client.cancel_subscription(subscribe_code)
                _LOGGER.debug("Cancelled RT subscription on unload")
            except Exception:
                _LOGGER.debug("Could not cancel RT subscription on unload (may be expired)")

    coordinator.mark_push_active(True)
    return _async_cleanup


def _resolve_callback_url(
    hass: HomeAssistant, entry: ConfigEntry, webhook_id: str
) -> str | None:
    """Determine the public callback URL based on user config."""
    url_mode = entry.options.get(CONF_RT_URL_MODE, RT_URL_MODE_MANUAL)

    if url_mode == RT_URL_MODE_NABU_CASA:
        try:
            from homeassistant.components.cloud import async_remote_ui_url

            base = async_remote_ui_url(hass)
            return f"{base}/api/webhook/{webhook_id}"
        except Exception:
            _LOGGER.error("Nabu Casa URL not available — is cloud connected?")
            return None

    if url_mode == RT_URL_MODE_MANUAL:
        external_url = entry.options.get(CONF_RT_EXTERNAL_URL, "").rstrip("/")
        if external_url:
            return f"{external_url}/api/webhook/{webhook_id}"
        _LOGGER.error("Manual URL mode selected but no external URL configured")
        return None

    return None


def _make_webhook_handler(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: HyxiDataUpdateCoordinator,
):
    """Create webhook handler closure bound to entry/coordinator."""

    async def _handle(
        hass: HomeAssistant, webhook_id: str, request: web.Request
    ) -> web.Response:
        """Handle incoming push data from HYXI Cloud."""
        # Verify access key header
        expected_key = entry.data.get(CONF_ACCESS_KEY)
        request_key = request.headers.get("accessKey")
        if not request_key or request_key != expected_key:
            _LOGGER.warning("RT push rejected: invalid accessKey header")
            return web.Response(status=401, text="Unauthorized")

        try:
            payload = await request.json()
        except Exception:
            _LOGGER.warning("RT push rejected: invalid JSON body")
            return web.Response(status=400, text="Bad Request")

        data_list = payload.get("dataList")
        if not isinstance(data_list, list):
            _LOGGER.warning("RT push rejected: missing or invalid dataList")
            return web.Response(status=400, text="Bad Request")

        _LOGGER.debug("RT push received: %d device(s)", len(data_list))

        for item in data_list:
            _merge_push_into_coordinator(coordinator, item)

        coordinator.mark_push_received()
        return web.Response(status=200, text="OK")

    return _handle


def _translate_push_fields(raw_item: dict) -> dict:
    """Translate push field names to coordinator metrics keys.

    - Map batterySoc -> batsoc (and any future renames in PUSH_TO_METRICS_MAP)
    - Convert reportTimestamp to last_seen ISO string
    - Compute derived metrics (grid_import/export, bat_charging/discharging)
    """
    metrics: dict[str, Any] = {}

    for key, value in raw_item.items():
        # Skip non-metric envelope fields
        if key in ("deviceSn", "reportTimestamp", "deviceType"):
            continue
        translated = PUSH_TO_METRICS_MAP.get(key, key)
        metrics[translated] = value

    # Convert reportTimestamp to last_seen
    ts = raw_item.get("reportTimestamp")
    if ts is not None:
        try:
            metrics["last_seen"] = datetime.fromtimestamp(
                int(ts) / 1000
            ).isoformat()
        except (ValueError, TypeError, OSError):
            pass

    # Compute derived metrics using API library
    device_type = normalize_device_type(
        raw_item.get("deviceType") or raw_item.get("deviceSn", "")
    )
    derived = HyxiApiClient.compute_derived_metrics(metrics, device_type)
    metrics.update(derived)

    return metrics


def _merge_push_into_coordinator(
    coordinator: HyxiDataUpdateCoordinator, push_item: dict
) -> None:
    """Merge a single push item into coordinator data and notify entities."""
    sn = push_item.get("deviceSn")
    if not sn:
        _LOGGER.debug("RT push item missing deviceSn, skipping")
        return

    current_data = coordinator.data
    if current_data is None:
        _LOGGER.debug("Coordinator has no data yet, skipping push merge")
        return

    if sn not in current_data:
        _LOGGER.debug("RT push for unknown device %s, skipping", sn)
        return

    metrics = _translate_push_fields(push_item)
    if not metrics:
        return

    # Deep copy to avoid mutating the existing data in-place
    new_data = copy.deepcopy(current_data)
    device_data = new_data[sn]

    # Merge translated metrics into device's metrics dict
    existing_metrics = device_data.get("metrics", {})
    existing_metrics.update(metrics)
    device_data["metrics"] = existing_metrics

    # Update coordinator data — triggers all entity listeners
    coordinator.async_set_updated_data(new_data)


def _async_update_entry_options(
    hass: HomeAssistant, entry: ConfigEntry, updates: dict
) -> None:
    """Merge new keys into entry.options without triggering a full reload."""
    new_options = {**entry.options, **updates}
    hass.config_entries.async_update_entry(entry, options=new_options)


async def _noop_cleanup() -> None:
    """No-op cleanup when setup failed."""
