"""Hourly weather for the scheduled day, from Open-Meteo.

Deliberately NOT an MCP server. Open-Meteo needs no API key and the backend is
already an HTTP client, so a dedicated MCP process would add a subprocess, a
tool description competing for prompt space, and another failure mode — to wrap
one GET. The MCP servers that do exist for weather are small single-maintainer
projects with no ecosystem standing. If the user ever wants to swap in their own
weather source, that is what `custom_mcp_servers` is for.

Weather matters to scheduling for a narrow, concrete reason: an outdoor errand
(a run, a grocery trip, a commute by bike) is worth moving out of the rain, and
the chat agent can only reason about that if it can see the forecast.

Free for non-commercial use with attribution (CC BY 4.0); no key, no signup.
https://open-meteo.com/
"""
import logging
from dataclasses import dataclass
from datetime import date

import httpx

_log = logging.getLogger("dayflow")

_ENDPOINT = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_S = 8.0   # a forecast is never worth stalling schedule generation for

# WMO weather codes → a short label. Grouped, not exhaustive: the agent needs
# "is it raining" far more than it needs to tell drizzle from freezing drizzle.
# https://open-meteo.com/en/docs
_WMO = [
    (0, 0, "clear"), (1, 3, "cloudy"), (45, 48, "fog"),
    (51, 57, "drizzle"), (61, 67, "rain"), (71, 77, "snow"),
    (80, 82, "rain showers"), (85, 86, "snow showers"), (95, 99, "thunderstorm"),
]


def describe_code(code: int | None) -> str:
    if code is None:
        return "unknown"
    for lo, hi, label in _WMO:
        if lo <= code <= hi:
            return label
    return "unknown"


@dataclass(frozen=True)
class HourlyWeather:
    hour: int                       # 0-23, local time
    temperature_c: float | None
    precipitation_chance: int | None   # percent
    description: str

    @property
    def is_wet(self) -> bool:
        """Worth moving an outdoor task away from."""
        return (self.precipitation_chance or 0) >= 50 or self.description in {
            "rain", "snow", "rain showers", "snow showers", "thunderstorm",
        }


@dataclass(frozen=True)
class DayWeather:
    date: date
    hours: list[HourlyWeather]

    @property
    def wet_hours(self) -> list[int]:
        return [h.hour for h in self.hours if h.is_wet]

    def summary(self) -> str:
        """One line for a prompt. Empty when there's nothing worth saying."""
        if not self.hours:
            return ""
        temps = [h.temperature_c for h in self.hours if h.temperature_c is not None]
        parts = []
        if temps:
            parts.append(f"{round(min(temps))}–{round(max(temps))}°C")
        wet = self.wet_hours
        if wet:
            parts.append(f"wet {_compress_hours(wet)}")
        else:
            parts.append("dry all day")
        return ", ".join(parts)


def _compress_hours(hours: list[int]) -> str:
    """[9,10,11,15] → '9-11, 15' — a prompt shouldn't carry 24 numbers."""
    if not hours:
        return ""
    runs: list[tuple[int, int]] = []
    start = prev = hours[0]
    for h in hours[1:]:
        if h == prev + 1:
            prev = h
            continue
        runs.append((start, prev))
        start = prev = h
    runs.append((start, prev))
    return ", ".join(f"{a}-{b}" if a != b else f"{a}" for a, b in runs)


async def fetch_day_weather(
    target_date: date, latitude: float, longitude: float
) -> DayWeather | None:
    """Hourly forecast for one day, or None if it can't be had.

    Returns None rather than raising: weather is an enhancement, and a schedule
    without it is still correct. Open-Meteo only forecasts forward, so a past
    date yields nothing useful.
    """
    params = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": "temperature_2m,precipitation_probability,weathercode",
        "start_date": target_date.isoformat(),
        "end_date": target_date.isoformat(),
        "timezone": "auto",
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(_ENDPOINT, params=params)
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:
        _log.warning("weather fetch failed (%s); continuing without it", exc)
        return None

    return parse_forecast(target_date, payload)


def parse_forecast(target_date: date, payload: dict) -> DayWeather | None:
    """Split out from the fetch so the shape handling is testable offline."""
    hourly = (payload or {}).get("hourly") or {}
    times = hourly.get("time") or []
    if not times:
        return None

    temps = hourly.get("temperature_2m") or []
    precip = hourly.get("precipitation_probability") or []
    codes = hourly.get("weathercode") or []

    def at(seq, i):
        return seq[i] if i < len(seq) else None

    hours = []
    for i, stamp in enumerate(times):
        try:
            hour = int(str(stamp)[11:13])
        except (ValueError, IndexError):
            continue
        hours.append(HourlyWeather(
            hour=hour,
            temperature_c=at(temps, i),
            precipitation_chance=at(precip, i),
            description=describe_code(at(codes, i)),
        ))
    return DayWeather(date=target_date, hours=hours) if hours else None
