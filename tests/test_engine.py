"""Tests for the Energy Manager decision engine.

The real engine.py imports HA modules that are hard to mock at import time.
Instead of fighting the import system, we embed the decision logic directly
in FakeEngine — it's a 1:1 copy of _make_decision from engine.py.  This
tests the priority logic, cooldowns, and state transitions without HA.

If engine.py _make_decision changes, update the copy here.
"""

import logging
import time
from collections import deque

import pytest

_LOGGER = logging.getLogger(__name__)


# ── Helpers to build a testable engine without real HA ──────────────────


class FakeEngine:
    """Stripped-down engine that replicates decision logic without HA imports."""

    def __init__(
        self,
        soc=50.0,
        solar=0.0,
        home_load=0.0,
        p1=0.0,
        is_night=False,
        current_mode=None,
        soc_min=20.0,
        soc_max=90.0,
        max_charge=5000.0,
        max_discharge=5000.0,
        grid_charge_allowed=False,
        high_load_assist=True,
    ):
        self.soc = soc
        self.solar = solar
        self.home_load = home_load
        self.p1 = p1
        self._is_night_val = is_night
        self.soc_min = soc_min
        self.soc_max = soc_max
        self.max_charge = max_charge
        self.max_discharge = max_discharge
        self.grid_charge_allowed = grid_charge_allowed
        self.high_load_assist = high_load_assist

        # Engine state
        self._sn = "TEST_SN"
        self._current_mode = current_mode
        self._last_decision = ""
        self._last_action = ""
        self._last_mode_switch = 0
        self._last_power_adjust = 0
        self._last_charge_exit = 0
        self._last_bottomout_exit = 0
        self._charge_entry_export_count = 0
        self._charge_bottomout_count = 0
        self._p1_buffer = deque()

        # Track API calls
        self.mode_calls = []
        self.adjust_calls = []

        # Default params
        self.params = {
            "soc_min": soc_min,
            "soc_max": soc_max,
            "max_charge_power": max_charge,
            "max_discharge_power": max_discharge,
            "high_load_threshold": 6500,
            "battery_capacity_wh": 14800,
            "mode_switch_cooldown": 60,
            "power_change_threshold": 100,
            "power_adjust_cooldown": 30,
            "avg_night_consumption": 400,
            "night_buffer_pct": 5,
            "min_solar_for_charge": 1000,
            "charge_margin": 150,
            "charge_entry_threshold": 500,
            "charge_reentry_delay": 300,
            "bottomout_cooldown": 300,
        }

    def _get_soc(self):
        return self.soc

    def _get_solar(self):
        return self.solar

    def _get_home_load(self):
        return self.home_load

    def _get_p1(self):
        return self.p1

    def _is_night(self):
        return self._is_night_val

    def _get_param(self, key):
        return float(self.params.get(key, 0))

    def _soc_needed_for_night(self):
        wh_needed = self._get_param("avg_night_consumption") * 12 * 1.05
        capacity = self._get_param("battery_capacity_wh")
        soc_min = self._get_param("soc_min")
        if capacity <= 0:
            capacity = 10000
        return soc_min + (wh_needed / capacity) * 100

    def _solar_will_cover_charge(self, target_soc):
        return False

    def _hours_until_sunset(self):
        return 12.0

    @property
    def p1_avg(self):
        if not self._p1_buffer:
            return 0.0
        return sum(v for _, v in self._p1_buffer) / len(self._p1_buffer)

    def _get_current_power_setting(self, direction):
        return 0.0

    def _find_entity_id(self, domain, unique_id):
        return unique_id  # Return the unique_id as the entity_id for matching

    def _get_ha_state_bool(self, entity_id, default=False):
        if "grid_charge_allowed" in str(entity_id or ""):
            return self.grid_charge_allowed
        if "high_load_battery_assist" in str(entity_id or ""):
            return self.high_load_assist
        return default

    def _set_decision(self, decision):
        self._last_decision = decision

    async def _set_mode(self, mode, power_w=None):
        self.mode_calls.append((mode, power_w))
        self._current_mode = mode
        self._last_mode_switch = time.monotonic()
        if power_w:
            self._last_action = f"{mode} @ {power_w}W"
        else:
            self._last_action = mode
        return True

    async def _adjust_power(self, direction, target_w):
        self.adjust_calls.append((direction, target_w))
        self._last_power_adjust = time.monotonic()
        return True

    def _notify_sensors(self):
        pass

    # ── Decision logic — 1:1 copy from engine.py _make_decision ────────
    # Keep in sync with custom_components/hyxi_cloud/engine.py

    async def _make_decision(self) -> None:
        soc = self._get_soc()
        solar = self._get_solar()
        p1 = self._get_p1()

        soc_min = self._get_param("soc_min")
        soc_max = self._get_param("soc_max")
        max_charge = self._get_param("max_charge_power")
        max_discharge = self._get_param("max_discharge_power")

        is_night = self._is_night()
        solar_producing = solar > 50
        night_soc_target = self._soc_needed_for_night()
        home_load = self._get_home_load()

        # PRIORITY 1: Emergency — SOC below minimum
        if soc < soc_min:
            if solar_producing:
                charge_target = min(solar - 50, max_charge)
                charge_target = max(charge_target, 300)
                self._set_decision("emergency_solar_charge")
                if self._current_mode != "charge":
                    await self._set_mode("charge", int(charge_target))
                else:
                    await self._adjust_power("charge", int(charge_target))
                return

            grid_charge_uid = f"hyxi_{self._sn}_em_grid_charge_allowed"
            grid_entity = self._find_entity_id("switch", grid_charge_uid)
            if self._get_ha_state_bool(grid_entity):
                grid_charge_w = min(2000, int(max_charge))
                self._set_decision("grid_charge_emergency")
                if self._current_mode != "charge":
                    await self._set_mode("charge", grid_charge_w)
                else:
                    await self._adjust_power("charge", grid_charge_w)
                return
            else:
                self._set_decision("low_soc_idle")
                if self._current_mode != "idle":
                    await self._set_mode("idle")
                return

        # PRIORITY 2: SOC above maximum — force discharge
        if soc > soc_max:
            discharge_w = max(p1, 1000)
            discharge_w = min(discharge_w, max_discharge)
            self._set_decision("forced_discharge_over_max")
            if self._current_mode != "discharge":
                await self._set_mode("discharge", int(discharge_w))
            else:
                await self._adjust_power("discharge", int(discharge_w))
            return

        # PRIORITY 3: Sustained high load
        high_load_threshold = self._get_param("high_load_threshold")

        high_load_assist_uid = f"hyxi_{self._sn}_em_high_load_battery_assist"
        high_load_entity = self._find_entity_id("switch", high_load_assist_uid)
        high_load_assist = self._get_ha_state_bool(high_load_entity, True)

        if home_load > high_load_threshold and high_load_assist:
            high_load_wh = max_discharge * 0.5
            capacity = self._get_param("battery_capacity_wh")
            soc_cost = (high_load_wh / capacity) * 100 if capacity > 0 else 100

            if (soc - soc_cost) > night_soc_target:
                self._set_decision("high_load_battery_assist")
                if self._current_mode != "self_consume":
                    await self._set_mode("self_consume")
            else:
                self._set_decision("high_load_grid_only")
                if self._current_mode != "idle":
                    await self._set_mode("idle")
            return

        # PRIORITY 4: Night
        if not solar_producing and is_night:
            if soc > soc_min:
                self._set_decision("night_self_consume")
                if self._current_mode != "self_consume":
                    await self._set_mode("self_consume")
            else:
                self._set_decision("night_reserve_hold")
                if self._current_mode != "idle":
                    await self._set_mode("idle")
            return

        # PRIORITY 4b: Night battery preservation during daytime
        p1_avg = self.p1_avg
        if (not is_night and soc <= night_soc_target
                and p1 > 0 and p1_avg > 0
                and not self._solar_will_cover_charge(night_soc_target)):
            self._set_decision("night_preserve_idle")
            if self._current_mode != "idle":
                await self._set_mode("idle")
            return

        # PRIORITY 5: Solar active
        min_solar_for_charge = self._get_param("min_solar_for_charge")
        charge_margin = self._get_param("charge_margin")
        charge_entry_threshold = self._get_param("charge_entry_threshold")
        charge_reentry_delay = self._get_param("charge_reentry_delay")
        readings_needed = max(int(charge_reentry_delay / 15 / 3), 2)

        bottomout_cooldown = self._get_param("bottomout_cooldown")
        if (time.monotonic() - self._last_bottomout_exit) < bottomout_cooldown:
            readings_needed = readings_needed * 2

        hours_to_sunset = self._hours_until_sunset()
        sunset_urgent = False
        if solar_producing and hours_to_sunset < 4 and soc < night_soc_target:
            if not self._solar_will_cover_charge(night_soc_target):
                sunset_urgent = True
                charge_entry_threshold = max(charge_entry_threshold // 2, 100)
                readings_needed = max(readings_needed // 2, 1)
                min_solar_for_charge = max(min_solar_for_charge - 300, 200)

        if solar_producing and soc < soc_max:
            if self._current_mode != "charge":
                if solar < min_solar_for_charge:
                    self._set_decision("solar_self_consume")
                    if self._current_mode not in ("self_consume", "idle"):
                        await self._set_mode("self_consume")
                    self._charge_entry_export_count = 0

                elif p1 < -charge_entry_threshold:
                    self._charge_entry_export_count += 1
                    if (self._charge_entry_export_count >= readings_needed
                            and solar >= min_solar_for_charge):
                        charge_target = min(abs(p1) - charge_margin - 100, solar - 500)
                        charge_target = min(charge_target, max_charge)
                        charge_target = max(charge_target, 300)
                        decision = "pre_night_charge" if sunset_urgent else "solar_charge"
                        self._set_decision(decision)
                        if await self._set_mode("charge", int(charge_target)):
                            self._charge_entry_export_count = 0
                    else:
                        self._set_decision("solar_export_waiting")
                        if self._current_mode not in ("self_consume", "idle"):
                            await self._set_mode("self_consume")
                else:
                    self._charge_entry_export_count = 0
                    self._set_decision("solar_self_consume")
                    if self._current_mode not in ("self_consume", "idle"):
                        await self._set_mode("self_consume")
            else:
                current_charge = self._get_current_power_setting("charge")
                solar_cap = max(solar - charge_margin, 100)

                if solar < min_solar_for_charge - 150:
                    self._set_decision("solar_self_consume")
                    self._last_charge_exit = time.monotonic()
                    self._charge_entry_export_count = 0
                    self._charge_bottomout_count = 0
                    await self._set_mode("self_consume")

                elif p1 > charge_margin:
                    charge_target = current_charge - (p1 + charge_margin)
                    charge_target = min(charge_target, solar_cap)
                    charge_target = max(charge_target, 100)

                    if charge_target <= 100:
                        self._charge_bottomout_count += 1
                        if self._charge_bottomout_count >= 3:
                            self._set_decision("solar_self_consume")
                            self._last_charge_exit = time.monotonic()
                            self._last_bottomout_exit = time.monotonic()
                            self._charge_entry_export_count = 0
                            self._charge_bottomout_count = 0
                            await self._set_mode("self_consume")
                        else:
                            self._set_decision("solar_charge_reduced")
                            await self._adjust_power("charge", 100)
                    else:
                        self._charge_bottomout_count = 0
                        self._set_decision("solar_charge")
                        await self._adjust_power("charge", int(charge_target))

                elif p1 < -(charge_margin + 100):
                    self._charge_bottomout_count = 0
                    excess_export = abs(p1) - charge_margin
                    charge_target = current_charge + excess_export
                    charge_target = min(charge_target, max_charge)
                    charge_target = min(charge_target, solar_cap)
                    self._set_decision("solar_charge")
                    await self._adjust_power("charge", int(charge_target))
                else:
                    self._charge_bottomout_count = 0
                    self._set_decision("solar_charge")
            return

        if solar_producing and soc >= soc_max:
            self._set_decision("solar_battery_full")
            if self._current_mode != "self_consume":
                await self._set_mode("self_consume")
            return

        # DEFAULT: self_consume as safe fallback
        self._set_decision("idle_default")
        if self._current_mode in ("charge", "discharge"):
            await self._set_mode("self_consume")


async def run_decision(engine):
    """Run the decision logic on FakeEngine."""
    await engine._make_decision()


# ═══════════════════════════════════════════════════════════════════════
# Priority 1: Emergency low SOC
# ═══════════════════════════════════════════════════════════════════════


class TestPriority1EmergencyLowSOC:
    """Test Priority 1: SOC below minimum triggers emergency actions."""

    @pytest.mark.asyncio
    async def test_low_soc_with_solar_charges(self):
        """SOC below min + solar producing -> emergency solar charge."""
        engine = FakeEngine(soc=15, solar=2000, soc_min=20)
        await run_decision(engine)
        assert engine._last_decision == "emergency_solar_charge"
        assert len(engine.mode_calls) == 1
        assert engine.mode_calls[0][0] == "charge"

    @pytest.mark.asyncio
    async def test_low_soc_no_solar_grid_allowed(self):
        """SOC below min + no solar + grid charge allowed -> grid charge."""
        engine = FakeEngine(soc=15, solar=0, soc_min=20, grid_charge_allowed=True)
        await run_decision(engine)
        assert engine._last_decision == "grid_charge_emergency"
        assert engine.mode_calls[0][0] == "charge"

    @pytest.mark.asyncio
    async def test_low_soc_no_solar_no_grid(self):
        """SOC below min + no solar + no grid charge -> idle."""
        engine = FakeEngine(soc=15, solar=0, soc_min=20, grid_charge_allowed=False)
        await run_decision(engine)
        assert engine._last_decision == "low_soc_idle"
        assert engine.mode_calls[0][0] == "idle"

    @pytest.mark.asyncio
    async def test_low_soc_already_charging_adjusts_power(self):
        """SOC below min + already in charge mode -> adjust power, don't switch."""
        engine = FakeEngine(soc=15, solar=3000, soc_min=20, current_mode="charge")
        await run_decision(engine)
        assert engine._last_decision == "emergency_solar_charge"
        assert len(engine.mode_calls) == 0  # No mode switch
        assert len(engine.adjust_calls) == 1  # Power adjustment
        assert engine.adjust_calls[0][0] == "charge"


# ═══════════════════════════════════════════════════════════════════════
# Priority 2: SOC above maximum
# ═══════════════════════════════════════════════════════════════════════


class TestPriority2OverMax:
    """Test Priority 2: SOC above maximum triggers forced discharge."""

    @pytest.mark.asyncio
    async def test_over_max_forces_discharge(self):
        """SOC above max -> forced discharge."""
        engine = FakeEngine(soc=95, soc_max=90, p1=500)
        await run_decision(engine)
        assert engine._last_decision == "forced_discharge_over_max"
        assert engine.mode_calls[0][0] == "discharge"

    @pytest.mark.asyncio
    async def test_over_max_already_discharging_adjusts(self):
        """SOC above max + already discharging -> adjust power."""
        engine = FakeEngine(soc=95, soc_max=90, p1=500, current_mode="discharge")
        await run_decision(engine)
        assert engine._last_decision == "forced_discharge_over_max"
        assert len(engine.mode_calls) == 0
        assert len(engine.adjust_calls) == 1


# ═══════════════════════════════════════════════════════════════════════
# Priority 3: High load
# ═══════════════════════════════════════════════════════════════════════


class TestPriority3HighLoad:
    """Test Priority 3: High home load handling."""

    @pytest.mark.asyncio
    async def test_high_load_with_sufficient_soc(self):
        """High load + enough SOC -> battery assist (self_consume)."""
        engine = FakeEngine(soc=80, home_load=8000, high_load_assist=True)
        engine.params["high_load_threshold"] = 6500
        await run_decision(engine)
        assert engine._last_decision == "high_load_battery_assist"
        assert engine.mode_calls[0][0] == "self_consume"

    @pytest.mark.asyncio
    async def test_high_load_low_soc_goes_idle(self):
        """High load + low SOC (would drain below night target) -> idle."""
        engine = FakeEngine(soc=30, home_load=8000, high_load_assist=True)
        engine.params["high_load_threshold"] = 6500
        await run_decision(engine)
        assert engine._last_decision == "high_load_grid_only"
        assert engine.mode_calls[0][0] == "idle"

    @pytest.mark.asyncio
    async def test_high_load_assist_disabled(self):
        """High load + assist disabled -> skips to next priority."""
        engine = FakeEngine(
            soc=70, home_load=8000, is_night=True, high_load_assist=False
        )
        engine.params["high_load_threshold"] = 6500
        await run_decision(engine)
        # Should fall through to Priority 4 (night)
        assert "night" in engine._last_decision


# ═══════════════════════════════════════════════════════════════════════
# Priority 4: Night mode
# ═══════════════════════════════════════════════════════════════════════


class TestPriority4Night:
    """Test Priority 4: Night mode handling."""

    @pytest.mark.asyncio
    async def test_night_soc_above_min_self_consumes(self):
        """Night + SOC above min -> self_consume to discharge for house."""
        engine = FakeEngine(soc=50, is_night=True, soc_min=20)
        await run_decision(engine)
        assert engine._last_decision == "night_self_consume"
        assert engine.mode_calls[0][0] == "self_consume"

    @pytest.mark.asyncio
    async def test_night_soc_at_min_idles(self):
        """Night + SOC at minimum -> idle to protect reserve."""
        engine = FakeEngine(soc=20, is_night=True, soc_min=20)
        await run_decision(engine)
        assert engine._last_decision == "night_reserve_hold"
        assert engine.mode_calls[0][0] == "idle"

    @pytest.mark.asyncio
    async def test_night_soc_below_min_triggers_emergency(self):
        """Night + SOC below minimum -> Priority 1 (emergency) takes over."""
        engine = FakeEngine(soc=15, is_night=True, soc_min=20)
        await run_decision(engine)
        assert engine._last_decision == "low_soc_idle"


# ═══════════════════════════════════════════════════════════════════════
# Priority 5: Solar optimization
# ═══════════════════════════════════════════════════════════════════════


class TestPriority5Solar:
    """Test Priority 5: Solar active — self_consume vs charge."""

    @pytest.mark.asyncio
    async def test_solar_low_stays_self_consume(self):
        """Solar below min threshold -> stay in self_consume."""
        engine = FakeEngine(soc=50, solar=500, p1=-200, soc_max=90)
        engine.params["min_solar_for_charge"] = 1000
        await run_decision(engine)
        assert engine._last_decision == "solar_self_consume"

    @pytest.mark.asyncio
    async def test_solar_exporting_counts_before_charge(self):
        """Heavy export -> counts up, doesn't immediately charge."""
        engine = FakeEngine(soc=50, solar=3000, p1=-1000, soc_max=90)
        engine.params["min_solar_for_charge"] = 1000
        engine.params["charge_entry_threshold"] = 500
        await run_decision(engine)
        # First tick should just count, not switch to charge
        assert engine._charge_entry_export_count == 1
        assert engine._last_decision == "solar_export_waiting"

    @pytest.mark.asyncio
    async def test_solar_battery_full_self_consumes(self):
        """Solar + SOC at max -> self_consume (battery full)."""
        engine = FakeEngine(soc=90, solar=3000, soc_max=90)
        await run_decision(engine)
        assert engine._last_decision == "solar_battery_full"
        assert engine.mode_calls[0][0] == "self_consume"


# ═══════════════════════════════════════════════════════════════════════
# Default fallback
# ═══════════════════════════════════════════════════════════════════════


class TestDefaultFallback:
    """Test default behavior when no priority matches."""

    @pytest.mark.asyncio
    async def test_default_idle_from_charge(self):
        """No conditions match + in charge mode -> switch to self_consume."""
        engine = FakeEngine(soc=50, solar=0, current_mode="charge")
        await run_decision(engine)
        assert engine._last_decision == "idle_default"
        assert engine.mode_calls[0][0] == "self_consume"

    @pytest.mark.asyncio
    async def test_default_already_self_consume(self):
        """No conditions match + already in self_consume -> no mode switch."""
        engine = FakeEngine(soc=50, solar=0, current_mode="self_consume")
        await run_decision(engine)
        assert engine._last_decision == "idle_default"
        assert len(engine.mode_calls) == 0


# ═══════════════════════════════════════════════════════════════════════
# P1 rolling average
# ═══════════════════════════════════════════════════════════════════════


class TestP1RollingAverage:
    """Test the P1 rolling average buffer."""

    def test_empty_buffer_returns_zero(self):
        engine = FakeEngine()
        assert engine.p1_avg == 0.0

    def test_single_value(self):
        engine = FakeEngine()
        engine._p1_buffer.append((time.monotonic(), 500.0))
        assert engine.p1_avg == 500.0

    def test_multiple_values_averaged(self):
        engine = FakeEngine()
        now = time.monotonic()
        engine._p1_buffer.append((now, 100.0))
        engine._p1_buffer.append((now, 200.0))
        engine._p1_buffer.append((now, 300.0))
        assert engine.p1_avg == 200.0


# ═══════════════════════════════════════════════════════════════════════
# Night consumption estimation
# ═══════════════════════════════════════════════════════════════════════


class TestNightEstimation:
    """Test night SOC target calculation."""

    def test_soc_needed_includes_buffer(self):
        """Night SOC target should be soc_min + energy needed."""
        engine = FakeEngine(soc_min=20)
        engine.params["avg_night_consumption"] = 400
        engine.params["battery_capacity_wh"] = 14800
        engine.params["night_buffer_pct"] = 5
        target = engine._soc_needed_for_night()
        # soc_min(20) + (400 * 12 * 1.05 / 14800) * 100 ≈ 20 + 34.05 ≈ 54
        assert target > engine.soc_min
        assert target < 100

    def test_soc_needed_with_zero_capacity(self):
        """Zero capacity should use fallback of 10000."""
        engine = FakeEngine(soc_min=20)
        engine.params["battery_capacity_wh"] = 0
        target = engine._soc_needed_for_night()
        assert target > 20  # Should still compute something reasonable


# ═══════════════════════════════════════════════════════════════════════
# Charge bottomout counter
# ═══════════════════════════════════════════════════════════════════════


class TestChargeBottomout:
    """Test charge mode exit via sustained bottomout."""

    @pytest.mark.asyncio
    async def test_bottomout_counter_increments(self):
        """Importing while in charge mode -> bottomout counter goes up."""
        engine = FakeEngine(
            soc=50, solar=1200, p1=500, current_mode="charge", soc_max=90
        )
        engine.params["charge_margin"] = 150
        engine.params["min_solar_for_charge"] = 1000
        await run_decision(engine)
        assert engine._charge_bottomout_count >= 1

    @pytest.mark.asyncio
    async def test_three_bottomouts_exits_to_self_consume(self):
        """Three consecutive bottomouts -> exit charge to self_consume."""
        engine = FakeEngine(
            soc=50, solar=1200, p1=2000, current_mode="charge", soc_max=90
        )
        engine.params["charge_margin"] = 150
        engine.params["min_solar_for_charge"] = 1000
        engine._charge_bottomout_count = 2  # Already at 2, next will be 3

        await run_decision(engine)
        assert engine._last_decision == "solar_self_consume"
        assert engine.mode_calls[0][0] == "self_consume"
