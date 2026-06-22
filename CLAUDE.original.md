# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **Home Assistant custom integration** (`hyxi_cloud`) that polls/subscribes to the HYXIPower Cloud OpenAPI for solar inverters, batteries, meters, and microinverters. Requires Python 3.14 and HA conventions. Distributed via HACS. The cloud API itself lives in a separate PyPI package, `hyxi-cloud-api` (`HyxiApiClient`) — this repo consumes it, it does not implement the HTTP/auth layer.

## Commands

Run from repo root unless noted.

```bash
# Unit tests (asyncio_mode=auto; integration tests excluded by default via addopts)
pytest tests/ --ignore=tests/integration -v -p no:warnings
pytest tests/test_engine.py -v                 # single file
pytest tests/test_engine.py::test_name -v      # single test

# Integration tests (gated by env var; otherwise skipped)
HYXI_INTEGRATION_TEST="1" pytest tests/integration -v -p no:warnings

# Lint / format / typecheck
ruff check . --fix
ruff format .
mypy --config-file mypy.ini      # checks packages hyxi_cloud,tests
pre-commit run --all-files       # full audit: ruff, codespell, mypy, gitleaks, pylint, shellcheck, version sync
```

`dev_env/manage.sh` wraps a Dockerized HA sandbox + the above lint/test targets:
`./dev_env/manage.sh {start|stop|restart|ruff-check|ruff-fix|lint|test-integration}`. `start` wipes `ha_testing_config`, seeds from `ha_testing_seed` if present, boots HA at `localhost:8123`, and `pip install -e`'s a sibling `hyxi-cloud-api` checkout for live API dev.

## Version sync (enforced)

`scripts/check_versions.py` runs as a pre-commit hook and CI. The integration version must match across `manifest.json`, `pyproject.toml`, and `const.py` (`VERSION`); the `hyxi-cloud-api` requirement must match across `manifest.json`, `pyproject.toml`, and `requirements*.txt`. Bumping a version means editing all of these together.

## Architecture

Standard HA coordinator integration. Platforms registered in `const.PLATFORMS`: `sensor`, `binary_sensor`, `number`, `switch`, `button`.

- **`__init__.py`** — `async_setup_entry` wires everything: builds `HyxiApiClient`, creates the coordinator, does first refresh, registers webhook push subscriptions (telemetry + alarms), and registers devices in a **two-pass** scheme (Pass 1 registers all SNs standalone, Pass 2 links `via_device` parent→child) so children never orphan. Also holds the webhook receive handler with HMAC verification.
- **`coordinator.py`** — `HyxiDataUpdateCoordinator` (subclass of `DataUpdateCoordinator`). `_async_update_data` pulls all device data, **merges** freshly polled metrics into cached metrics so push-only keys survive, recomputes derived metrics via `client.compute_derived_metrics`, and syncs sw/hw/model into the device registry. Per-API health is tracked in `self.hyxi_metadata` and push/alarm state in dedicated attrs — **not** in `coordinator.data`. `coordinator.data` is `{serial_number: device_dict}`.
- **`entity.py`** — `HyxiEntity(CoordinatorEntity)` base; all entities subclass it. `_attr_has_entity_name = True` always; names come from `translation_key` → `translations/*.json`, never hardcoded. `unique_id` = `f"{serial_number}_{key}"`.
- **`const.py`** — all `CONF_*` option keys, device-code → translation-key maps, and the masking/normalization helpers (`mask_sn` SHA-256, `mask_url`, `mask_sensitive_key_value`, `normalize_device_type`, `get_raw_device_code`, `detect_phase_type`, `is_null_value`). Logging must use these masks — serials, plant IDs, URLs, and addresses are scrubbed from debug logs.
- **`sensor.py`** (largest) — sensor descriptions + value parsing/glitch-filtering (spike/dip filters, `_last_valid_time` catchup).
- **`protection.py`** — `HyxiBatteryProtectionController`, battery SOC min/max threshold enforcement. Gated behind the "Device Control & Protection" opt-in.
- **`engine.py`** — `EnergyManager` Standalone (Beta): a 15-second local decision loop (charge/discharge/self-consume/idle) reading P1 meter, SOC, solar forecast. Priority-ordered ruleset documented in detail in README. Builds on top of protection (respects its SOC limits), gated behind Device Control + its own opt-in + per-feature switches.
- **`config_flow.py`** — initial setup (access/secret key) and the **options flow** that gates every advanced feature: polling interval, alarm discovery, device control+protection, real-time push, energy manager.

### Cross-cutting rules

- **Everything is async.** Use `aiohttp` / `asyncio.sleep`, never `requests` / `time.sleep`. Lifecycle methods stay `async`.
- **No independent API calls in entities.** Read from `coordinator.data`; entities are `CoordinatorEntity`.
- **Device control is opt-in and safety-gated.** Control entities only appear after the user enables them; VPP dispatch locks them out unless explicitly overridden. Phase-type detection (`detect_phase_type`) decides which controls exist — if undetermined, no controls are created on purpose.
- **Push + polling coexist.** Webhook push updates sensors instantly; the polling loop still runs as heartbeat/fallback and to fetch pull-only metrics. Don't assume one or the other.
- New sensors need `device_class`, `state_class`, units, and a `translation_key` entry mirrored across all `translations/*.json` (`scripts/sync_translations.py` helps).

## Lint config notes

Ruff (`py314`, line-length 88) selects `E,W,F,I,UP,B,S,N,PTH,RUF`; `E501` (line length) and a few naming rules are ignored. `PTH` is on — prefer `pathlib` over `os.path`. Tests relax `S101/S105/S106` etc. A custom pre-commit hook rejects parenthesized `except (A, B):`-style tuples written as the legacy comma form. Codespell runs on commits.

## Git workflow

Branches: `main` (default, PR target) and `dev`. `manage.sh sync-git` / `reset-dev` automate keeping them in sync. PRs go through CodeQL, Gitleaks, Bandit, pre-commit, and the test matrix.
