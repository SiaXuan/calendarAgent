"""
Weather + commute tools on the chat agent's belt.

Both are read-only context: they answer questions the schedule can't, and never
reschedule anything (that stays with the schedule tools, so the impact gate
still sees every change as a scratch diff).

The weather HTTP call is never made here — `parse_forecast` is split out from
the fetch precisely so the shape handling is testable offline.
"""
from datetime import date

import pytest

from agents.tools.context_tools import make_context_tools
from api.preferences import get_current_prefs
from integrations.weather import DayWeather, HourlyWeather, describe_code, parse_forecast

D = date(2026, 6, 15)


@pytest.fixture
def prefs():
    """Preferences are process-global; restore what was there."""
    p = get_current_prefs()
    before = (p.latitude, p.longitude, dict(p.commute_minutes))
    yield p
    p.latitude, p.longitude = before[0], before[1]
    p.commute_minutes = before[2]


def _tool(name):
    return next(t for t in make_context_tools(D) if t.name == name)


# ─── forecast parsing ───────────────────────────────────────────────────────

def _payload(**series):
    base = {"time": [f"2026-06-15T{h:02d}:00" for h in range(24)]}
    base.update(series)
    return {"hourly": base}


def test_parses_hours_from_timestamps():
    day = parse_forecast(D, _payload(
        temperature_2m=[12.0] * 24,
        precipitation_probability=[0] * 24,
        weathercode=[0] * 24,
    ))
    assert [h.hour for h in day.hours] == list(range(24))
    assert day.summary() == "12–12°C, dry all day"


def test_wet_hours_are_the_ones_worth_moving_a_run_out_of():
    precip = [0] * 24
    for h in (14, 15, 16):
        precip[h] = 80
    day = parse_forecast(D, _payload(
        temperature_2m=[18.0] * 24,
        precipitation_probability=precip,
        weathercode=[61 if p else 0 for p in precip],
    ))
    assert day.wet_hours == [14, 15, 16]
    assert "wet 14-16" in day.summary()


def test_wet_runs_are_compressed_not_listed_one_by_one():
    """A prompt shouldn't carry 24 numbers."""
    precip = [0] * 24
    for h in (9, 10, 11, 15):
        precip[h] = 90
    day = parse_forecast(D, _payload(
        temperature_2m=[10.0] * 24, precipitation_probability=precip,
        weathercode=[0] * 24))
    assert "wet 9-11, 15" in day.summary()


def test_rain_code_counts_as_wet_even_at_low_probability():
    day = parse_forecast(D, _payload(
        temperature_2m=[8.0] * 24, precipitation_probability=[10] * 24,
        weathercode=[95] * 24))
    assert len(day.wet_hours) == 24


def test_missing_series_degrade_instead_of_raising():
    """Open-Meteo can omit a field; a partial forecast beats none."""
    day = parse_forecast(D, _payload())
    assert len(day.hours) == 24
    assert all(h.temperature_c is None for h in day.hours)
    assert day.summary() == "dry all day"


def test_empty_payload_is_none():
    assert parse_forecast(D, {}) is None
    assert parse_forecast(D, {"hourly": {"time": []}}) is None


def test_unknown_weather_code_is_not_fatal():
    assert describe_code(None) == "unknown"
    assert describe_code(12345) == "unknown"
    assert describe_code(61) == "rain"


# ─── tools ──────────────────────────────────────────────────────────────────

async def test_weather_tool_says_so_when_no_location_is_set(prefs):
    prefs.latitude = prefs.longitude = None
    out = await _tool("get_weather").ainvoke({})
    assert "没有天气数据" in out


async def test_weather_tool_reports_wet_hours(prefs, monkeypatch):
    prefs.latitude, prefs.longitude = 42.34, -71.09
    hours = [HourlyWeather(h, 15.0, 90 if h in (13, 14) else 0,
                           "rain" if h in (13, 14) else "clear") for h in range(24)]

    async def fake(target_date, lat, lon):
        return DayWeather(date=target_date, hours=hours)

    monkeypatch.setattr("integrations.weather.fetch_day_weather", fake)
    out = await _tool("get_weather").ainvoke({})
    assert "13" in out and "14" in out


async def test_weather_tool_survives_the_service_being_down(prefs, monkeypatch):
    """A missing forecast must not fail the turn — the schedule is still valid."""
    prefs.latitude, prefs.longitude = 42.34, -71.09

    async def fake(target_date, lat, lon):
        return None

    monkeypatch.setattr("integrations.weather.fetch_day_weather", fake)
    out = await _tool("get_weather").ainvoke({})
    assert "拿不到天气预报" in out


def test_commute_tool_says_so_when_unconfigured(prefs):
    prefs.commute_minutes = {}
    out = _tool("get_commute_minutes").invoke({"from_place": "home", "to_place": "campus"})
    assert "没有通勤数据" in out


def test_commute_tool_is_direction_agnostic(prefs):
    """One configured route answers both ways — nobody configures both."""
    prefs.commute_minutes = {"home->campus": 35}
    tool = _tool("get_commute_minutes")
    assert "35" in tool.invoke({"from_place": "home", "to_place": "campus"})
    assert "35" in tool.invoke({"from_place": "campus", "to_place": "home"})


def test_commute_tool_lists_what_it_knows_when_asked_for_an_unknown_route(prefs):
    prefs.commute_minutes = {"home->campus": 35}
    out = _tool("get_commute_minutes").invoke({"from_place": "home", "to_place": "airport"})
    assert "home->campus" in out
