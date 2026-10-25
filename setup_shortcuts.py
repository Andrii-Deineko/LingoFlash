#!/usr/bin/env python3
"""CLI utility to register or update LingoFlash shortcuts in GNOME/Wayland."""

from __future__ import annotations

import sys
from pathlib import Path

from app.translation.translator import TranslationManager
from app.utils.shortcuts import apply_gnome_shortcuts


def main() -> int:
    main_py_arg = sys.argv[1] if len(sys.argv) > 1 else None
    manager = TranslationManager()
    shortcuts = manager.get_shortcuts()

    print("Registering GNOME keyboard shortcuts for LingoFlash...")
    success, message = apply_gnome_shortcuts(shortcuts, main_py_path=main_py_arg)
    print(message)
    if success:
        print("Active bindings:")
        for action, key in shortcuts.items():
            print(f"  {action:22} -> {key}")
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
