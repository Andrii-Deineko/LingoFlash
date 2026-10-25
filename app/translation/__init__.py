"""Translation backend module for LingoFlash.

Provides pluggable translation backends (Google, DeepL, LibreTranslate),
async translation workers, and translation history management.
"""

from app.translation.translator import (
    DeepLBackend,
    GoogleTranslateBackend,
    LibreTranslateBackend,
    TranslationBackend,
    TranslationError,
    TranslationHistory,
    TranslationManager,
    TranslationWorker,
)

__all__ = [
    "TranslationBackend",
    "GoogleTranslateBackend",
    "DeepLBackend",
    "LibreTranslateBackend",
    "TranslationError",
    "TranslationManager",
    "TranslationWorker",
    "TranslationHistory",
]
