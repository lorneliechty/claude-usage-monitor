#!/usr/bin/env python3
"""Debug: list all claude.ai cookies from Chrome to find org UUID."""

import browser_cookie3
from urllib.parse import unquote

print("=== All claude.ai cookies from Chrome ===\n")

try:
    cj = browser_cookie3.chrome(domain_name="claude.ai")
    cookies = []
    for c in cj:
        if "claude.ai" in (c.domain or ""):
            cookies.append(c)
            val_preview = c.value[:80] + "..." if len(c.value) > 80 else c.value
            print(f"  name={c.name}")
            print(f"    domain={c.domain}  path={c.path}")
            print(f"    value={val_preview}")
            print()

    print(f"Total: {len(cookies)} cookies\n")

    # Look for org UUID in various places
    print("=== Searching for org UUID ===\n")

    for c in cookies:
        name_lower = c.name.lower()
        # Check obvious names
        if "org" in name_lower or "active" in name_lower or "uuid" in name_lower:
            print(f"  POTENTIAL ORG COOKIE: {c.name} = {unquote(c.value)[:120]}")

    # Also check if it's embedded in any cookie value
    print("\n=== Checking all cookie values for UUID pattern ===\n")
    import re
    uuid_pattern = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', re.I)
    for c in cookies:
        decoded = unquote(c.value)
        matches = uuid_pattern.findall(decoded)
        if matches:
            print(f"  {c.name} contains UUID(s): {matches}")

except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
