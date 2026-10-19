"""External-context tools for the chat agent.

These answer questions the schedule itself can't: what the weather will do, how
long a commute takes. They only ever READ — rescheduling stays with the schedule
tools, so the impact gate still sees every change as a diff on the scratch.

Weather is a direct HTTP call rather than an MCP server (see
integrations/weather.py for why). Commute is a user-configured table rather than
a maps API: the realistic question is "how long from home to campus", the answer
barely changes, and the Google Maps MCP server was archived while its managed
replacement can't route transit.
"""
from datetime import date

from langchain_core.tools import StructuredTool


def make_context_tools(target_date: date) -> list:
    """Read-only context tools. Each degrades to a plain sentence the agent can
    relay, never an exception — a missing forecast must not fail the turn."""

    async def get_weather() -> str:
        """查询当天的天气预报(按小时)。安排户外任务(跑步、买菜、骑车通勤)前先看这个。
        返回温度范围和会下雨/雪的时段;用户没设置位置时会告诉你没有数据。"""
        from api.preferences import get_current_prefs
        from integrations.weather import fetch_day_weather

        prefs = get_current_prefs()
        if prefs.latitude is None or prefs.longitude is None:
            return "没有天气数据：用户还没设置位置（PATCH /preferences 的 latitude/longitude）。"

        weather = await fetch_day_weather(target_date, prefs.latitude, prefs.longitude)
        if weather is None:
            return "拿不到天气预报（服务不可用或日期超出预报范围）。"

        lines = [f"{target_date.isoformat()} 天气：{weather.summary()}"]
        wet = weather.wet_hours
        if wet:
            lines.append(f"这些整点有雨雪，户外任务避开：{wet}")
        return "\n".join(lines)

    def get_commute_minutes(from_place: str, to_place: str) -> str:
        """查两个地点之间的通勤时长(分钟)。排程时如果两件事在不同地点,中间要留出这段时间。
        地点名用用户自己配置的那些(比如 home / campus)。"""
        from api.preferences import get_current_prefs

        routes = getattr(get_current_prefs(), "commute_minutes", {}) or {}
        if not routes:
            return "没有通勤数据：用户还没配置常用路线（PATCH /preferences 的 commute_minutes）。"

        key = f"{from_place.strip().lower()}->{to_place.strip().lower()}"
        reverse = f"{to_place.strip().lower()}->{from_place.strip().lower()}"
        normalised = {k.strip().lower(): v for k, v in routes.items()}
        minutes = normalised.get(key) or normalised.get(reverse)
        if minutes is None:
            return (f"没有 {from_place} 到 {to_place} 的通勤时长。"
                    f"已配置的路线：{sorted(routes)}")
        return f"{from_place} → {to_place} 约 {minutes} 分钟。"

    return [
        StructuredTool.from_function(coroutine=get_weather, name="get_weather",
                                     description=get_weather.__doc__),
        StructuredTool.from_function(get_commute_minutes),
    ]
