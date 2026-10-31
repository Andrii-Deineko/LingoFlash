"""Dynamic JSON-based internationalization (i18n) for LingoFlash.

Loads translation dictionaries dynamically from the ``locales/`` directory.
Supports runtime locale switching without requiring application restart.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.utils.json_utils import safe_load_json

logger = logging.getLogger(__name__)

SUPPORTED_LOCALES: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "uk": "Українська",
    "sv": "Svenska",
}

DEFAULT_LOCALE = "en"

# Search directories for locales
_LOCALE_SEARCH_PATHS: list[Path] = [
    Path(__file__).resolve().parent.parent.parent / "locales",
    Path.cwd() / "locales",
]

_CACHED_STRINGS: dict[str, dict[str, str]] = {}
_CURRENT_LOCALE = DEFAULT_LOCALE


def get_locales_directory() -> Path:
    """Locate the active locales directory."""
    for p in _LOCALE_SEARCH_PATHS:
        if p.is_dir():
            return p
    return _LOCALE_SEARCH_PATHS[0]


def load_locale(locale: str) -> dict[str, str]:
    """Load strings dictionary for a specific locale code from JSON."""
    if locale in _CACHED_STRINGS:
        return _CACHED_STRINGS[locale]

    loc_dir = get_locales_directory()
    json_path = loc_dir / f"{locale}.json"
    data = safe_load_json(json_path, default=None)

    if isinstance(data, dict):
        # Enforce string keys and string values
        clean_strings = {str(k): str(v) for k, v in data.items()}
        _CACHED_STRINGS[locale] = clean_strings
        logger.debug("Loaded locale '%s' (%d strings) from %s", locale, len(clean_strings), json_path)
        return clean_strings

    # If fallback fails or file doesn't exist
    if locale != DEFAULT_LOCALE:
        return load_locale(DEFAULT_LOCALE)

    return {}


def set_current_locale(locale: str) -> None:
    """Set the active UI locale and ensure its strings are loaded."""
    global _CURRENT_LOCALE
    if locale in SUPPORTED_LOCALES:
        _CURRENT_LOCALE = locale
        load_locale(locale)
        logger.debug("Active locale set to '%s'", locale)
    else:
        logger.warning("Unsupported locale '%s'; falling back to '%s'", locale, DEFAULT_LOCALE)
        _CURRENT_LOCALE = DEFAULT_LOCALE
        load_locale(DEFAULT_LOCALE)


def get_current_locale() -> str:
    """Return the active UI locale identifier."""
    return _CURRENT_LOCALE


def tr(key: str, locale: str | None = None, **kwargs: Any) -> str:
    """Translate *key* into the current or specified locale with optional format arguments."""
    loc = locale or _CURRENT_LOCALE
    strings = load_locale(loc)
    val = strings.get(key)

    if val is None and loc != DEFAULT_LOCALE:
        val = load_locale(DEFAULT_LOCALE).get(key, key)
    elif val is None:
        val = key

    val_str = str(val)
    if kwargs:
        try:
            return val_str.format(**kwargs)
        except Exception:
            return val_str
    return val_str


def reload_locales() -> None:
    """Clear cached locales and reload active strings from disk."""
    _CACHED_STRINGS.clear()
    load_locale(_CURRENT_LOCALE)
    logger.debug("Locales cache reloaded.")
