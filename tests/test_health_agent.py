"""Tests for agents/health_agent.py — no LLM calls, no network."""
import json
from datetime import date, datetime

import pytest

from agents.health_agent import (
    compute_energy_curve,
    get_health_summary,
    score_windows,
)
from models.health import HealthSnapshot, SleepData
from models.schedule import FreeWindow


def _make_snapshot(
    sleep_start="2026-04-10T23:30:00",
    sleep_end="2026-04-11T07:15:00",
    hrv: float | None = 45.0,
    resting_heart_rate: int | None = 58,
    active_minutes: int | None = 38,
) -> HealthSnapshot:
    start = datetime.fromisoformat(sleep_start)
    end = datetime.fromisoformat(sleep_end)
    duration = (end - start).total_seconds() / 3600
    return HealthSnapshot(
        date=date(2026, 4, 11),
        sleep=SleepData(
            duration_hours=duration,
            sleep_start=start,
            sleep_end=end,
        ),
        hrv=hrv,
        resting_heart_rate=resting_heart_rate,
        active_minutes=active_minutes,
        submitted_at=datetime.now(),
    )


class TestComputeEnergyCurve:
    def test_returns_24_values(self):
        curve = compute_energy_curve(_make_snapshot())
        assert len(curve) == 24

    def test_all_values_in_range(self):
        curve = compute_energy_curve(_make_snapshot())
        assert all(0.0 <= v <= 1.0 for v in curve), curve

    def test_sleep_hours_are_zero(self):
        """Hours 0–6 should be near zero (sleeping)."""
        curve = compute_energy_curve(_make_snapshot())
        for h in range(0, 6):
            assert curve[h] == 0.0, f"hour {h} expected 0, got {curve[h]}"

    def test_post_lunch_dip(self):
        """Hours 13–14 must be lower than the morning peak."""
        curve = compute_energy_curve(_make_snapshot())
        morning_peak = max(curve[9:12])
        assert curve[13] < morning_peak, "post-lunch dip not observed"

    def test_low_sleep_reduces_peak(self):
        """< 6h sleep should produce a lower morning peak."""
        good = compute_energy_curve(_make_snapshot())
        bad = compute_energy_curve(
            _make_snapshot(sleep_start="2026-04-11T02:00:00", sleep_end="2026-04-11T07:00:00")
        )
        good_peak = max(good[8:13])
        bad_peak = max(bad[8:13])
        assert bad_peak < good_peak, "low sleep should reduce morning peak"

    def test_high_hrv_boosts_peak(self):
        """HRV above baseline (40ms) should yield a higher peak than no HRV."""
        base = compute_energy_curve(_make_snapshot(hrv=None))
        boosted = compute_energy_curve(_make_snapshot(hrv=60.0))
        assert max(boosted[8:13]) >= max(base[8:13])

    def test_mock_data_file(self):
        """Smoke test using the sample health file."""
        with open("tests/mock_data/health_sample.json") as f:
            data = json.load(f)
        snapshot = _make_snapshot(
            sleep_start=data["sleep_start"],
            sleep_end=data["sleep_end"],
            hrv=data.get("hrv"),
            resting_heart_rate=data.get("resting_heart_rate"),
            active_minutes=data.get("active_minutes"),
        )
        curve = compute_energy_curve(snapshot)
        assert len(curve) == 24
        assert all(0.0 <= v <= 1.0 for v in curve)


class TestScoreWindows:
    def test_score_attached(self):
        curve = [0.0] * 24
        for h in range(9, 12):
            curve[h] = 0.9
        windows = [FreeWindow(start_hour=9, end_hour=12, duration_minutes=180)]
        scored = score_windows(windows, curve)
        assert len(scored) == 1
        assert abs(scored[0].energy_score - 0.9) < 0.01

    def test_empty_window_list(self):
        curve = [0.5] * 24
        assert score_windows([], curve) == []


class TestGetHealthSummary:
    async def test_good_sleep(self):
        s = _make_snapshot()
        summary = await get_health_summary(s)
        assert "Good sleep" in summary

    async def test_low_sleep_warning(self):
        s = _make_snapshot(
            sleep_start="2026-04-11T02:00:00",
            sleep_end="2026-04-11T07:00:00",
        )
        summary = await get_health_summary(s)
        assert "Low sleep" in summary or "Below-average" in summary

    async def test_high_hrv_positive(self):
        s = _make_snapshot(hrv=60.0)
        summary = await get_health_summary(s)
        assert "HRV" in summary or "Good sleep" in summary

    async def test_returns_string(self):
        s = _make_snapshot()
        assert isinstance(await get_health_summary(s), str)
