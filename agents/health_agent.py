"""
Health Agent — converts a HealthSnapshot into a 24-hour energy curve and a plain-text summary.
Energy curve is pure rule-based logic. Health summary uses Claude for non-English languages.
"""
import json
import os
from datetime import date

import anthropic

from models.health import HealthSnapshot
from models.schedule import FreeWindow
from models.user import Language


# Typical HRV baseline used when no personal history is available
_HRV_BASELINE = 40.0


def compute_energy_curve(snapshot: HealthSnapshot) -> list[float]:
    """
    Return a list of 24 floats indexed by hour (0 = midnight).
    All values clamped to [0.0, 1.0].
    """
    sleep_end_hour = snapshot.sleep.sleep_end.hour + snapshot.sleep.sleep_end.minute / 60
    sleep_start_hour = snapshot.sleep.sleep_start.hour + snapshot.sleep.sleep_start.minute / 60

    # Normalise sleep_start so that a 23:00 bedtime → 23.0 and midnight+ stays as-is
    # (sleep_start could be previous calendar day but we work with hour values only)
    if sleep_start_hour < sleep_end_hour:
        # e.g. nap: started at 13:00, ended 14:30 — treat as staying-up edge case
        sleep_start_hour += 24

    duration = snapshot.sleep.duration_hours
    wake_hour = sleep_end_hour

    # --- Build baseline Gaussian-ish curve centred on peak hours ---
    peak_hour = wake_hour + 3.0   # primary peak 3h after waking
    afternoon_peak = wake_hour + 8.0   # secondary peak (mid-afternoon)

    curve: list[float] = []
    for h in range(24):
        # Primary morning peak contribution
        morning = _gaussian(h, peak_hour, sigma=2.5)
        # Secondary afternoon peak
        afternoon = _gaussian(h, afternoon_peak, sigma=2.0) * 0.7
        value = max(morning, afternoon)

        # Post-lunch dip: hours 13–14 always lose 20%
        if 13 <= h < 15:
            value *= 0.8

        # Last 2h before sleep: cap at 0.2
        if sleep_start_hour <= 24:
            wind_down_start = sleep_start_hour - 2
            if h >= wind_down_start % 24:
                value = min(value, 0.2)

        # Sleep hours: energy = 0
        asleep = _is_asleep(h, sleep_end_hour, sleep_start_hour)
        if asleep:
            value = 0.0

        curve.append(round(value, 3))

    # --- Apply sleep-quality modifiers ---
    if duration < 5:
        curve = _scale_peak_hours(curve, wake_hour, factor=0.50)
    elif duration < 6:
        curve = _scale_peak_hours(curve, wake_hour, factor=0.70)

    # HRV boost: if above baseline, +10% on peak hours
    if snapshot.hrv is not None and snapshot.hrv > _HRV_BASELINE:
        curve = _scale_peak_hours(curve, wake_hour, factor=1.10)

    # Activity boost: if > 30 active minutes before noon, light +15% 1h after
    if (
        snapshot.active_minutes is not None
        and snapshot.active_minutes > 30
        and wake_hour < 12
    ):
        boost_hour = int(wake_hour + snapshot.active_minutes / 60 + 1)
        if 0 <= boost_hour < 24:
            curve[boost_hour] = min(1.0, curve[boost_hour] * 1.15)

    return [round(min(1.0, max(0.0, v)), 3) for v in curve]


def score_windows(windows: list[FreeWindow], curve: list[float]) -> list[FreeWindow]:
    """Attach average energy score to each FreeWindow from the curve."""
    scored = []
    for w in windows:
        hours = list(range(w.start_hour, min(w.end_hour, 24)))
        if not hours:
            scored.append(w.model_copy(update={"energy_score": 0.0}))
            continue
        avg = sum(curve[h] for h in hours) / len(hours)
        scored.append(w.model_copy(update={"energy_score": round(avg, 3)}))
    return scored


async def get_health_summary(
    snapshot: HealthSnapshot,
    language: Language = Language.en,
) -> str:
    """
    Return a 1–2 sentence health summary for the schedule header.
    English: rule-based (no LLM). Other languages: rule-based English base
    translated via a small Claude call.
    """
    english_summary = _build_english_summary(snapshot)
    if language == Language.en:
        return english_summary
    return await _translate_summary(english_summary, language)


def _build_english_summary(snapshot: HealthSnapshot) -> str:
    """Rule-based English summary — no LLM."""
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

    if snapshot.hrv is not None:
        if snapshot.hrv < _HRV_BASELINE * 0.8:
            parts.append("HRV is low — consider recovery-focused scheduling.")
        elif snapshot.hrv > _HRV_BASELINE * 1.2:
            parts.append("HRV is elevated — peak cognitive capacity available.")

    if snapshot.resting_heart_rate is not None and snapshot.resting_heart_rate > 75:
        parts.append("Elevated resting HR — monitor stress load today.")

    return " ".join(parts)


async def _translate_summary(english: str, language: Language) -> str:
    """Translate an English health summary into the target language via Claude."""
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
        return english  # fall back to English on error


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────

def _gaussian(x: float, mu: float, sigma: float) -> float:
    import math
    return math.exp(-0.5 * ((x - mu) / sigma) ** 2)


def _is_asleep(hour: int, wake_hour: float, sleep_start_hour: float) -> bool:
    """Return True if `hour` falls inside the sleep window."""
    # Sleep window: [sleep_start_hour % 24 .. wake_hour)
    sleep_start_mod = sleep_start_hour % 24
    if sleep_start_mod > wake_hour:
        # Crosses midnight: asleep if h >= sleep_start_mod OR h < wake_hour
        return hour >= sleep_start_mod or hour < wake_hour
    else:
        return sleep_start_mod <= hour < wake_hour


def _scale_peak_hours(
    curve: list[float], wake_hour: float, factor: float
) -> list[float]:
    """Multiply hours in the primary peak window (wake+1 to wake+6) by factor."""
    result = curve[:]
    start = int(wake_hour + 1)
    end = int(wake_hour + 7)
    for h in range(start, min(end, 24)):
        result[h] = min(1.0, result[h] * factor)
    return result
