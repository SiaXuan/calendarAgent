"""
Centralised ChatAnthropic clients.

All other modules import `haiku` (fast, cheap) or `sonnet` (better reasoning)
from here so we have one place to change model versions, temperatures, or
swap providers entirely.

Env knobs (see .env.example for defaults):
  LLM_FAST_MODEL          — model ID for the haiku client
  LLM_FAST_TEMPERATURE    — temperature for the haiku client
  LLM_REASON_MODEL        — model ID for the sonnet client
  LLM_REASON_TEMPERATURE  — temperature for the sonnet client

Defaults match the Anthropic models we built against; override in .env when
you want to test a different version or swap providers.
"""
import os

from langchain_anthropic import ChatAnthropic


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


# Fast classifier / ranker. Used for: task ranking, simple translations, light JSON extraction.
haiku = ChatAnthropic(
    model=os.getenv("LLM_FAST_MODEL", "claude-haiku-4-5-20251001"),
    temperature=_env_float("LLM_FAST_TEMPERATURE", 0.0),
    max_tokens=2048,
)


# Reasoning model. Used for: task decomposition, chat parsing, planning conversations.
sonnet = ChatAnthropic(
    model=os.getenv("LLM_REASON_MODEL", "claude-sonnet-4-6"),
    temperature=_env_float("LLM_REASON_TEMPERATURE", 0.3),
    max_tokens=4096,
)
