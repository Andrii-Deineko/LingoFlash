"""Desktop entry and icon installation helper for Linux desktop environments."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DESKTOP_ENTRY_TEMPLATE = """[Desktop Entry]
Name=LingoFlash
Exec={exec_cmd}
Icon={icon_path}
Type=Application
Categories=Utility;Education;Translation;
Terminal=false
StartupWMClass=LingoFlash
"""


def get_project_root() -> Path:
    """Return the absolute path to the project root directory."""
    return Path(__file__).resolve().parent.parent.parent


def generate_desktop_entry_content(
    python_bin: Optional[str] = None,
    project_root: Optional[Path] = None,
) -> str:
    """Generate the .desktop entry string with absolute paths."""
    root = (project_root or get_project_root()).resolve()
    py_exec = python_bin or sys.executable or "python3"
    main_py = (root / "main.py").resolve()
    icon_svg = (root / "assets" / "logo.svg").resolve()

    exec_cmd = f"{py_exec} {main_py}"
    return DESKTOP_ENTRY_TEMPLATE.format(
        exec_cmd=exec_cmd,
        icon_path=str(icon_svg),
    )


def install_desktop_file(
    target_dir: Optional[Path] = None,
    update_database: bool = True,
) -> Path:
    """Write ~/.local/share/applications/lingoflash.desktop with absolute paths.

    Returns:
        The Path to the created .desktop file.
    """
    dest_dir = target_dir or (Path.home() / ".local" / "share" / "applications")
    dest_dir.mkdir(parents=True, exist_ok=True)
    desktop_file = dest_dir / "lingoflash.desktop"

    content = generate_desktop_entry_content()
    desktop_file.write_text(content, encoding="utf-8")
    desktop_file.chmod(0o755)
    logger.info("Installed desktop launcher at %s", desktop_file)

    # Also install icon to standard hicolor icons directory for broader desktop support
    try:
        root = get_project_root()
        icon_source = (root / "assets" / "logo.svg").resolve()
        if icon_source.is_file():
            icon_dir = Path.home() / ".local" / "share" / "icons" / "hicolor" / "scalable" / "apps"
            icon_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(icon_source, icon_dir / "lingoflash.svg")
    except Exception as exc:
        logger.debug("Optional hicolor icon installation notice: %s", exc)

    if update_database and shutil.which("update-desktop-database"):
        try:
            subprocess.run(
                ["update-desktop-database", str(dest_dir)],
                capture_output=True,
                check=False,
            )
        except Exception:
            pass

    return desktop_file
