#!/usr/bin/env bash
# uninstall.sh — Remove wallpick from the system.
#
# Usage:
#   ./uninstall.sh          # Uninstall user installation
#   sudo ./uninstall.sh     # Uninstall system-wide

set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    DESKTOP_DIR="/usr/share/applications"
    echo "Uninstalling system-wide…"
else
    DESKTOP_DIR="${HOME}/.local/share/applications"
    echo "Uninstalling for current user…"
fi

# 1. Uninstall the Python package
echo "→ Removing Python package…"
pip3 uninstall -y wallpick 2>/dev/null || pip uninstall -y wallpick 2>/dev/null || true

# 2. Remove the desktop entry
DESKTOP_FILE="${DESKTOP_DIR}/io.github.alyasmrthi.wallpick.desktop"
if [ -f "${DESKTOP_FILE}" ]; then
    echo "→ Removing desktop entry…"
    rm -f "${DESKTOP_FILE}"
fi

# 3. Update desktop database
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${DESKTOP_DIR}" 2>/dev/null || true
fi

echo ""
echo "✓ wallpick uninstalled."
echo ""
echo "  Config and cache are preserved at:"
echo "    ~/.config/wallpick/"
echo "    ~/.cache/wallpick/"
echo "  Remove them manually if you want a full cleanup."
