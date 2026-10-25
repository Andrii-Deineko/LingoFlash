"""Desktop shortcut management and GNOME/Wayland gsettings integration."""

from __future__ import annotations

import ast
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_SHORTCUTS: dict[str, str] = {
    "translate_selection": "Ctrl+Alt+T",
    "ocr_capture": "Ctrl+Alt+O",
    "ocr_overlay": "Ctrl+Alt+S",
    "show_main": "Ctrl+Alt+L",
    "swap_languages": "Ctrl+Shift+S",
}

GNOME_KEYBINDINGS_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_CUSTOM_SCHEMA = "org.gnome.settings-daemon.plugins.media-keys.custom-keybinding"

KB_BASE = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings"
KB_PATHS = {
    "translate_selection": f"{KB_BASE}/lingoflash-translate/",
    "ocr_capture": f"{KB_BASE}/lingoflash-ocr/",
    "ocr_overlay": f"{KB_BASE}/lingoflash-overlay/",
    "show_main": f"{KB_BASE}/lingoflash-show/",
}


def qt_to_gnome_shortcut(qt_key_str: str) -> str:
    """Convert Qt key sequence format (e.g. 'Ctrl+Alt+T') to GNOME gsettings format (e.g. '<Ctrl><Alt>t')."""
    if not qt_key_str or not qt_key_str.strip():
        return ""

    parts = [p.strip() for p in qt_key_str.split("+") if p.strip()]
    if not parts:
        return ""

    gnome_mods: list[str] = []
    key = ""

    for part in parts:
        lower = part.lower()
        if lower in ("ctrl", "control"):
            gnome_mods.append("<Ctrl>")
        elif lower == "alt":
            gnome_mods.append("<Alt>")
        elif lower == "shift":
            gnome_mods.append("<Shift>")
        elif lower in ("meta", "super", "win"):
            gnome_mods.append("<Super>")
        else:
            if len(part) == 1:
                key = part.lower()
            elif lower == "space":
                key = "space"
            else:
                key = part

    return "".join(gnome_mods) + key


def gnome_to_qt_shortcut(gnome_str: str) -> str:
    """Convert GNOME binding string (e.g. '<Ctrl><Alt>t') to Qt format ('Ctrl+Alt+T')."""
    if not gnome_str:
        return ""

    gnome_str = gnome_str.strip("'\"")
    mods: list[str] = []
    tokens = re.findall(r"<[^>]+>", gnome_str)
    for tok in tokens:
        name = tok.strip("<>").capitalize()
        if name in ("Ctrl", "Alt", "Shift", "Super"):
            mods.append(name)

    key = re.sub(r"<[^>]+>", "", gnome_str).strip()
    if len(key) == 1:
        key = key.upper()
    elif key.lower() == "space":
        key = "Space"

    parts = mods + ([key] if key else [])
    return "+".join(parts)


def apply_gnome_shortcuts(
    shortcuts: dict[str, str],
    main_py_path: Optional[str] = None,
) -> tuple[bool, str]:
    """Register or update GNOME keyboard shortcuts using gsettings.

    Args:
        shortcuts: Mapping of action name to Qt shortcut string (e.g. ``{"translate_selection": "Ctrl+Alt+T"}``).
        main_py_path: Absolute path to ``main.py``. Defaults to resolving relative to this file.

    Returns:
        (success, message) tuple.
    """
    if not shutil.which("gsettings"):
        msg = "gsettings executable not found; skipping GNOME shortcut registration."
        logger.info(msg)
        return False, msg

    if main_py_path is None:
        main_py = str(Path(__file__).resolve().parent.parent.parent / "main.py")
    else:
        main_py = str(Path(main_py_path).resolve())

    python_bin = sys.executable or "python3"

    try:
        # 1. Fetch existing custom-keybindings list
        res = subprocess.run(
            ["gsettings", "get", GNOME_KEYBINDINGS_SCHEMA, "custom-keybindings"],
            capture_output=True,
            text=True,
            check=True,
            timeout=3,
        )
        out = res.stdout.strip()
        if out.startswith("@as"):
            out = out[3:].strip()

        try:
            bindings: list[str] = ast.literal_eval(out) if out else []
        except Exception:
            bindings = []

        # 2. Append LingoFlash custom binding paths if missing
        needed = list(KB_PATHS.values())
        changed = False
        for path in needed:
            if path not in bindings:
                bindings.append(path)
                changed = True

        if changed or not out:
            formatted = "[" + ", ".join(f"'{x}'" for x in bindings) + "]"
            subprocess.run(
                ["gsettings", "set", GNOME_KEYBINDINGS_SCHEMA, "custom-keybindings", formatted],
                check=True,
                timeout=3,
            )

        # 3. Configure individual keybindings
        actions_info = [
            (
                "translate_selection",
                "LingoFlash - Translate Selection",
                f"{python_bin} {main_py} --action translate_selection",
            ),
            (
                "ocr_capture",
                "LingoFlash - OCR Capture",
                f"{python_bin} {main_py} --action ocr_capture",
            ),
            (
                "ocr_overlay",
                "LingoFlash - Comic/Game OCR Overlay",
                f"{python_bin} {main_py} --action ocr_overlay",
            ),
            (
                "show_main",
                "LingoFlash - Show Window",
                f"{python_bin} {main_py} --action show_main",
            ),
        ]

        for action_key, name, cmd in actions_info:
            path = KB_PATHS[action_key]
            schema_path = f"{GNOME_CUSTOM_SCHEMA}:{path}"
            qt_binding = shortcuts.get(action_key, DEFAULT_SHORTCUTS.get(action_key, ""))
            gnome_binding = qt_to_gnome_shortcut(qt_binding)

            subprocess.run(
                ["gsettings", "set", schema_path, "name", name],
                check=True,
                timeout=3,
            )
            subprocess.run(
                ["gsettings", "set", schema_path, "command", cmd],
                check=True,
                timeout=3,
            )
            subprocess.run(
                ["gsettings", "set", schema_path, "binding", gnome_binding],
                check=True,
                timeout=3,
            )

        logger.info("GNOME keyboard shortcuts successfully updated in gsettings")
        return True, "GNOME keyboard shortcuts updated successfully"

    except Exception as exc:
        msg = f"Failed to update GNOME shortcuts via gsettings: {exc}"
        logger.warning(msg)
        return False, msg
