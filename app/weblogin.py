#!/usr/bin/env python3
"""
Cookie & API access for Claude Usage Monitor.

Primary strategy: Run JavaScript directly in a Chrome tab that's already
on claude.ai via AppleScript. This bypasses all cookie extraction issues
because Chrome handles auth (including httpOnly/session cookies) automatically.

Fallback: manual Cookie header paste via a rumps dialog.
"""

import json
import os
import subprocess
import sys
from urllib.parse import unquote

CONFIG_DIR = os.path.expanduser("~/.claude-usage-monitor")
COOKIE_FILE = os.path.join(CONFIG_DIR, ".session_cookie")

# ── Keyring (optional) ──
try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False

KEYRING_SERVICE = "claude-usage-monitor"
KEYRING_COOKIE_KEY = "session-cookie"


# ═════════════════════════════════════════════════════════════════════
# CHROME JAVASCRIPT EXECUTION (primary method)
# ═════════════════════════════════════════════════════════════════════

def _chrome_js(js_code):
    """
    Execute JavaScript in a Chrome tab that's on claude.ai.
    Returns the string result, or None on failure.

    Uses AppleScript to control Chrome — user will get a one-time
    macOS permission prompt ("wants to control Google Chrome").
    """
    # Escape for AppleScript double-quoted string
    escaped = js_code.replace("\\", "\\\\").replace('"', '\\"')

    applescript = f'''
tell application "Google Chrome"
    set resultText to "ERROR:NO_CLAUDE_TAB"
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t starts with "https://claude.ai" then
                set resultText to (execute t javascript "{escaped}")
                return resultText
            end if
        end repeat
    end repeat
    return resultText
end tell
'''
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True, text=True, timeout=15,
        )
        output = result.stdout.strip()
        if result.returncode != 0 or "ERROR:" in output:
            err = result.stderr.strip() or output
            print(f"[ChromeJS] AppleScript error: {err}", file=sys.stderr)
            return None
        return output
    except subprocess.TimeoutExpired:
        print("[ChromeJS] AppleScript timed out", file=sys.stderr)
        return None
    except Exception as e:
        print(f"[ChromeJS] Error: {e}", file=sys.stderr)
        return None


def chrome_fetch_usage():
    """
    Fetch usage data by running JS directly in Chrome.
    Returns parsed JSON dict or None.
    """
    # Step 1: Get org UUID from cookies (via document.cookie)
    org_uuid = _chrome_js(
        "(document.cookie.match(/lastActiveOrg=([^;]+)/)||[])[1]||''"
    )

    if not org_uuid:
        print("[ChromeJS] lastActiveOrg not in document.cookie, trying /api/organizations", file=sys.stderr)
        # Fallback: call the organizations endpoint
        orgs_json = _chrome_js(
            "(function(){"
            "var x=new XMLHttpRequest();"
            "x.open('GET','/api/organizations',false);"
            "x.send();"
            "return x.responseText"
            "})()"
        )
        if orgs_json:
            try:
                orgs = json.loads(orgs_json)
                if isinstance(orgs, list) and len(orgs) > 0:
                    org_uuid = orgs[0].get("uuid") or orgs[0].get("id")
                    print(f"[ChromeJS] Got org UUID from /api/organizations: {org_uuid}", file=sys.stderr)
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                print(f"[ChromeJS] Failed to parse orgs: {e}", file=sys.stderr)

    if not org_uuid:
        print("[ChromeJS] Could not determine org UUID", file=sys.stderr)
        return None

    # Clean up the UUID
    org_uuid = unquote(org_uuid).strip().strip('"')
    print(f"[ChromeJS] Org UUID: {org_uuid}", file=sys.stderr)

    # Step 2: Fetch usage via synchronous XHR
    usage_json = _chrome_js(
        "(function(){"
        "var x=new XMLHttpRequest();"
        f"x.open('GET','/api/organizations/{org_uuid}/usage',false);"
        "x.send();"
        "return x.responseText"
        "})()"
    )

    if not usage_json:
        print("[ChromeJS] No response from usage API", file=sys.stderr)
        return None

    try:
        data = json.loads(usage_json)
        print(f"[ChromeJS] Got usage data: five_hour={data.get('five_hour', {}).get('utilization', 'N/A')}%", file=sys.stderr)
        return data
    except json.JSONDecodeError as e:
        print(f"[ChromeJS] Failed to parse usage JSON: {e}", file=sys.stderr)
        print(f"[ChromeJS] Raw response: {usage_json[:200]}", file=sys.stderr)
        return None


