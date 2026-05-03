# Changes — `feature/control-write`

## Summary

Adds write/control support to the HYXI Cloud integration for Home Assistant.
The integration was previously read-only (sensors and binary sensors). After
this change, users can control their inverter's operating mode, peak shaving,
and frequency control directly from the Home Assistant UI.

## Verified API Endpoint

- **Control endpoint:** `POST /api/device/v2/control`
- **Request body:** `{"deviceControlMap": {"<deviceSn>": {"<controlId>": <value>}}}`
- **Response:** `{"success": true/false, "code": ..., "msg": ..., "traceId": ..., "deviceSn": ...}`
- **Source:** Scraped from the HYXI Open API developer documentation at
  `https://open.hyxicloud.com/#/document` → "Issue Device Control Instructions"
- **Scraped doc dump:** `docs/hyxi-api-doc.json`

## New Entities

| Platform | Entity | Device Class | Description |
|----------|--------|-------------|-------------|
| `select` | Operating Mode | — | Idle / Charge / Discharge / Self-Consumption (controlIds 1062–1065) |
| `select` | Peak Shaving | — | Close / Charge / Discharge / Stop / Hold (controlId 1021) |
| `switch` | Frequency Control | — | Enable / Disable (controlId 1020) |
| `number` | Charge Power | power (W) | Wattage for Charge mode (0–max, step 100) |
| `number` | Discharge Power | power (W) | Wattage for Discharge mode (0–max, step 100) |

## Migration Note

The `hyxi-cloud-api` PyPI package (`==1.1.5`) is no longer required as an
external dependency. The SDK has been vendored into
`custom_components/hyxi_cloud/_vendor/hyxi_cloud_api/` and extended with the
`set_device_control()` method and convenience wrappers. The `manifest.json`
`requirements` field has been updated accordingly — only `aiohttp` remains.

Users upgrading from a previous version should remove the `hyxi-cloud-api`
package from their Python environment if it was manually installed.

## Known Limitations

1. **No API field for current mode:** The HYXI polling API does not return the
   inverter's current operating mode, peak shaving state, or frequency control
   status. These entities track state internally after successful writes.
   After a Home Assistant restart, the state will show as "unknown" until the
   user makes a new selection.

2. **Wattage upper bound:** The charge/discharge power number entities default
   to a maximum of 10,000W. If the device's `maxChargePower` or
   `maxDischargePower` fields are available from the device info API, those
   values are used instead.

3. **Single-phase vs three-phase:** Peak Shaving (1021) and Frequency Control
   (1020) are documented as single-phase controls. Mode Control (1062–1065)
   is documented as three-phase. Both are exposed for hybrid inverters; the
   API will reject commands that are incompatible with the device's phase
   configuration.
