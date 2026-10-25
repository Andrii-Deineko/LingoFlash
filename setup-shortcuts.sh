#!/bin/bash
# Register LingoFlash GNOME keyboard shortcuts safely without overwriting others
# Usage: ./setup-shortcuts.sh [path_to_main.py]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAIN_PY="${1:-$SCRIPT_DIR/main.py}"

python3 "$SCRIPT_DIR/setup_shortcuts.py" "$MAIN_PY"

