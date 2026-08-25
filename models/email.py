"""Email triage report (Phase D). The agent reads today's new mail via a
user-attached email MCP server and returns this structured summary; the Swift
Mail window renders it. Read-only for now — detected invites are surfaced, not
auto-added (the user can then ask the main agent to add one via add_to_schedule)."""
from pydantic import BaseModel, Field


class EmailItem(BaseModel):
    subject: str
    sender: str | None = None
    gist: str | None = None   # one-line why-it-matters / summary


class EmailReport(BaseModel):
    connected: bool                                # is an email MCP server attached?
    generated_at: str                              # ISO timestamp of this triage
    summary: str = ""                              # short overview in the user's language
    needs_reply: list[EmailItem] = Field(default_factory=list)
    newsletters: list[EmailItem] = Field(default_factory=list)
    detected_invites: list[EmailItem] = Field(default_factory=list)  # meetings/interviews/tests
    filtered_ads: int = 0                          # count of promotional mail skipped
    note: str | None = None                        # e.g. "no mailbox connected"
