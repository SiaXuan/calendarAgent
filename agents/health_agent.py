"""
Health Agent — converts a HealthSnapshot into a 24-hour energy curve and summary.
Energy curve is pure rule-based logic. Health summary uses Claude for non-English locales.

Chronotype model
────────────────
Bedtime is used to infer the user's circadian rhythm:
  - "Early bird"  (bed ≤ 22:00) → two peaks: midday + mid-afternoon, early wind-down
  - "Normal"      (bed 22–23:59) → standard two-peak curve
  - "Night owl"   (bed ≥ 00:00) → adds a third evening Gaussian centred 2 h before bed;
                                   wind-down is delayed accordingly

All three peak Gaussians are evaluated on an extended hour axis (h_ext) anchored at
wake_hour, so the arithmetic stays monotone across midnight.
"""
import json
import math
import os
from datetime import date

import anthropic

from models.health import HealthSnapshot
from models.schedule import FreeWindow
from models.user import Language

_HRV_BASELINE = 40.0   # ms — population median used when no personal history exists


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def compute_energy_curve(snapshot: HealthSnapshot) -> list[float]:
    """
    Return a list of 24 floats (index = hour 0–23, midnight = 0).
    All values are clamped to [0.0, 1.0].
    """
    sleep_end_h   = _frac_hour(snapshot.sleep.sleep_end)
    sleep_start_h = _frac_hour(snapshot.sleep.sleep_start)

    # Normalise to extended scale so sleep_start > sleep_end (overnight)
    if sleep_start_h < sleep_end_h:
        sleep_start_h += 24   # e.g. 2:00 am → 26.0

    duration  = snapshot.sleep.duration_hours
    wake_hour = sleep_end_h

    # ── Peak locations ────────────────────────────────────────────────────────
    primary_peak   = wake_hour + 3.0   # sharp morning/midday peak
    afternoon_peak = wake_hour + 8.0   # softer secondary peak

    # ── Chronotype (night-owl coefficient 0–1) ────────────────────────────────
    # Re-express bedtime on a 22–30 axis so linear interpolation is clean:
    #   bed at 22:00 → 22   (chronotype = 0.00)
    #   bed at 23:00 → 23   (chronotype = 0.25)
    #   bed at 00:00 → 24   (chronotype = 0.50)
    #   bed at 02:00 → 26   (chronotype = 1.00)
    bedtime_norm = sleep_start_h   # already in 24+ range for post-midnight sleepers;
    # for pre-midnight sleepers sleep_start_h < 24, which is correct
    chronotype = max(0.0, min(1.0, (bedtime_norm - 22.0) / 4.0))

    # Evening Gaussian: centred 2 h before sleep_start
    evening_peak = sleep_start_h - 2.0

    # ── Build curve ───────────────────────────────────────────────────────────
    raw: list[float] = []
    for h in range(24):
        # Extended axis: pre-wake hours shift to +24 so all three Gaussians
        # are evaluated with sensible distances from the peaks.
        h_ext = h if h >= wake_hour else h + 24

        v_morning   = _gauss(h_ext, primary_peak,   sigma=2.5)
        v_afternoon = _gauss(h_ext, afternoon_peak, sigma=2.0) * 0.7
        v_evening   = _gauss(h_ext, evening_peak,   sigma=1.8) * chronotype * 0.9

        value = max(v_morning, v_afternoon, v_evening)

        # Post-lunch dip
        if 13 <= h < 15:
            value *= 0.8

        # Gradual wind-down in the last 1.5 h before sleep (linear fade to ~0)
        wind_start = sleep_start_h - 1.5
        if h_ext >= wind_start:
            fade = max(0.0, (sleep_start_h - h_ext) / 1.5)   # 1→0 over 1.5 h
            value = min(value, fade * 0.55 + 0.05)

        # Zero out sleep hours
        if _is_asleep(h, sleep_end_h, sleep_start_h):
            value = 0.0

        raw.append(round(value, 3))

    # ── Sleep-quality modifiers ───────────────────────────────────────────────
    if duration < 5:
        raw = _scale_peak(raw, wake_hour, 0.50)
    elif duration < 6:
        raw = _scale_peak(raw, wake_hour, 0.70)

    # HRV boost (above-baseline recovery → better peak hours)
    if snapshot.hrv is not None and snapshot.hrv > _HRV_BASELINE:
        raw = _scale_peak(raw, wake_hour, 1.10)

    # Activity boost (≥ 30 active min before noon → lift 1 h after activity)
    if snapshot.active_minutes and snapshot.active_minutes > 30 and wake_hour < 12:
        boost_h = int(wake_hour + snapshot.active_minutes / 60 + 1)
        if 0 <= boost_h < 24:
            raw[boost_h] = min(1.0, raw[boost_h] * 1.15)

    return [round(min(1.0, max(0.0, v)), 3) for v in raw]


