"""
Centralised ChatAnthropic clients.

All other modules import `haiku` (fast, cheap) or `sonnet` (better reasoning)
from here so we have one place to change model versions, temperatures, or
swap providers entirely.
"""
from langchain_anthropic import ChatAnthropic


# Fast classifier / ranker. Used for: task ranking, simple translations, light JSON extraction.
haiku = ChatAnthropic(
    model="claude-haiku-4-5-20251001",
    temperature=0.0,
    max_tokens=2048,
)


# Reasoning model. Used for: task decomposition, chat parsing, planning conversations.
sonnet = ChatAnthropic(
    model="claude-sonnet-4-6",
    temperature=0.3,
    max_tokens=4096,
)
