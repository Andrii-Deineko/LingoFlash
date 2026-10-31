"""Text sanitization and plain-text stripping utilities for LingoFlash."""

from __future__ import annotations

import html
import re


def strip_formatting(text: str) -> str:
    """Remove HTML/rich-text formatting and decode HTML entities, returning clean plain text.

    Preserves mathematical operators and plain text structure while stripping HTML/XML tags.

    Args:
        text: Input string which may contain HTML tags, style elements, or entities.

    Returns:
        Clean plain text string without formatting.
    """
    if not text:
        return ""

    if "<" in text and ">" in text and re.search(r"</?[a-zA-Z][^>]*>", text):
        # Convert <br> tags to newlines
        s = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        # Convert closing block tags to newlines
        s = re.sub(r"</(?:p|div|h[1-6]|tr|blockquote)>", "\n", s, flags=re.IGNORECASE)
        s = re.sub(r"<li[^>]*>", "• ", s, flags=re.IGNORECASE)
        s = re.sub(r"</li\s*>", "\n", s, flags=re.IGNORECASE)
        # Strip all other HTML tags
        s = re.sub(r"</?[a-zA-Z][^>]*>", "", s)
        s = html.unescape(s)
        s = s.replace("\r\n", "\n").replace("\r", "\n")
        # Collapse 3+ consecutive newlines to 2
        s = re.sub(r"\n{3,}", "\n\n", s)
        # Strip trailing/leading newline created by outer block tags if original didn't end with whitespace
        if not text.endswith(("\n", "\r", " ")):
            s = s.rstrip("\n")
        if not text.startswith(("\n", "\r", " ")):
            s = s.lstrip("\n")
        return s

    s = html.unescape(text)
    return s.replace("\r\n", "\n").replace("\r", "\n")
