#!/usr/bin/env python3
"""
Claude Usage Monitor — macOS Menu Bar App
Shows Claude usage as a battery indicator with real-time data from claude.ai.

Data source:
  Runs JavaScript directly in a Chrome tab on claude.ai via AppleScript.
  Chrome handles all auth automatically — no cookie extraction needed.

Just have Chrome open with claude.ai and this app does the rest.
"""

# Fix for PyObjC + forked threads in .app bundles
import os
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")

import sys
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import rumps

# ─── Force accessory/agent mode (no dock icon, no app switcher) ──────
try:
    from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
    NSApplication.sharedApplication().setActivationPolicy_(
        NSApplicationActivationPolicyAccessory
    )
except Exception:
    pass  # fallback: LSUIElement in Info.plist handles it

# ─── Try optional imports ────────────────────────────────────────────
try:
    from PIL import Image, ImageDraw
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

try:
    import requests as req_lib
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


# ═════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═════════════════════════════════════════════════════════════════════

CONFIG_DIR = os.path.expanduser("~/.claude-usage-monitor")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
ICON_CACHE_DIR = os.path.join(CONFIG_DIR, "icons")

KEYRING_SERVICE = "claude-usage-monitor"
KEYRING_COOKIE_KEY = "session-cookie"

API_BASE = "https://claude.ai/api"

# How often to refresh data (seconds)
REFRESH_INTERVAL = 120  # 2 minutes — be gentle to the API

# Notification thresholds (percent used)
NOTIFY_WARNING = 80
NOTIFY_CRITICAL = 95


# ═════════════════════════════════════════════════════════════════════
# CONFIGURATION MANAGER
# ═════════════════════════════════════════════════════════════════════

