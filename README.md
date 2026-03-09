# Claude Usage Monitor

A macOS menu bar app that displays your Claude.ai usage limits as a battery-style indicator. Click the menu bar icon to see disaggregated breakdowns of each usage meter (5-hour, 7-day, extra usage) with progress bars and reset times.

## Features

- Battery-style emoji indicator in the menu bar (green/yellow/warning/blocked)
- Disaggregated breakdown of all usage meters: 5-hour rolling, 7-day rolling, extra usage credits
- Progress bars with percentage and time-until-reset
- Threshold notifications when usage crosses 50%, 80%, and 95%
- Auto-refreshes every 5 minutes
- Manual refresh via menu
- Runs as a true menu-bar-only app (no Dock icon, no app switcher entry)
- Auto-starts on login via LaunchAgent

## Requirements

- macOS (tested on macOS 14+)
- Python 3.9+
- Google Chrome with an active claude.ai session
- Chrome setting: **View → Developer → Allow JavaScript from Apple Events** (must be enabled)

## How It Works

The app uses AppleScript to execute JavaScript in your Chrome browser tabs on claude.ai. This lets it read your session cookies (which are in-memory only and inaccessible via traditional cookie-extraction tools) and make authenticated API calls to `claude.ai/api/organizations/{org_uuid}/usage`.

All data fetching and UI updates happen on the main thread via `rumps.timer` callbacks — no background threads. This is a hard requirement of PyObjC/AppKit; updating Cocoa UI elements from background threads causes immediate segfaults.

## Installation

```bash
cd app
chmod +x install.sh
./install.sh
```

The install script:
1. Creates a Python virtual environment at `~/.claude-usage-monitor/.venv`
2. Installs dependencies (rumps, Pillow, keyring, requests)
3. Generates the app icon (.icns) from `generate_icon.py`
4. Builds a `.app` bundle at `/Applications/Claude Usage Monitor.app`
5. Installs a LaunchAgent for auto-start on login
6. Kills any previously running instance and launches the new build

Logs go to `~/.claude-usage-monitor/app.log`.

## Development

Run directly from source without building a `.app` bundle:

```bash
cd app
chmod +x run_dev.sh
./run_dev.sh
```

## Architecture

| File | Purpose |
|------|---------|
| `claude_usage_monitor.py` | Main menu bar app (rumps). Handles display, refresh timers, notifications, menu construction. |
| `weblogin.py` | Data layer. Executes JavaScript in Chrome via AppleScript to fetch usage data from claude.ai API. |
| `generate_icon.py` | Generates `.icns` app icon (dark square, green battery, yellow lightning bolt) at all required sizes. |
| `install.sh` | Build and install script. Creates `.app` bundle, LaunchAgent, venv. |
| `run_dev.sh` | Development launcher. Runs from source with a local venv. |
| `requirements.txt` | Python dependencies. |

## Key Technical Decisions

- **No threading.** PyObjC segfaults when Cocoa UI elements are modified from background threads. All work (including the ~1s AppleScript call to Chrome) runs on the main thread via rumps timer callbacks.
- **AppleScript JS execution** instead of HTTP requests with extracted cookies. Chrome's session cookies for claude.ai are in-memory only (not in the SQLite cookie store), so `browser_cookie3` and similar tools can't read them. Running JS inside Chrome sidesteps this entirely.
- **Shell-wrapper .app bundle** instead of py2app. py2app consistently fails with `Abort trap: 6` on Python 3.9. The shell wrapper approach (a minimal `.app` whose launcher script activates a venv and runs the Python script) is reliable.
- **`NSApplicationActivationPolicyAccessory`** at Python startup to suppress the Dock icon and app switcher entry, since `LSUIElement=true` in Info.plist alone doesn't prevent the Python process icon from appearing.
- **LaunchAgent** for auto-start instead of `osascript` login items, which proved unreliable.

## Design Constants

```
Background:  #1E1E23 (dark square)
Battery:     #28D158 (green fill)
Lightning:   #FFCC00 (yellow bolt)
Outline:     #DCDCE6 (light gray)
```
