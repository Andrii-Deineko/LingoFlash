"""Theme detection, structured color palettes, and dynamic QSS compiler for LingoFlash.

Theme palettes and QSS templates are externalized in ``assets/themes.json``.
Supports 11+ distinct, hand-crafted desktop themes (Dark, Light, Nord, Dracula,
AMOLED Black, Catppuccin, Tokyo Night, Solarized Dark/Light, Gruvbox, High Contrast).
Allows live switching at runtime without application restarts.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Search paths for themes.json
_THEME_SEARCH_PATHS: list[Path] = [
    Path(__file__).resolve().parent.parent.parent / "assets" / "themes.json",
    Path.cwd() / "assets" / "themes.json",
    Path.cwd() / "themes.json",
]

_CACHED_THEMES: dict[str, dict[str, Any]] = {}
_CACHED_QSS_TEMPLATE: str = ""


def get_themes_json_path() -> Path:
    """Locate the themes.json file."""
    for p in _THEME_SEARCH_PATHS:
        if p.is_file():
            return p
    return _THEME_SEARCH_PATHS[0]


def load_themes_data() -> tuple[dict[str, dict[str, Any]], str]:
    """Load themes and QSS template from assets/themes.json."""
    global _CACHED_THEMES, _CACHED_QSS_TEMPLATE
    if _CACHED_THEMES and _CACHED_QSS_TEMPLATE:
        return _CACHED_THEMES, _CACHED_QSS_TEMPLATE

    json_path = get_themes_json_path()
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                themes = data.get("themes", {})
                template = data.get("qss_template", "")
                if isinstance(themes, dict) and themes:
                    _CACHED_THEMES = themes
                if isinstance(template, str) and template:
                    _CACHED_QSS_TEMPLATE = template
                logger.debug("Loaded %d themes from %s", len(_CACHED_THEMES), json_path)
                return _CACHED_THEMES, _CACHED_QSS_TEMPLATE
        except Exception as exc:
            logger.warning("Failed to load themes.json from %s: %s", json_path, exc)

    return _CACHED_THEMES, _CACHED_QSS_TEMPLATE


def reload_themes() -> None:
    """Clear theme cache and reload from disk."""
    global _CACHED_THEMES, _CACHED_QSS_TEMPLATE
    _CACHED_THEMES = {}
    _CACHED_QSS_TEMPLATE = ""
    load_themes_data()


# Initial load
_LOADED_THEMES, _ = load_themes_data()
THEMES: dict[str, dict[str, Any]] = _LOADED_THEMES


def get_available_themes() -> list[tuple[str, str]]:
    """Return list of (theme_id, display_name) for theme selectors."""
    themes, _ = load_themes_data()
    items = [("system", "System Auto (Follow OS)")]
    items.extend((t_id, info.get("name", t_id.capitalize())) for t_id, info in themes.items())
    return items


def detect_system_theme() -> str:
    """Detect whether GNOME is currently set to dark or light mode.

    Returns:
        "dark" or "light". Defaults to "dark" on error.
    """
    try:
        res = subprocess.run(
            ["gsettings", "get", "org.gnome.desktop.interface", "color-scheme"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if "dark" in res.stdout.lower():
            return "dark"
        if "light" in res.stdout.lower():
            return "light"
    except Exception as exc:
        logger.debug("Failed to detect system theme via gsettings: %s", exc)

    return "dark"


def get_theme_palette(theme: str = "system") -> dict[str, Any]:
    """Return the resolved color palette dict for the specified theme or system mode."""
    themes, _ = load_themes_data()
    if theme == "system" or theme not in themes:
        sys_mode = detect_system_theme()
        theme_id = "dark" if sys_mode == "dark" else "light"
    else:
        theme_id = theme
    return dict(themes.get(theme_id, themes.get("dark", {})))


def get_app_icon() -> Any:
    """Return the official LingoFlash SVG application icon."""
    try:
        from PyQt6.QtGui import QIcon

        for p in [
            Path(__file__).resolve().parent.parent.parent / "assets" / "logo.svg",
            Path.cwd() / "assets" / "logo.svg",
            Path.cwd() / "logo.svg",
        ]:
            if p.is_file():
                icon = QIcon(str(p))
                if not icon.isNull():
                    return icon
        return QIcon.fromTheme("accessories-text-editor")
    except Exception as exc:
        logger.debug("Failed to load app icon: %s", exc)
        return None


SVG_ICONS: dict[str, str] = {
    "save": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path fill="{color}" d="M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z"/>
    </svg>""",
    "cancel": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path fill="{color}" d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
    </svg>""",
    "speaker": """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
        <path fill="{color}" d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/>
    </svg>""",
}


def create_svg_icon(svg_name_or_template: str, color: str = "#FFFFFF", size: int = 16) -> Any:
    """Create a crisp QIcon from an inline SVG template string or known icon name."""
    try:
        from PyQt6.QtGui import QIcon, QPixmap, QPainter
        from PyQt6.QtCore import QByteArray, Qt
        from PyQt6.QtSvg import QSvgRenderer

        raw_template = SVG_ICONS.get(svg_name_or_template, svg_name_or_template)
        svg_str = raw_template.format(color=color)

        renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))
        if not renderer.isValid():
            return QIcon()

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()

        return QIcon(pixmap)
    except Exception as exc:
        logger.debug("Failed to create SVG icon: %s", exc)
        try:
            from PyQt6.QtGui import QIcon
            return QIcon()
        except ImportError:
            return None


def get_stylesheet(
    theme: str = "system",
    font_size: Optional[int] = None,
    ui_font_size: Optional[int] = None,
    text_font_size: Optional[int] = None,
) -> str:
    """Return an ultra-modern, minimalist QSS stylesheet compiled from theme tokens in assets/themes.json.

    Args:
        theme: Theme identifier (e.g. "dark", "light", "nord", "dracula", "amoled",
               "catppuccin", "tokyo_night", "solarized_dark", "solarized_light",
               "gruvbox", "high_contrast", or "system").
        font_size: Legacy/compatibility parameter for text_font_size.
        ui_font_size: Optional global UI font size in points (defaults to 13).
        text_font_size: Optional translation text editor font size in points (defaults to 14).
    """
    themes, template = load_themes_data()
    if theme == "system" or theme not in themes:
        sys_mode = detect_system_theme()
        theme_id = "dark" if sys_mode == "dark" else "light"
    else:
        theme_id = theme

    cfg = themes.get(theme_id, themes.get("dark", {}))
    ui_sz = ui_font_size if (ui_font_size is not None and 10 <= ui_font_size <= 36) else 13
    raw_txt = text_font_size if text_font_size is not None else font_size
    txt_sz = raw_txt if (raw_txt is not None and 10 <= raw_txt <= 36) else 14

    is_dark = cfg.get("is_dark", True)
    hover_overlay = "rgba(255, 255, 255, 0.08)" if is_dark else "rgba(0, 0, 0, 0.06)"
    pressed_overlay = "rgba(255, 255, 255, 0.15)" if is_dark else "rgba(0, 0, 0, 0.12)"
    item_hover = "rgba(255, 255, 255, 0.05)" if is_dark else "rgba(0, 0, 0, 0.04)"
    item_selected = f"{cfg.get('accent', '#3b82f6')}26"
    card_border = f"1px solid {cfg.get('border_subtle')}" if cfg.get("border_subtle", "transparent") != "transparent" else "none"

    tokens: dict[str, str] = {
        "ui_sz": str(ui_sz),
        "txt_sz": str(txt_sz),
        "hover_overlay": hover_overlay,
        "pressed_overlay": pressed_overlay,
        "item_hover": item_hover,
        "item_selected": item_selected,
        "card_border": card_border,
    }

    # Add all theme color tokens
    for k, v in cfg.items():
        if isinstance(v, (str, int, float, bool)):
            tokens[k] = str(v)

    qss = template
    for key, val in tokens.items():
        qss = qss.replace(f"{{{key}}}", val)

    return qss
