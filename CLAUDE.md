# CLAUDE.md

This file guide Claude Code (claude.ai/code) for work in this repo.

## What this is

**Home Assistant custom integration** (`hyxi_cloud`) — polls/subscribes HYXIPower Cloud OpenAPI for solar inverters, batteries, meters, microinverters. Needs Python 3.14 + HA conventions. Ships via HACS. Cloud API itself live in separate PyPI package `hyxi-cloud-api` (`HyxiApiClient`) — this repo consume it, not implement HTTP/auth layer.

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

`dev_env/manage.sh` wrap Dockerized HA sandbox + lint/test targets above:
`./dev_env/manage.sh {start|stop|restart|ruff-check|ruff-format|ruff-fix|lint|test-integration|sync-git|reset-dev}`. `start` wipe `ha_testing_config`, seed from `ha_testing_seed` if present, boot HA at `localhost:8123`, and `pip install -e` sibling `hyxi-cloud-api` checkout for live API dev.

## Version sync (enforced)

`scripts/check_versions.py` run as pre-commit hook + CI. Integration version must match across `manifest.json`, `pyproject.toml`, `const.py` (`VERSION`); `hyxi-cloud-api` requirement must match across `manifest.json`, `pyproject.toml`, `requirements*.txt`. Bump version = edit all together.

## Architecture

All integration code under `custom_components/hyxi_cloud/` (bare filenames below relative to it). Standard HA coordinator integration. Platforms in `const.PLATFORMS`: `sensor`, `binary_sensor`, `number`, `switch`, `button`.

- **`__init__.py`** — `async_setup_entry` wires all: build `HyxiApiClient`, create coordinator, do first refresh, register webhook push subscriptions (telemetry + alarms), register devices in **two-pass** scheme (Pass 1 registers all SNs standalone, Pass 2 links `via_device` parent→child) so children never orphan. Also hold webhook receive handler with HMAC verification.
- **`coordinator.py`** — `HyxiDataUpdateCoordinator` (subclass `DataUpdateCoordinator`). `_async_update_data` pull all device data, **merge** fresh polled metrics into cached metrics so push-only keys survive, recompute derived metrics via `client.compute_derived_metrics`, sync sw/hw/model into device registry. Per-API health tracked in `self.hyxi_metadata`, push/alarm state in dedicated attrs — **not** in `coordinator.data`. `coordinator.data` is `{serial_number: device_dict}`.
- **`entity.py`** — `HyxiEntity(CoordinatorEntity)` base; all entities subclass it. `_attr_has_entity_name = True` always; names come from `translation_key` → `translations/*.json`, never hardcoded. `unique_id` = `f"{serial_number}_{key}"`.
- **`const.py`** — all `CONF_*` option keys, device-code → translation-key maps, masking/normalization helpers (`mask_sn` SHA-256, `mask_url`, `mask_sensitive_key_value`, `normalize_device_type`, `get_raw_device_code`, `detect_phase_type`, `is_null_value`). Logging must use these masks — serials, plant IDs, URLs, addresses scrubbed from debug logs.
- **`sensor.py`** (largest) — sensor descriptions + value parsing/glitch-filtering (spike/dip filters, `_last_valid_time` catchup).
- **`protection.py`** — `HyxiBatteryProtectionController`, battery SOC min/max threshold enforcement. Gated behind "Device Control & Protection" opt-in.
- **`engine.py`** — `EnergyManager` Standalone (Beta): 15-second local decision loop (charge/discharge/self-consume/idle) reading P1 meter, SOC, solar forecast. Priority-ordered ruleset documented in detail in README. Builds on protection (respects its SOC limits), gated behind Device Control + own opt-in + per-feature switches.
- **`config_flow.py`** — initial setup (access/secret key) and **options flow** that gates every advanced feature: polling interval, alarm discovery, device control+protection, real-time push, energy manager.

### Cross-cutting rules

- **All async.** Use `aiohttp` / `asyncio.sleep`, never `requests` / `time.sleep`. Lifecycle methods stay `async`.
- **No independent API calls in entities.** Read from `coordinator.data`; entities are `CoordinatorEntity`.
- **Device control opt-in + safety-gated.** Control entities appear only after user enables them; VPP dispatch locks them out unless explicitly overridden. Phase-type detection (`detect_phase_type`) decides which controls exist — if undetermined, no controls created on purpose.
- **Push + polling coexist.** Webhook push updates sensors instantly; polling loop still runs as heartbeat/fallback and fetches pull-only metrics. Don't assume one or other.
- New sensors need `device_class`, `state_class`, units, `translation_key` entry mirrored across all `translations/*.json` (`scripts/sync_translations.py` helps).

## Lint config notes

Ruff (`py314`, line-length 88) selects `E,W,F,I,UP,B,S,N,PTH,RUF`; `E501` (line length) + few naming rules ignored. `PTH` on — prefer `pathlib` over `os.path`. Tests relax `S101/S105/S106` etc. Custom pre-commit hook rejects parenthesized `except (A, B):`-style tuples written as legacy comma form. Codespell runs on commits.

## Git workflow

Branches: `main` (default, PR target) and `dev`. `manage.sh sync-git` / `reset-dev` automate keeping in sync. PRs go through CodeQL, Gitleaks, Bandit, pre-commit, test matrix.