#!/usr/bin/env bash
#
# Claude Usage Monitor — Installer
#
# This script:
#   1. Creates a Python virtual environment
#   2. Installs dependencies
#   3. Builds a native .app bundle (py2app if possible, shell wrapper fallback)
#   4. Copies it to /Applications
#   5. Optionally sets it to launch at login
#
# Usage:
#   chmod +x install.sh
#   ./install.sh
#

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_NAME="Claude Usage Monitor"
VENV_DIR="$SCRIPT_DIR/.venv"
DIST_DIR="$SCRIPT_DIR/dist"

# ── Colors ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BLUE}  ⚡ Claude Usage Monitor — Installer${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""

# ── Check prerequisites ──
echo -e "${YELLOW}Checking prerequisites...${NC}"

if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Error: python3 not found. Install Python 3.9+ from python.org${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
echo -e "  Python: ${GREEN}${PYTHON_VERSION}${NC}"

if [[ "$(uname)" != "Darwin" ]]; then
    echo -e "${RED}Error: This app is macOS only.${NC}"
    exit 1
fi

MACOS_VERSION=$(sw_vers -productVersion)
echo -e "  macOS:  ${GREEN}${MACOS_VERSION}${NC}"
echo ""

# ── Create virtual environment ──
echo -e "${YELLOW}Creating virtual environment...${NC}"
if [ -d "$VENV_DIR" ]; then
    echo -e "  Removing existing venv..."
    rm -rf "$VENV_DIR"
fi
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
echo -e "  ${GREEN}Done${NC}"
echo ""

# ── Install dependencies ──
echo -e "${YELLOW}Installing dependencies...${NC}"

# CRITICAL: install setuptools first — py2app needs pkg_resources from it
pip install --upgrade pip > /dev/null 2>&1
pip install "setuptools>=69.0,<71.0" wheel > /dev/null 2>&1

# Install runtime deps (skip py2app initially)
pip install rumps Pillow keyring requests 2>&1 | while IFS= read -r line; do
    if [[ "$line" == *"Successfully installed"* ]]; then
        echo -e "  ${GREEN}${line}${NC}"
    fi
done
echo -e "  ${GREEN}Done${NC}"
echo ""

# ── Build .app ──
echo -e "${YELLOW}Building .app bundle...${NC}"
cd "$SCRIPT_DIR"
rm -rf build dist

BUILD_OK=false

# ── Attempt 1: py2app ──
echo -e "  Trying py2app..."
pip install "py2app>=0.28" > /dev/null 2>&1 || true

if python3 setup.py py2app 2>/dev/null; then
    if [ -d "$DIST_DIR/$APP_NAME.app" ]; then
        BUILD_OK=true
        echo -e "  ${GREEN}py2app build succeeded${NC}"
    fi
fi

# ── Attempt 2: shell-wrapper .app bundle (works on any Python) ──
if [ "$BUILD_OK" = false ]; then
    echo -e "  ${YELLOW}py2app failed — building shell-wrapper .app instead${NC}"

    APP_BUNDLE="$DIST_DIR/$APP_NAME.app"
    MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
    RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"

    mkdir -p "$MACOS_DIR" "$RESOURCES_DIR"

    # Copy Python source into Resources
    cp "$SCRIPT_DIR/claude_usage_monitor.py" "$RESOURCES_DIR/"
    cp "$SCRIPT_DIR/weblogin.py" "$RESOURCES_DIR/"

    # Generate app icon
    echo -e "  Generating app icon..."
    cd "$SCRIPT_DIR"
    "$VENV_DIR/bin/python3" "$SCRIPT_DIR/generate_icon.py" 2>&1 || echo -e "  ${YELLOW}Icon generation failed (non-fatal)${NC}"
    if [ -f "$SCRIPT_DIR/AppIcon.icns" ]; then
        cp "$SCRIPT_DIR/AppIcon.icns" "$RESOURCES_DIR/"
        rm -f "$SCRIPT_DIR/AppIcon.icns" "$SCRIPT_DIR/AppIcon.png"
        echo -e "  ${GREEN}App icon created${NC}"
    elif [ -f "$SCRIPT_DIR/AppIcon.png" ]; then
        # Fallback if iconutil wasn't available
        cp "$SCRIPT_DIR/AppIcon.png" "$RESOURCES_DIR/"
        rm -f "$SCRIPT_DIR/AppIcon.png"
        echo -e "  ${GREEN}App icon created (PNG fallback)${NC}"
    else
        echo -e "  ${YELLOW}No icon generated, continuing without custom icon${NC}"
    fi

    # Compile a native C launcher binary — required for macOS TCC to
    # attribute file access to our .app bundle instead of python3
    echo -e "  Compiling native launcher..."
    cat > "$MACOS_DIR/launcher.c" << 'LAUNCHER_C_EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <libgen.h>
#include <mach-o/dyld.h>
#include <sys/stat.h>

int main(int argc, char *argv[]) {
    /* Find our own path to derive Resources dir */
    char exe_path[4096];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        fprintf(stderr, "Failed to get executable path\n");
        return 1;
    }

    /* Resolve symlinks */
    char real_path[4096];
    if (realpath(exe_path, real_path) == NULL) {
        fprintf(stderr, "Failed to resolve path\n");
        return 1;
    }

    /* Build Resources path: .../Contents/MacOS/launcher -> .../Contents/Resources */
    char *macos_dir = dirname(real_path);
    char resources_dir[4096];
    snprintf(resources_dir, sizeof(resources_dir), "%s/../Resources", macos_dir);

    char resources_real[4096];
    if (realpath(resources_dir, resources_real) == NULL) {
        fprintf(stderr, "Failed to resolve Resources dir\n");
        return 1;
    }

    /* Build paths */
    char script_path[4096];
    snprintf(script_path, sizeof(script_path), "%s/claude_usage_monitor.py", resources_real);

    char *home = getenv("HOME");
    if (!home) {
        fprintf(stderr, "HOME not set\n");
        return 1;
    }

    char venv_python[4096];
    snprintf(venv_python, sizeof(venv_python), "%s/.claude-usage-monitor/.venv/bin/python3", home);

    char log_dir[4096];
    snprintf(log_dir, sizeof(log_dir), "%s/.claude-usage-monitor", home);
    mkdir(log_dir, 0755);

    char log_path[4096];
    snprintf(log_path, sizeof(log_path), "%s/app.log", log_dir);

    /* Redirect stdout/stderr to log file */
    FILE *log_file = fopen(log_path, "a");
    if (log_file) {
        dup2(fileno(log_file), STDOUT_FILENO);
        dup2(fileno(log_file), STDERR_FILENO);
        fclose(log_file);
    }

    printf("\n=== Launch (native) ===\n");
    printf("Script: %s\n", script_path);
    printf("Python: %s\n", venv_python);

    /* Set environment */
    setenv("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);

    /* Check if venv python exists */
    struct stat st;
    if (stat(venv_python, &st) == 0) {
        printf("Using venv python3\n");
        fflush(stdout);
        execl(venv_python, "python3", script_path, NULL);
    } else {
        /* Fallback to system python */
        printf("Venv not found, using system python3\n");
        fflush(stdout);
        execlp("python3", "python3", script_path, NULL);
    }

    /* If exec fails */
    perror("exec failed");
    return 1;
}
LAUNCHER_C_EOF

    cc -o "$MACOS_DIR/launcher" "$MACOS_DIR/launcher.c" -framework Foundation 2>&1
    if [ $? -eq 0 ]; then
        rm "$MACOS_DIR/launcher.c"
        echo -e "  ${GREEN}Native launcher compiled${NC}"
    else
        echo -e "  ${RED}Native compilation failed — falling back to shell script${NC}"
        rm -f "$MACOS_DIR/launcher.c" "$MACOS_DIR/launcher"
        cat > "$MACOS_DIR/launcher" << 'LAUNCHER_FALLBACK_EOF'
#!/usr/bin/env bash
RESOURCES_DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
VENV_DIR="$HOME/.claude-usage-monitor/.venv"
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES
export PYTHONUNBUFFERED=1
exec "$VENV_DIR/bin/python3" "$RESOURCES_DIR/claude_usage_monitor.py"
LAUNCHER_FALLBACK_EOF
        chmod +x "$MACOS_DIR/launcher"
    fi

    # Create Info.plist
    cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST_EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Claude Usage Monitor</string>
    <key>CFBundleDisplayName</key>
    <string>Claude Usage Monitor</string>
    <key>CFBundleIdentifier</key>
    <string>com.claude.usage-monitor</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleExecutable</key>
    <string>launcher</string>
    <key>LSUIElement</key>
    <true/>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>NSHumanReadableCopyright</key>
    <string>MIT License</string>
    <key>NSDocumentsFolderUsageDescription</key>
    <string>Claude Usage Monitor needs access to your Documents folder to check git repo status of your agent projects.</string>
</dict>
</plist>
PLIST_EOF

    # Copy the venv to a stable location so the .app works after moving
    STABLE_VENV="$HOME/.claude-usage-monitor/.venv"
    echo -e "  Copying venv to ~/.claude-usage-monitor/.venv ..."
    mkdir -p "$HOME/.claude-usage-monitor"
    rm -rf "$STABLE_VENV"
    cp -R "$VENV_DIR" "$STABLE_VENV"

    # Native launcher already handles the stable venv path
    # (~/.claude-usage-monitor/.venv/bin/python3)

    BUILD_OK=true
    echo -e "  ${GREEN}.app bundle built successfully${NC}"
fi

echo ""

# ── Install to /Applications ──
APP_PATH="$DIST_DIR/$APP_NAME.app"
if [ -d "$APP_PATH" ] && [ "$BUILD_OK" = true ]; then
    echo -e "${YELLOW}Installing to /Applications...${NC}"

    # Kill any running instance first
    pkill -f "claude_usage_monitor.py" 2>/dev/null || true
    sleep 1

    if [ -d "/Applications/$APP_NAME.app" ]; then
        echo -e "  Removing previous version..."
        rm -rf "/Applications/$APP_NAME.app"
    fi

    cp -R "$APP_PATH" "/Applications/"

    # Ad-hoc code sign — required for macOS TCC to grant ambient file access
    # Without this, the app can't read ~/Documents, ~/Downloads, etc.
    echo -e "  Signing .app bundle (ad-hoc)..."
    codesign --force --sign - "/Applications/$APP_NAME.app"

    # Reset any cached TCC denial for this bundle ID so macOS re-prompts
    echo -e "  Resetting file access permissions (may require password)..."
    tccutil reset SystemPolicyDocumentsFolder com.claude.usage-monitor 2>/dev/null || true

    echo -e "  ${GREEN}Installed to /Applications/$APP_NAME.app${NC}"
    echo ""

    # ── Launch at login? (via LaunchAgent) ──
    read -p "Launch at login? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
        LAUNCH_AGENT_FILE="$LAUNCH_AGENT_DIR/com.claude.usage-monitor.plist"
        mkdir -p "$LAUNCH_AGENT_DIR"
        cat > "$LAUNCH_AGENT_FILE" << PLIST_LA_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.claude.usage-monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Applications/Claude Usage Monitor.app/Contents/MacOS/launcher</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OBJC_DISABLE_INITIALIZE_FORK_SAFETY</key>
        <string>YES</string>
    </dict>
    <key>StandardOutPath</key>
    <string>$HOME/.claude-usage-monitor/launchd.log</string>
    <key>StandardErrorPath</key>
    <string>$HOME/.claude-usage-monitor/launchd.log</string>
</dict>
</plist>
PLIST_LA_EOF
        # Unload any existing agent, then load the new one
        launchctl unload "$LAUNCH_AGENT_FILE" 2>/dev/null || true
        echo -e "  ${GREEN}LaunchAgent installed — will auto-start on login${NC}"
    fi
    echo ""

    # ── Launch now? ──
    read -p "Launch now? (y/n) " -n 1 -r
    echo ""
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        open "/Applications/$APP_NAME.app"
        echo -e "  ${GREEN}Launched!${NC}"
    fi
else
    echo -e "${RED}Error: Build failed${NC}"
    echo -e "  You can still run directly:"
    echo -e "    source $VENV_DIR/bin/activate"
    echo -e "    python3 $SCRIPT_DIR/claude_usage_monitor.py"
    exit 1
fi

echo ""
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  ✓ Installation complete!${NC}"
echo -e "${BLUE}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
echo "  Quick start:"
echo "    • Look for ⚡ in your menu bar"
echo "    • Click it to see your 5-hour, 7-day, and extra usage"
echo "    • Make sure Chrome has claude.ai open and you're logged in"
echo ""
echo "  Config:  ~/.claude-usage-monitor/config.json"
echo "  Logs:    ~/.claude-usage-monitor/app.log"
echo "  Data:    Live from claude.ai API (via Chrome)"
echo ""
