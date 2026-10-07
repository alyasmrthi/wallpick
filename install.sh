#!/usr/bin/env bash
# install.sh — Install wallpick as a system application.
#
# Usage:
#   ./install.sh          # Install for the current user (~/.local)
#   sudo ./install.sh     # Install system-wide (/usr)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Determine prefix
if [ "$(id -u)" -eq 0 ]; then
    PREFIX="/usr"
    DESKTOP_DIR="/usr/share/applications"
    echo "Installing system-wide to ${PREFIX}…"
else
    PREFIX="${HOME}/.local"
    DESKTOP_DIR="${HOME}/.local/share/applications"
    echo "Installing for current user to ${PREFIX}…"
fi

BIN_DIR="${PREFIX}/bin"

# 1. Install the Python package
echo "→ Installing Python package…"
if command -v pip3 &>/dev/null; then
    pip3 install --prefix="${PREFIX}" "${SCRIPT_DIR}"
elif command -v pip &>/dev/null; then
    pip install --prefix="${PREFIX}" "${SCRIPT_DIR}"
else
    echo "ERROR: pip is not installed. Install python-pip first."
    exit 1
fi

# 2. Install the desktop entry
echo "→ Installing desktop entry…"
mkdir -p "${DESKTOP_DIR}"
DESKTOP_FILE="${SCRIPT_DIR}/data/io.github.alyasmrthi.wallpick.desktop"

if [ -f "${DESKTOP_FILE}" ]; then
    # Fix the Exec path for the installed binary
    sed "s|^Exec=.*|Exec=${BIN_DIR}/wallpick|" "${DESKTOP_FILE}" \
        > "${DESKTOP_DIR}/io.github.alyasmrthi.wallpick.desktop"
else
    echo "WARNING: Desktop file not found at ${DESKTOP_FILE}"
fi

# 3. Update desktop database if available
if command -v update-desktop-database &>/dev/null; then
    echo "→ Updating desktop database…"
    update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
fi

echo ""
echo "✓ wallpick installed successfully!"
echo ""
echo "  Run it from the terminal:   wallpick"
echo "  Or find 'Wallpick' in your app menu."
echo ""
echo "  To uninstall:  pip3 uninstall wallpick"
echo "                 rm ${DESKTOP_DIR}/io.github.alyasmrthi.wallpick.desktop"
