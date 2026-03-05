#!/usr/bin/env bash
#
# Diagnostic script — run this to help debug the monitor
#

BLUE='\033[0;34m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${BLUE}━━━ Claude Usage Monitor Diagnostics ━━━${NC}"
echo ""

# ── 1. Check config ──
echo -e "${YELLOW}1. Config${NC}"
if [ -f "$HOME/.claude-usage-monitor/config.json" ]; then
    echo "   $(cat "$HOME/.claude-usage-monitor/config.json")"
else
    echo "   No config file yet (using defaults)"
fi
echo ""

# ── 2. Check Python + dependencies ──
echo -e "${YELLOW}2. Python environment${NC}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
STABLE_VENV="$HOME/.claude-usage-monitor/.venv"

# Find venv
if [ -f "$VENV/bin/activate" ]; then
    source "$VENV/bin/activate"
    echo "   Using venv: $VENV"
elif [ -f "$STABLE_VENV/bin/activate" ]; then
    source "$STABLE_VENV/bin/activate"
    echo "   Using venv: $STABLE_VENV"
else
    echo "   No venv found, using system Python"
fi

python3 -c "
import sys
print(f'   Python: {sys.version}')

for mod in ['rumps', 'PIL', 'keyring', 'requests', 'browser_cookie3']:
    try:
        __import__(mod)
        print(f'   {mod}: ✅ installed')
    except ImportError:
        print(f'   {mod}: ❌ NOT installed')
" 2>&1
echo ""

# ── 3. Cookie extraction test ──
echo -e "${YELLOW}3. Cookie extraction${NC}"
python3 -c "
try:
    import browser_cookie3
except ImportError:
    print('   browser_cookie3: NOT installed — cannot extract cookies')
    import sys; sys.exit(0)

from urllib.parse import unquote

browsers = [
    ('Chrome',  browser_cookie3.chrome),
    ('Firefox', browser_cookie3.firefox),
    ('Safari',  browser_cookie3.safari),
    ('Brave',   browser_cookie3.brave),
]

org_uuid = None

for name, loader in browsers:
    try:
        cj = loader(domain_name='claude.ai')
        cookies = {c.name: c.value for c in cj if 'claude.ai' in (c.domain or '')}
        if cookies:
            names = list(cookies.keys())
            print(f'   {name}: ✅ {len(cookies)} cookies: {names[:8]}')
            if 'lastActiveOrg' in cookies:
                org_uuid = unquote(cookies['lastActiveOrg']).strip('\"')
                print(f'   → Org UUID: {org_uuid}')
        else:
            print(f'   {name}: ⚠️  accessible but no claude.ai cookies')
    except PermissionError as e:
        print(f'   {name}: 🔒 permission denied — {e}')
    except Exception as e:
        err = str(e)[:100]
        print(f'   {name}: ❌ {err}')
" 2>&1
echo ""

# ── 4. API connectivity test ──
echo -e "${YELLOW}4. API connectivity test${NC}"
python3 -c "
import sys
try:
    import requests
    import browser_cookie3
    from urllib.parse import unquote
except ImportError as e:
    print(f'   Missing dependency: {e}')
    sys.exit(0)

# Get cookies
org_uuid = None
cookie_header = None

for name, loader in [('Chrome', browser_cookie3.chrome), ('Safari', browser_cookie3.safari)]:
    try:
        cj = loader(domain_name='claude.ai')
        cookies = {}
        for c in cj:
            if 'claude.ai' in (c.domain or ''):
                cookies[c.name] = c.value
                if c.name == 'lastActiveOrg':
                    org_uuid = unquote(c.value).strip('\"')
        if cookies:
            cookie_header = '; '.join(f'{k}={v}' for k, v in cookies.items())
            break
    except:
        continue

if not cookie_header:
    print('   ❌ No cookies available')
    sys.exit(0)

if not org_uuid:
    print('   ❌ No lastActiveOrg cookie — cannot determine org UUID')
    sys.exit(0)

print(f'   Org UUID: {org_uuid}')
print(f'   Calling /api/organizations/{org_uuid}/usage ...')

try:
    resp = requests.get(
        f'https://claude.ai/api/organizations/{org_uuid}/usage',
        headers={
            'Cookie': cookie_header,
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)',
            'Accept': 'application/json',
        },
        timeout=15,
    )
    print(f'   HTTP {resp.status_code}')
    if resp.ok:
        data = resp.json()
        five = data.get('five_hour', {})
        seven = data.get('seven_day', {})
        extra = data.get('extra_usage', {})
        print(f'   ✅ 5-hour utilization:  {five.get(\"utilization\", \"N/A\")}%')
        print(f'   ✅ 7-day utilization:   {seven.get(\"utilization\", \"N/A\")}%')
        if extra and extra.get('is_enabled'):
            used = extra.get('used_credits', 0)
            limit = extra.get('monthly_limit', 0)
            print(f'   ✅ Extra usage: {extra.get(\"utilization\", 0):.1f}% (\${used/100:.2f} / \${limit/100:.2f})')
        print('   ')
        print(f'   Full response keys: {list(data.keys())}')
    else:
        print(f'   ❌ Error: {resp.text[:200]}')
except Exception as e:
    print(f'   ❌ Request failed: {e}')
" 2>&1

echo ""
echo -e "${BLUE}━━━ Done ━━━${NC}"
