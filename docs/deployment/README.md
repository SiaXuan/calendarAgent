# Deployment artifacts (archived)

Files here are NOT used during normal (local) development. They're kept so
that re-deploying to a public host later is a quick copy-paste, not a
re-discovery job.

## Files

- **`railway.toml`** — Railway's build/start manifest. Was at repo root while
  the project was deployed to Railway. Moved here when the trial ended so
  Railway stops trying to re-deploy on every push.

## How to re-enable Railway

1. Copy `railway.toml` back to the repo root.
2. In the Railway dashboard, set these env vars on the service:
   - `ANTHROPIC_API_KEY`
   - `CALDAV_URL=https://caldav.icloud.com`
   - `CALDAV_USERNAME` (Apple ID email)
   - `CALDAV_PASSWORD` (app-specific password)
   - `CALDAV_TARGET_CALENDAR=Agent` (or whatever iCloud calendar you want
     agent blocks written to)
   - `DEPLOYMENT_MODE=cloud`  ← important, opens CORS to `*`
3. `git push` — Railway auto-deploys.

## Caveats (read before re-deploying)

- **Reminders.app won't sync from a Linux container.** iCloud's `/reminders/`
  CalDAV endpoint blocks PROPFIND, and AppleScript only runs on macOS. The
  cloud deployment will only have access to whatever ends up in
  `task_store.json` from earlier local syncs or iPhone Shortcut pushes
  (`/tasks/push-reminder`).
- **`data/` is gitignored.** Anything in there on the Railway side stays on
  Railway. Mount a volume if you need it to survive container restarts.
