"""Tests for Energy Manager entity setup and definitions."""

import json
from pathlib import Path

import pytest

from custom_components.hyxi_cloud.const import (
    CONF_EM_ENABLED,
    CONF_EM_INVERTER_SN,
    CONF_EM_P1_ENTITY,
    EM_DEFAULTS,
)


# ═══════════════════════════════════════════════════════════════════════
# EM Constants
# ═══════════════════════════════════════════════════════════════════════


class TestEMConstants:
    """Test EM constant definitions."""

    def test_em_defaults_has_all_required_keys(self):
        """EM_DEFAULTS should have all 16 parameter keys."""
        required_keys = [
            "soc_min", "soc_max", "night_buffer_pct", "high_load_threshold",
            "battery_capacity_wh", "max_charge_power", "max_discharge_power",
            "min_solar_for_charge", "mode_switch_cooldown", "power_change_threshold",
            "power_adjust_cooldown", "avg_night_consumption", "charge_margin",
            "charge_entry_threshold", "charge_reentry_delay", "bottomout_cooldown",
        ]
        for key in required_keys:
            assert key in EM_DEFAULTS, f"Missing EM_DEFAULTS key: {key}"

    def test_em_defaults_values_are_reasonable(self):
        """EM_DEFAULTS values should be within sane ranges."""
        assert 5 <= EM_DEFAULTS["soc_min"] <= 50
        assert 50 <= EM_DEFAULTS["soc_max"] <= 100
        assert EM_DEFAULTS["soc_min"] < EM_DEFAULTS["soc_max"]
        assert EM_DEFAULTS["battery_capacity_wh"] > 0
        assert EM_DEFAULTS["max_charge_power"] > 0
        assert EM_DEFAULTS["max_discharge_power"] > 0
        assert EM_DEFAULTS["mode_switch_cooldown"] >= 10
        assert EM_DEFAULTS["night_buffer_pct"] >= 0

    def test_conf_keys_are_strings(self):
        """Config option keys should be non-empty strings."""
        assert isinstance(CONF_EM_ENABLED, str) and CONF_EM_ENABLED
        assert isinstance(CONF_EM_INVERTER_SN, str) and CONF_EM_INVERTER_SN
        assert isinstance(CONF_EM_P1_ENTITY, str) and CONF_EM_P1_ENTITY


# ═══════════════════════════════════════════════════════════════════════
# EM Number Definitions
# ═══════════════════════════════════════════════════════════════════════


class TestEMNumberDefinitions:
    """Test EM number entity definitions."""

    def test_em_number_defs_match_defaults(self):
        """Every EM number def key should have a matching EM_DEFAULTS entry."""
        from custom_components.hyxi_cloud.number import EM_NUMBER_DEFS

        for numdef in EM_NUMBER_DEFS:
            assert numdef.key in EM_DEFAULTS, f"EM_NUMBER_DEFS key '{numdef.key}' not in EM_DEFAULTS"

    def test_em_number_defs_ranges_valid(self):
        """Min should be less than max, step should be positive."""
        from custom_components.hyxi_cloud.number import EM_NUMBER_DEFS

        for numdef in EM_NUMBER_DEFS:
            assert numdef.min_val < numdef.max_val, f"Invalid range for '{numdef.key}': {numdef.min_val} >= {numdef.max_val}"
            assert numdef.step > 0, f"Invalid step for '{numdef.key}': {numdef.step}"
            default = EM_DEFAULTS[numdef.key]
            assert numdef.min_val <= default <= numdef.max_val, (
                f"Default {default} outside range [{numdef.min_val}, {numdef.max_val}] for '{numdef.key}'"
            )

    def test_em_number_defs_have_icons(self):
        """Every EM number should have an mdi icon."""
        from custom_components.hyxi_cloud.number import EM_NUMBER_DEFS

        for numdef in EM_NUMBER_DEFS:
            assert numdef.icon.startswith("mdi:"), f"Invalid icon for '{numdef.key}': {numdef.icon}"


# ═══════════════════════════════════════════════════════════════════════
# Translation Coverage for EM Entities
# ═══════════════════════════════════════════════════════════════════════


class TestEMTranslationCoverage:
    """Verify all EM entities have translations."""

    @staticmethod
    def _load_strings():
        path = Path(__file__).parent / "../custom_components/hyxi_cloud/strings.json"
        with path.open(encoding="utf-8") as f:
            return json.load(f)

    def test_em_number_keys_in_strings(self):
        """Every EM number translation_key should exist in strings.json."""
        from custom_components.hyxi_cloud.number import EM_NUMBER_DEFS

        strings = self._load_strings()
        number_translations = strings.get("entity", {}).get("number", {})

        # EM numbers use translation_key = f"em_{key}"
        for numdef in EM_NUMBER_DEFS:
            key = numdef.key
            tk = f"em_{key}"
            assert tk in number_translations, (
                f"Number translation key '{tk}' missing from strings.json"
            )

        # Always-on SOC numbers
        assert "em_soc_min" in number_translations
        assert "em_soc_max" in number_translations

    def test_em_switch_keys_in_strings(self):
        """EM switch translation keys should exist in strings.json."""
        strings = self._load_strings()
        switch_translations = strings.get("entity", {}).get("switch", {})

        for key in ("em_enabled", "em_grid_charge_allowed", "em_high_load_battery_assist"):
            assert key in switch_translations, (
                f"Switch translation key '{key}' missing from strings.json"
            )

    def test_em_sensor_keys_in_strings(self):
        """EM sensor translation keys should exist in strings.json."""
        strings = self._load_strings()
        sensor_translations = strings.get("entity", {}).get("sensor", {})

        em_keys = [
            "em_current_decision", "em_last_action",
            "em_battery_energy_available", "em_hours_until_sunrise",
            "em_hours_until_sunset", "em_p1_average",
        ]
        for key in em_keys:
            assert key in sensor_translations, (
                f"Sensor translation key '{key}' missing from strings.json"
            )

    def test_em_binary_sensor_keys_in_strings(self):
        """EM binary sensor translation keys should exist in strings.json."""
        strings = self._load_strings()
        bs_translations = strings.get("entity", {}).get("binary_sensor", {})

        for key in ("em_night_mode_active", "em_high_load_detected"):
            assert key in bs_translations, (
                f"Binary sensor translation key '{key}' missing from strings.json"
            )

    def test_em_options_step_in_strings(self):
        """Energy manager options step should exist in strings.json."""
        strings = self._load_strings()
        options_steps = strings.get("options", {}).get("step", {})
        assert "energy_manager" in options_steps, (
            "Missing 'energy_manager' options step in strings.json"
        )
        em_step = options_steps["energy_manager"]
        assert "data" in em_step
        assert "em_inverter_sn" in em_step["data"]
        assert "em_p1_entity" in em_step["data"]


# ═══════════════════════════════════════════════════════════════════════
# Config Flow EM Step
# ═══════════════════════════════════════════════════════════════════════


class TestConfigFlowEM:
    """Test the config flow EM options step data keys."""

    def test_em_enabled_in_init_step_data(self):
        """The init step should include enable_energy_manager in its data translations."""
        strings = TestEMTranslationCoverage._load_strings()
        init_data = strings["options"]["step"]["init"]["data"]
        assert "enable_energy_manager" in init_data