def chrome_is_available():
    """Check if Chrome is running with a claude.ai tab."""
    result = _chrome_js("'ok'")
    return result == "ok"


# ═════════════════════════════════════════════════════════════════════
# MANUAL COOKIE STORAGE (fallback method)
# ═════════════════════════════════════════════════════════════════════

def _save_cookie(value):
    """Store cookie string securely."""
    if HAS_KEYRING:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_COOKIE_KEY, value)
            return True
        except Exception:
            pass
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(COOKIE_FILE, "w") as f:
        f.write(value)
    os.chmod(COOKIE_FILE, 0o600)
    return True


def _load_cookie():
    """Load stored cookie string."""
    if HAS_KEYRING:
        try:
            val = keyring.get_password(KEYRING_SERVICE, KEYRING_COOKIE_KEY)
            if val:
                return val
        except Exception:
            pass
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE) as f:
            return f.read().strip()
    return None


def get_cookie_header():
    """Get stored Cookie header string (from manual paste)."""
    raw = _load_cookie()
    if raw:
        try:
            data = json.loads(raw)
            return data.get("cookie_header", raw)
        except (json.JSONDecodeError, AttributeError):
            return raw
    return None


def get_org_uuid():
    """Get org UUID from stored cookie data."""
    raw = _load_cookie()
    if raw:
        try:
            data = json.loads(raw)
            return data.get("org_uuid")
        except (json.JSONDecodeError, AttributeError):
            pass
    return None


def save_manual_cookie(cookie_header_str):
    """Save a manually pasted Cookie header string."""
    header = cookie_header_str.strip()
    org_uuid = None
    for part in header.split(";"):
        part = part.strip()
        if part.startswith("lastActiveOrg="):
            org_uuid = unquote(part.split("=", 1)[1]).strip().strip('"')
            break
    payload = json.dumps({
        "cookie_header": header,
        "org_uuid": org_uuid,
        "source": "manual_paste",
    })
    _save_cookie(payload)


def clear_cookie():
    """Remove stored cookie."""
    if HAS_KEYRING:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_COOKIE_KEY)
        except Exception:
            pass
    if os.path.exists(COOKIE_FILE):
        os.unlink(COOKIE_FILE)


# Legacy compat stubs
def auto_extract_and_save():
    return False, None, ""


# ═════════════════════════════════════════════════════════════════════
# CLI TEST
# ═════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=== Chrome JS Method ===\n")

    if chrome_is_available():
        print("Chrome is running with a claude.ai tab!")
        data = chrome_fetch_usage()
        if data:
            five = data.get("five_hour", {})
            seven = data.get("seven_day", {})
            extra = data.get("extra_usage", {})
            print(f"\n  5-hour:  {five.get('utilization', 'N/A')}% used")
            if five.get("resets_at"):
                print(f"           resets at {five['resets_at']}")
            print(f"  7-day:   {seven.get('utilization', 'N/A')}% used")
            if seven.get("resets_at"):
                print(f"           resets at {seven['resets_at']}")
            if extra and extra.get("is_enabled"):
                used = extra.get("used_credits", 0)
                limit = extra.get("monthly_limit", 0)
                print(f"  Extra:   {extra.get('utilization', 0):.1f}% "
                      f"(${used/100:.2f} / ${limit/100:.2f})")
            print(f"\n  Full keys: {list(data.keys())}")
        else:
            print("  Failed to fetch usage data.")
    else:
        print("Chrome is not running or no claude.ai tab is open.")
        print("Please open https://claude.ai in Chrome and try again.")
