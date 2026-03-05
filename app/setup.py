"""
py2app build configuration for Claude Usage Monitor.

Build with:
    python setup.py py2app

The resulting .app will be in dist/Claude Usage Monitor.app
"""

from setuptools import setup

APP = ["claude_usage_monitor.py"]
DATA_FILES = ["weblogin.py"]

OPTIONS = {
    "argv_emulation": False,
    "iconfile": None,  # Add a .icns file here if you have one
    "plist": {
        "CFBundleName": "Claude Usage Monitor",
        "CFBundleDisplayName": "Claude Usage Monitor",
        "CFBundleIdentifier": "com.claude.usage-monitor",
        "CFBundleVersion": "1.0.0",
        "CFBundleShortVersionString": "1.0.0",
        "LSUIElement": True,  # Hide from Dock (menu bar only)
        "NSHumanReadableCopyright": "MIT License",
        "LSMinimumSystemVersion": "12.0",
    },
    "packages": [
        "rumps",
        "PIL",
        "keyring",
        "requests",
        "browser_cookie3",
    ],
    "includes": [
        "json",
        "glob",
        "hashlib",
        "threading",
    ],
}

setup(
    name="Claude Usage Monitor",
    app=APP,
    data_files=[(".", DATA_FILES)],
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