def load_config():
    """Load or create configuration."""
    defaults = {
        "refresh_interval": REFRESH_INTERVAL,
        "notify_warning": NOTIFY_WARNING,
        "notify_critical": NOTIFY_CRITICAL,
        "notifications_enabled": True,
        "show_percentage_in_menubar": True,
        "last_notification_id": None,
        "cowork_dir": "~/Documents/Claude Cowork",
    }
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            saved.pop("plan", None)
            defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_config(config):
    """Persist configuration to disk."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


# ═════════════════════════════════════════════════════════════════════
# ICON GENERATOR
# ═════════════════════════════════════════════════════════════════════

def _color_for_pct(pct_remaining):
    """Return (r,g,b) for a given percentage remaining."""
    if pct_remaining > 50:
        return (48, 209, 88)       # green
    elif pct_remaining > 25:
        return (255, 214, 10)      # yellow
    elif pct_remaining > 10:
        return (255, 149, 0)       # orange
    else:
        return (255, 69, 58)       # red


def generate_battery_icon(pct_remaining, size=(44, 22)):
    """Generate a battery icon PNG. Returns path to a cached PNG file."""
    if not HAS_PILLOW:
        return None

    os.makedirs(ICON_CACHE_DIR, exist_ok=True)
    bucket = max(0, min(100, int(pct_remaining) // 2 * 2))
    cache_path = os.path.join(ICON_CACHE_DIR, f"battery_{bucket}.png")
    if os.path.exists(cache_path):
        return cache_path

    w, h = size
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Battery body outline
    body_left, body_top = 0, 2
    body_right, body_bottom = w - 5, h - 2
    draw.rounded_rectangle(
        [body_left, body_top, body_right, body_bottom],
        radius=3, outline=(220, 220, 220, 255), width=2,
    )

    # Battery cap
    cap_left = body_right + 1
    cap_top = h // 2 - 3
    cap_bottom = h // 2 + 3
    draw.rounded_rectangle(
        [cap_left, cap_top, cap_left + 3, cap_bottom],
        radius=1, fill=(220, 220, 220, 255),
    )

    # Fill bar
    fill_pct = max(0, min(100, pct_remaining)) / 100.0
    inner_left = body_left + 3
    inner_top = body_top + 3
    inner_right = body_right - 2
    inner_bottom = body_bottom - 3
    fill_width = int((inner_right - inner_left) * fill_pct)
    if fill_width > 0:
        color = _color_for_pct(pct_remaining)
        draw.rounded_rectangle(
            [inner_left, inner_top, inner_left + fill_width, inner_bottom],
            radius=1, fill=(*color, 255),
        )

    img.save(cache_path, "PNG")
    return cache_path


# ═════════════════════════════════════════════════════════════════════
# DATA FETCHING — via Chrome AppleScript
# ═════════════════════════════════════════════════════════════════════

try:
    _weblogin_dir = os.path.dirname(os.path.abspath(__file__))
    if _weblogin_dir not in sys.path:
        sys.path.insert(0, _weblogin_dir)
    from weblogin import (
        chrome_fetch_usage,
        chrome_is_available,
        save_manual_cookie,
        get_cookie_header,
        get_org_uuid,
        clear_cookie,
    )
    HAS_WEBLOGIN = True
except ImportError:
    HAS_WEBLOGIN = False

    def chrome_fetch_usage():
        return None

    def chrome_is_available():
        return False

    def save_manual_cookie(s):
        pass

    def get_cookie_header():
        return None

    def get_org_uuid():
        return None

    def clear_cookie():
        pass


def fetch_usage():
    """
    Fetch usage data from claude.ai.

    Primary method: Execute JavaScript in Chrome via AppleScript.
    Fallback: Use manually pasted Cookie header + requests library.

    Returns parsed API response dict or None.
    """
    # Method 1: Chrome JS (preferred — handles all cookie types)
    data = chrome_fetch_usage()
    if data:
        return {
            "five_hour": data.get("five_hour"),
            "seven_day": data.get("seven_day"),
            "extra_usage": data.get("extra_usage"),
            "raw": data,
        }

    # Method 2: Manual cookie + requests (fallback)
    if HAS_REQUESTS:
        cookie_header = get_cookie_header()
        org_uuid = get_org_uuid()
        if cookie_header and org_uuid:
            try:
                url = f"{API_BASE}/organizations/{org_uuid}/usage"
                headers = {
                    "Cookie": cookie_header,
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                    "Accept": "application/json",
                }
                resp = req_lib.get(url, headers=headers, timeout=15)
                if resp.ok:
                    rdata = resp.json()
                    return {
                        "five_hour": rdata.get("five_hour"),
                        "seven_day": rdata.get("seven_day"),
                        "extra_usage": rdata.get("extra_usage"),
                        "raw": rdata,
                    }
                print(f"[UsageAPI] HTTP {resp.status_code}", file=sys.stderr)
            except Exception as e:
                print(f"[UsageAPI] Fallback error: {e}", file=sys.stderr)

    return None


# ═════════════════════════════════════════════════════════════════════
# NOTIFICATIONS
# ═════════════════════════════════════════════════════════════════════

def send_notification(title, message, sound=True):
    """Send a macOS notification from the app itself (not osascript)."""
    try:
        rumps.notification(
            title=title,
            subtitle="",
            message=message,
            sound=sound,
        )
    except Exception:
        pass


# ═════════════════════════════════════════════════════════════════════
# GIT REPO STATUS
# ═════════════════════════════════════════════════════════════════════

def scan_git_repos(base_dir):
    """Scan a directory for git repos, including one level of nesting.

    Returns list of (display_name, abs_path) sorted by display_name.
    E.g. [("claude-usage-monitor", "/path/to/it"),
          ("claude-usage-monitor/agent-toolkit", "/path/to/it/agent-toolkit")]
    """
    base = Path(base_dir).expanduser()
    if not base.is_dir():
        return []

    repos = []
    for child in sorted(base.iterdir()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        # Check if this directory is a git repo
        if (child / ".git").exists():
            repos.append((child.name, str(child)))
            # Check one level deeper for nested repos (e.g. agent-toolkit/)
            for grandchild in sorted(child.iterdir()):
                if not grandchild.is_dir() or grandchild.name.startswith("."):
                    continue
                if (grandchild / ".git").exists():
                    display = f"{child.name}/{grandchild.name}"
                    repos.append((display, str(grandchild)))
    return repos


def get_repo_status(repo_path):
    """Get git sync status for a repo.

    Returns dict with keys:
      branch: str - current branch name
      ahead: int - commits ahead of remote
      behind: int - commits behind remote
      dirty: bool - has uncommitted changes
      has_remote: bool - has a tracking branch
      error: str|None - error message if git commands failed
    """
    result = {
        "branch": "unknown",
        "ahead": 0,
        "behind": 0,
        "dirty": False,
        "has_remote": False,
        "error": None,
    }

    try:
        # Fetch from remote (timeout to avoid blocking UI)
        subprocess.run(
            ["git", "-C", repo_path, "fetch", "--quiet"],
            timeout=5, capture_output=True,
        )
    except subprocess.TimeoutExpired:
        print(f"[Git] Fetch timeout for {repo_path}", file=sys.stderr)
    except Exception as e:
        print(f"[Git] Fetch error for {repo_path}: {e}", file=sys.stderr)

    try:
        # Get branch + ahead/behind + porcelain status
        proc = subprocess.run(
            ["git", "-C", repo_path, "status", "--porcelain=v1", "--branch"],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0:
            result["error"] = proc.stderr.strip() or "git status failed"
            return result

        lines = proc.stdout.strip().split("\n")
        if not lines:
            return result

        # Parse branch line: ## main...origin/main [ahead 3, behind 2]
        branch_line = lines[0]
        branch_match = re.match(r"## (\S+?)(?:\.\.\.(\S+))?(?:\s+\[(.+)\])?$", branch_line)
        if branch_match:
            result["branch"] = branch_match.group(1)
            result["has_remote"] = branch_match.group(2) is not None
            tracking_info = branch_match.group(3) or ""
            ahead_m = re.search(r"ahead (\d+)", tracking_info)
            behind_m = re.search(r"behind (\d+)", tracking_info)
            if ahead_m:
                result["ahead"] = int(ahead_m.group(1))
            if behind_m:
                result["behind"] = int(behind_m.group(1))

        # Any non-branch lines = dirty working tree
        status_lines = [l for l in lines[1:] if l.strip()]
        result["dirty"] = len(status_lines) > 0

    except subprocess.TimeoutExpired:
        result["error"] = "timeout"
    except Exception as e:
        result["error"] = str(e)

    return result


def format_repo_status(status):
    """Format a repo status dict into a display string.

    Returns (icon, description) tuple.
    """
    if status.get("error"):
        return "⚠️", "error"

    if not status["has_remote"]:
        return "—", "no remote"

    parts = []
    icon = "✓"

    if status["ahead"] > 0 and status["behind"] > 0:
        icon = "⬆⬇"
        parts.append("diverged")
    elif status["ahead"] > 0:
        icon = "⬆"
        parts.append(f"{status['ahead']} ahead")
    elif status["behind"] > 0:
        icon = "⬇"
        parts.append(f"{status['behind']} behind")

    if status["dirty"]:
        if icon == "✓":
            icon = "✎"
        parts.append("uncommitted")

    if not parts:
        parts.append("in sync")

    return icon, ", ".join(parts)


# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _fmt_time_until(iso_str):
    """Format an ISO-8601 reset timestamp as '3h 42m' from now."""
    if not iso_str:
        return "unknown"
    try:
        ts_str = iso_str.replace("Z", "+00:00")
        reset_dt = datetime.fromisoformat(ts_str)
        now = datetime.now(timezone.utc)
        delta = reset_dt - now
        total_secs = int(delta.total_seconds())
        if total_secs <= 0:
            return "now"
        hours = total_secs // 3600
        minutes = (total_secs % 3600) // 60
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        return f"{minutes}m"
    except Exception:
        return "unknown"


def _bar_chars(pct_used, width=20):
    """Generate a text progress bar."""
    filled = int((pct_used / 100) * width)
    empty = width - filled
    return "\u2588" * filled + "\u2591" * empty


def _status_emoji(pct):
    """Return a status emoji for a utilization percentage."""
    if pct >= 95:
        return "\U0001F6AB"
    elif pct >= 80:
        return "\u26A0\uFE0F"
    elif pct >= 50:
        return "\U0001F7E1"
    else:
        return "\U0001F7E2"


# ═════════════════════════════════════════════════════════════════════
# MENU BAR APP
# ═════════════════════════════════════════════════════════════════════

class ClaudeUsageMonitor(rumps.App):
    """macOS menu bar app for monitoring Claude usage.

    IMPORTANT: All UI updates happen on the main thread (via rumps timer
    callbacks). Never update self.title or menu items from a background
    thread — that causes PyObjC segfaults.
    """

    def __init__(self):
        super().__init__(
            name="Claude Usage Monitor",
            title="\u26A1 ---%",
            quit_button=None,
        )

        self.config = load_config()
        self.usage_data = None
        self._notified_warning = False
        self._notified_critical = False
        self._notified_budget_warning = False
        self._notified_budget_critical = False
        self._icon_path = None
        self._last_error = None
        self._needs_refresh = True  # trigger initial load

        # Build initial menu
        self._build_menu()
        print("[Main] App initialized, waiting for first timer tick...")

    def _build_menu(self):
        """Construct the dropdown menu structure."""
        self.menu.clear()

        # Header
        self.menu.add(rumps.MenuItem("Claude Usage Monitor", callback=None))
        self.menu.add(rumps.separator)

        # 5-Hour Limit
        self._five_hour_header = rumps.MenuItem("5-Hour Limit: ---")
        self._five_hour_bar = rumps.MenuItem("-" * 20)
        self._five_hour_reset = rumps.MenuItem("  Resets in: ---")
        self.menu.add(self._five_hour_header)
        self.menu.add(self._five_hour_bar)
        self.menu.add(self._five_hour_reset)
        self.menu.add(rumps.separator)

        # 7-Day Limit
        self._seven_day_header = rumps.MenuItem("7-Day Limit: ---")
        self._seven_day_bar = rumps.MenuItem("-" * 20)
        self._seven_day_reset = rumps.MenuItem("  Resets in: ---")
        self.menu.add(self._seven_day_header)
        self.menu.add(self._seven_day_bar)
        self.menu.add(self._seven_day_reset)
        self.menu.add(rumps.separator)

        # Extra Usage
        self._extra_header = rumps.MenuItem("Extra Usage: ---")
        self._extra_bar = rumps.MenuItem("-" * 20)
        self._extra_detail = rumps.MenuItem("  ---")
        self.menu.add(self._extra_header)
        self.menu.add(self._extra_bar)
        self.menu.add(self._extra_detail)
        self.menu.add(rumps.separator)

        # Git Repos — pre-allocate slots with unique keys (rumps uses title as dict key)
        MAX_GIT_SLOTS = 12
        self._git_header = rumps.MenuItem("🔀 Agent Repos")
        self.menu.add(self._git_header)
        self._git_slots = []
        for i in range(MAX_GIT_SLOTS):
            # Each slot needs a unique title to avoid key collisions in rumps OrderedDict
            placeholder = "  Scanning..." if i == 0 else f"  \u200b{'.' * i}"
            item = rumps.MenuItem(placeholder)
            self._git_slots.append(item)
            self.menu.add(item)
        self.menu.add(rumps.separator)

        # Status line
        self._status_item = rumps.MenuItem("Status: Starting up...")
        self.menu.add(self._status_item)
        self.menu.add(rumps.separator)

        # Actions
        self.menu.add(rumps.MenuItem("\u21BB  Refresh Now", callback=self._on_refresh))
        self.menu.add(rumps.MenuItem(
            "\U0001F310  Open Usage in Browser",
            callback=self._on_open_browser,
        ))

        # Fallback auth
        cookie_menu = rumps.MenuItem("\U0001F511  Auth Settings")
        cookie_menu.add(rumps.MenuItem(
            "Paste cookie manually...",
            callback=self._on_paste_cookie,
        ))
        cookie_menu.add(rumps.MenuItem(
            "Clear stored cookie",
            callback=self._on_clear_cookie,
        ))
        self.menu.add(cookie_menu)
        self.menu.add(rumps.separator)

        # Settings
        notif_state = "On" if self.config.get("notifications_enabled") else "Off"
        self.menu.add(rumps.MenuItem(
            f"Notifications: {notif_state}",
            callback=self._on_toggle_notifications,
        ))
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("Quit Claude Usage Monitor", callback=self._on_quit))

    # ─── Data refresh (ALL ON MAIN THREAD) ────────────────────────────

    def _refresh_data(self):
        """Fetch usage from claude.ai API and update display.
        This runs on the MAIN THREAD — safe to update UI directly.
        """
        try:
            print("[Refresh] Fetching usage data...")
            data = fetch_usage()
            if data:
                # Log raw API response for debugging
                raw = data.get("raw", {})
                print(f"[Refresh] Raw API keys: {list(raw.keys())}")
                print(f"[Refresh] Raw API response: {json.dumps(raw, indent=2, default=str)}")
                self.usage_data = data
                self._last_error = None
                self._update_display()
                self._check_notifications()
                print("[Refresh] Display updated successfully")
            else:
                self._last_error = "Could not fetch usage data"
                self._update_error_display()
                print("[Refresh] No data returned")
        except Exception as e:
            self._last_error = str(e)
            self._update_error_display()
            print(f"[Refresh] Error: {e}", file=sys.stderr)

        # Git repo status (runs regardless of usage data success)
        try:
            self._refresh_git_status()
        except Exception as e:
            print(f"[Git] Error refreshing status: {e}", file=sys.stderr)

    def _update_display(self):
        """Update menu bar title and dropdown items with live data.

        The menu bar battery reflects rate limits only (5-hour and 7-day).
        Extra usage is a spending budget — shown separately, not in the battery.
        """
        if not self.usage_data:
            return

        worst_rate_pct = 0  # Only rate limits, not spending

        # Five-hour limit
        five = self.usage_data.get("five_hour")
        if five and five.get("utilization") is not None:
            pct = five["utilization"]
            worst_rate_pct = max(worst_rate_pct, pct)
            marker = _status_emoji(pct)
            self._five_hour_header.title = f"{marker} 5-Hour Limit: {pct:.0f}% used"
            self._five_hour_bar.title = _bar_chars(pct)
            reset_at = five.get("resets_at")
            if pct == 0 and not reset_at:
                self._five_hour_reset.title = "  Not limited"
            else:
                self._five_hour_reset.title = f"  Resets in {_fmt_time_until(reset_at)}"
        else:
            self._five_hour_header.title = "5-Hour Limit: N/A"
            self._five_hour_bar.title = "-" * 20
            self._five_hour_reset.title = "  Not applicable"

        # Seven-day limit
        seven = self.usage_data.get("seven_day")
        if seven and seven.get("utilization") is not None:
            pct = seven["utilization"]
            worst_rate_pct = max(worst_rate_pct, pct)
            marker = _status_emoji(pct)
            self._seven_day_header.title = f"{marker} 7-Day Limit: {pct:.0f}% used"
            self._seven_day_bar.title = _bar_chars(pct)
            reset_at = seven.get("resets_at")
            if pct == 0 and not reset_at:
                self._seven_day_reset.title = "  Not limited"
            else:
                self._seven_day_reset.title = f"  Resets in {_fmt_time_until(reset_at)}"
        else:
            self._seven_day_header.title = "7-Day Limit: N/A"
            self._seven_day_bar.title = "-" * 20
            self._seven_day_reset.title = "  Not applicable"

        # Extra usage (spending budget — separate from rate limits)
        extra = self.usage_data.get("extra_usage")
        if extra and extra.get("is_enabled"):
            pct = extra.get("utilization", 0)
            used = extra.get("used_credits", 0)
            limit = extra.get("monthly_limit", 0)
            marker = _status_emoji(pct)
            self._extra_header.title = f"{marker} Extra Usage: {pct:.1f}%"
            self._extra_bar.title = _bar_chars(pct)
            self._extra_detail.title = f"  ${used/100:.2f} / ${limit/100:.2f} this month"
        else:
            self._extra_header.title = "Extra Usage: Disabled"
            self._extra_bar.title = "-" * 20
            self._extra_detail.title = "  Not enabled on this plan"

        # Menu bar title — battery reflects RATE LIMITS only, not spending
        pct_remaining = max(0, 100 - worst_rate_pct)
        if self.config.get("show_percentage_in_menubar", True):
            self.title = f"\u26A1 {int(pct_remaining)}%"
        else:
            self.title = "\u26A1"

        # Icon
        icon_path = generate_battery_icon(pct_remaining)
        if icon_path and icon_path != self._icon_path:
            try:
                self.icon = icon_path
                self._icon_path = icon_path
            except Exception:
                pass

        # Status
        now = datetime.now().strftime("%H:%M")
        self._status_item.title = f"Last updated: {now}"

    def _update_error_display(self):
        """Show error state in menu bar."""
        self.title = "\u26A1 ---"
        self._status_item.title = "\u26A0\uFE0F Open claude.ai in Chrome to connect"

    def _check_notifications(self):
        """Send notifications if thresholds are crossed."""
        if not self.config.get("notifications_enabled"):
            return
        if not self.usage_data:
            return

        # Rate limit notifications (5-hour)
        five = self.usage_data.get("five_hour")
        if five and five.get("utilization") is not None:
            pct = five["utilization"]

            if pct >= self.config.get("notify_critical", 95) and not self._notified_critical:
                self._notified_critical = True
                reset_in = _fmt_time_until(five.get("resets_at"))
                send_notification(
                    "Claude Rate Limit Critical",
                    f"5-hour limit at {pct:.0f}%. Resets in {reset_in}.",
                )
            elif pct >= self.config.get("notify_warning", 80) and not self._notified_warning:
                self._notified_warning = True
                reset_in = _fmt_time_until(five.get("resets_at"))
                send_notification(
                    "Claude Rate Limit Warning",
                    f"5-hour limit at {pct:.0f}%. Resets in {reset_in}.",
                )

        # Budget notifications (extra usage spending)
        extra = self.usage_data.get("extra_usage")
        if extra and extra.get("is_enabled"):
            budget_pct = extra.get("utilization", 0)
            used = extra.get("used_credits", 0)
            limit = extra.get("monthly_limit", 0)

            if budget_pct >= 95 and not self._notified_budget_critical:
                self._notified_budget_critical = True
                send_notification(
                    "Claude Budget Almost Exhausted",
                    f"Extra usage at ${used/100:.2f} / ${limit/100:.2f} ({budget_pct:.0f}%).",
                )
            elif budget_pct >= 80 and not self._notified_budget_warning:
                self._notified_budget_warning = True
                send_notification(
                    "Claude Budget Warning",
                    f"Extra usage at ${used/100:.2f} / ${limit/100:.2f} ({budget_pct:.0f}%).",
                )

    # ─── Git repo status ────────────────────────────────────────────

    def _refresh_git_status(self):
        """Scan cowork directory and update git repo status slots."""
        cowork_dir = self.config.get("cowork_dir", "~/Documents/Claude Cowork")
        print(f"[Git] Scanning {cowork_dir}...")

        try:
            repos = scan_git_repos(cowork_dir)
            print(f"[Git] Found {len(repos)} repos: {[r[0] for r in repos]}")
        except Exception as e:
            print(f"[Git] Scan error: {e}", file=sys.stderr)
            self._git_slots[0].title = f"  ⚠️ Scan error"
            return

        if not repos:
            self._git_slots[0].title = "  No repos found"
            for i, slot in enumerate(self._git_slots[1:], start=1):
                slot.title = f"  \u200b{chr(0x200c + i)}"  # unique invisible
            return

        for i, slot in enumerate(self._git_slots):
            if i < len(repos):
                display_name, repo_path = repos[i]
                try:
                    status = get_repo_status(repo_path)
                    icon, desc = format_repo_status(status)
                    slot.title = f"  {icon}  {display_name} — {desc}"
                    print(f"[Git]   {icon} {display_name}: {desc}")
                except Exception as e:
                    slot.title = f"  ⚠️  {display_name} — error"
                    print(f"[Git]   Error for {display_name}: {e}", file=sys.stderr)
            else:
                # Unique invisible placeholder — avoids rumps key collisions
                slot.title = f"  \u200b{chr(0x200c + i)}"

        print(f"[Git] Updated {len(repos)} repos")

    # ─── Timer callback (MAIN THREAD) ────────────────────────────────

    @rumps.timer(5)
    def _initial_load(self, timer):
        """Fire once after app starts to do the first data load."""
        timer.stop()
        print("[Timer] Initial load triggered")
        self._refresh_data()

    @rumps.timer(REFRESH_INTERVAL)
    def _auto_refresh(self, _):
        """Periodic refresh — runs on main thread, safe for UI updates."""
        self._refresh_data()

    # ─── Menu callbacks ──────────────────────────────────────────────

    def _on_refresh(self, _):
        self._notified_warning = False
        self._notified_critical = False
        self._notified_budget_warning = False
        self._notified_budget_critical = False
        self._refresh_data()

    def _on_open_browser(self, _):
        subprocess.Popen(["open", "https://claude.ai/settings/usage"])

    def _on_paste_cookie(self, _):
        """Show a dialog for the user to paste their cookie string."""
        response = rumps.Window(
            title="Paste Session Cookie",
            message=(
                "1. Open claude.ai in your browser (make sure you're logged in)\n"
                "2. Open DevTools (Cmd+Opt+I) > Network tab\n"
                "3. Reload the page and click any request to claude.ai\n"
                "4. In the Headers tab, find 'Cookie:' and copy the full value\n"
                "5. Paste it below:"
            ),
            default_text="",
            ok="Save",
            cancel="Cancel",
            dimensions=(420, 80),
        ).run()

        if response.clicked and response.text.strip():
            cookie_text = response.text.strip()
            save_manual_cookie(cookie_text)
            send_notification(
                "Cookie Saved",
                "Session cookie stored. Refreshing usage data...",
                sound=False,
            )
            self._refresh_data()

    def _on_clear_cookie(self, _):
        """Remove the stored session cookie."""
        clear_cookie()
        self.usage_data = None
        self.title = "\u26A1 ---"
        self._status_item.title = "Cookie cleared"
        rumps.alert(
            title="Cookie Cleared",
            message="Stored session cookie has been removed.",
        )

    def _on_toggle_notifications(self, sender):
        self.config["notifications_enabled"] = not self.config.get("notifications_enabled", True)
        save_config(self.config)
        notif_state = "On" if self.config["notifications_enabled"] else "Off"
        sender.title = f"Notifications: {notif_state}"

    def _on_quit(self, _):
        rumps.quit_application()


# ═════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════

def main():
    # Enable faulthandler for segfault tracebacks
    import faulthandler
    log_dir = os.path.expanduser("~/.claude-usage-monitor")
    os.makedirs(log_dir, exist_ok=True)
    crash_log = open(os.path.join(log_dir, "crash.log"), "w")
    faulthandler.enable(file=crash_log)

    try:
        print("[Main] Creating ClaudeUsageMonitor app...")
        app = ClaudeUsageMonitor()
        print("[Main] Starting rumps event loop...")
        app.run()
    except Exception as e:
        import traceback
        msg = traceback.format_exc()
        print(f"[Main] FATAL: {msg}", file=sys.stderr)
        with open(os.path.join(log_dir, "crash.log"), "a") as f:
            f.write(f"\n=== Python exception at {datetime.now()} ===\n")
            f.write(msg)
        raise


if __name__ == "__main__":
    main()
