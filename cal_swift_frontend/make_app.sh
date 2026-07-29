#!/bin/bash
# Package the SwiftPM executable into a real .app bundle and ad-hoc code-sign it.
# macOS TCC (privacy) only recognises EventKit usage-description strings from a
# proper bundle — a bare `swift run` binary is denied outright. Run this, then:
#   ./ScheduleAgent.app/Contents/MacOS/ScheduleAgentApp --verify-eventkit
set -euo pipefail
cd "$(dirname "$0")"

CONFIG="${1:-debug}"     # debug | release
APP="ScheduleAgent.app"

echo "› Building ($CONFIG)…"
swift build -c "$CONFIG" >/dev/null
BIN_DIR="$(swift build -c "$CONFIG" --show-bin-path)"

echo "› Assembling ${APP}…"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$BIN_DIR/ScheduleAgentApp" "$APP/Contents/MacOS/ScheduleAgentApp"
cp Info.plist "$APP/Contents/Info.plist"

echo "› Code-signing (ad-hoc)…"
codesign --force --sign - --identifier com.dayflow.scheduleagent "$APP"

echo "✓ Built $APP"
echo "  Run:  ./$APP/Contents/MacOS/ScheduleAgentApp --verify-eventkit"
