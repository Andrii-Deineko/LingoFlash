"""Safe, atomic JSON file reading and writing utilities for LingoFlash."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def safe_load_json(file_path: Path | str, default: Any = None) -> Any:
    """Safely read and parse a JSON file, returning *default* on any error.

    Handles missing files, unreadable permissions, corrupted JSON syntax,
    and non-file paths without raising exceptions.

    Args:
        file_path: Path to the JSON file.
        default: Fallback value returned if the file is missing or invalid.

    Returns:
        The deserialized JSON content, or *default*.
    """
    path = Path(file_path)
    if not path.is_file():
        return default

    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            logger.debug("JSON file '%s' is empty; returning default", path)
            return default
        return json.loads(content)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("Corrupted JSON in '%s': %s; falling back to default", path, exc)
        return default
    except OSError as exc:
        logger.warning("Could not read JSON file '%s': %s; falling back to default", path, exc)
        return default


def safe_save_json(file_path: Path | str, data: Any, indent: int = 2) -> bool:
    """Atomically serialize and save *data* to a JSON file on disk.

    Writes first to a sibling `.tmp` file and replaces the destination
    atomically to prevent data corruption during unexpected shutdown.

    Args:
        file_path: Path to the target JSON file.
        data: Object to serialize.
        indent: Indentation level for readability (default: 2).

    Returns:
        True if the file was saved successfully, False otherwise.
    """
    path = Path(file_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = path.with_suffix(".tmp")
        tmp_file.write_text(
            json.dumps(data, indent=indent, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_file.replace(path)
        logger.debug("Successfully saved JSON to '%s'", path)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.error("Failed to save JSON to '%s': %s", path, exc)
        tmp_file = path.with_suffix(".tmp")
        if tmp_file.is_file():
            try:
                tmp_file.unlink()
            except OSError:
                pass
        return False