def score_windows(windows: list[FreeWindow], curve: list[float]) -> list[FreeWindow]:
    """Attach average energy score to each FreeWindow."""
    result = []
    for w in windows:
        hours = list(range(w.start_hour, min(w.end_hour, 24)))
        if not hours:
            result.append(w.model_copy(update={"energy_score": 0.0}))
            continue
        avg = sum(curve[h] for h in hours) / len(hours)
        result.append(w.model_copy(update={"energy_score": round(avg, 3)}))
    return result


async def get_health_summary(
    snapshot: HealthSnapshot,
    language: Language = Language.en,
) -> str:
    """1–2 sentence summary. English is rule-based; other locales use Claude."""
    english = _build_english_summary(snapshot)
    if language == Language.en:
        return english
    return await _translate_summary(english, language)


# ──────────────────────────────────────────────────────────────────────────────
# Summary builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_english_summary(snapshot: HealthSnapshot) -> str:
    parts: list[str] = []
    d = snapshot.sleep.duration_hours

    if d < 5:
        parts.append(f"Very low sleep ({d:.1f}h) — light day strongly recommended.")
    elif d < 6:
        parts.append(f"Low sleep ({d:.1f}h) — lighter morning recommended.")
    elif d < 7:
        parts.append(f"Below-average sleep ({d:.1f}h) — avoid back-to-back deep-work blocks.")
    else:
        parts.append(f"Good sleep ({d:.1f}h).")

    # Chronotype annotation
    sleep_start_h = _frac_hour(snapshot.sleep.sleep_start)
    sleep_end_h   = _frac_hour(snapshot.sleep.sleep_end)
    if sleep_start_h < sleep_end_h:
        sleep_start_h += 24
    chronotype = max(0.0, min(1.0, (sleep_start_h - 22.0) / 4.0))
    if chronotype >= 0.75:
        parts.append("Night-owl schedule detected — late-evening energy peak included.")
    elif chronotype >= 0.4:
        parts.append("Late-night schedule — evening energy boost applied.")

    if snapshot.hrv is not None:
        if snapshot.hrv < _HRV_BASELINE * 0.8:
            parts.append("HRV is low — consider recovery-focused scheduling.")
        elif snapshot.hrv > _HRV_BASELINE * 1.2:
            parts.append("HRV is elevated — peak cognitive capacity available.")

    if snapshot.resting_heart_rate is not None and snapshot.resting_heart_rate > 75:
        parts.append("Elevated resting HR — monitor stress load today.")

    return " ".join(parts)


async def _translate_summary(english: str, language: Language) -> str:
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    client = anthropic.AsyncAnthropic(api_key=api_key)
    try:
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=256,
            system=(
                f"Translate the following health summary into {language.value}. "
                "Return only the translated text, no explanation."
            ),
            messages=[{"role": "user", "content": english}],
        )
        return response.content[0].text.strip()
    except Exception:
        return english


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _frac_hour(dt) -> float:
    """Convert a datetime to a fractional hour (e.g. 09:30 → 9.5)."""
    return dt.hour + dt.minute / 60


def _gauss(x: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def _is_asleep(hour: int, wake_hour: float, sleep_start_hour: float) -> bool:
    """True if `hour` falls inside the sleep window [sleep_start % 24, wake_hour)."""
    s = sleep_start_hour % 24
    if s > wake_hour:
        return hour >= s or hour < wake_hour
    return s <= hour < wake_hour


def _scale_peak(curve: list[float], wake_hour: float, factor: float) -> list[float]:
    """Multiply the primary-peak window (wake+1 … wake+7) by factor."""
    result = curve[:]
    for h in range(int(wake_hour + 1), min(int(wake_hour + 8), 24)):
        result[h] = min(1.0, result[h] * factor)
    return result
