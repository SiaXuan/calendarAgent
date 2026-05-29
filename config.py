"""
Single source of truth for deployment-mode config.

Most of the codebase is environment-agnostic. The only knobs that actually
differ between running locally and running on a public host (Railway, etc.)
are collected here so future readers can audit "what's different?" in one
file instead of grepping the repo.
"""
import os
import sys


DEPLOYMENT_MODE = os.getenv("DEPLOYMENT_MODE", "local").lower()
IS_LOCAL = DEPLOYMENT_MODE == "local"
IS_CLOUD = not IS_LOCAL


# CORS origins.
# Local mode: restrict to the Vite dev server — we're on a private machine and
# there's no reason to accept requests from anywhere else.
# Cloud mode: keep "*" because public deployments are reached from arbitrary
# devices (iPhone Shortcuts, browsers on other networks, etc.).
CORS_ORIGINS: list[str] = (
    ["http://localhost:5173", "http://127.0.0.1:5173"]
    if IS_LOCAL
    else ["*"]
)


# Informational flag — Reminders.app via AppleScript only works on macOS.
# `integrations/caldav_client.py` already gates the AppleScript path on
# `sys.platform == "darwin"`; this constant is for documentation/awareness.
APPLESCRIPT_AVAILABLE: bool = IS_LOCAL and sys.platform == "darwin"
