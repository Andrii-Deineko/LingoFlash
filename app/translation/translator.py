"""Modular translation backend with async workers.

This module provides a pluggable translation architecture with multiple
backend implementations (Google Translate, DeepL, LibreTranslate), an
async QThread-based worker for non-blocking UI translations, and a
persistent translation history store.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, QThread, pyqtSignal
import requests
from requests.adapters import HTTPAdapter

from app.utils.shortcuts import DEFAULT_SHORTCUTS

logger = logging.getLogger(__name__)

# Dedicated thread pool for bounded language detection with timeouts
_DETECTION_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="detector")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_CONFIG_DIR = Path.home() / ".config" / "lingoflash"
_CONFIG_FILE = _CONFIG_DIR / "config.json"
_DATA_DIR = Path.home() / ".local" / "share" / "lingoflash"
_HISTORY_FILE = _DATA_DIR / "history.json"

_MAX_HISTORY = 20

# In-memory thread-safe LRU cache to avoid duplicate network calls
_TRANSLATION_CACHE: dict[tuple[str, str, str], tuple[str, str]] = {}
_CACHE_LOCK = threading.Lock()
_MAX_CACHE_SIZE = 500

_LINGUA_DETECTOR: Any = None
_LINGUA_LOCK = threading.Lock()


def _get_lingua_detector() -> Any:
    """Lazily load lingua language detector for high accuracy on short texts."""
    global _LINGUA_DETECTOR
    if _LINGUA_DETECTOR is None:
        with _LINGUA_LOCK:
            if _LINGUA_DETECTOR is None:
                try:
                    from lingua import LanguageDetectorBuilder  # type: ignore[import-untyped]

                    _LINGUA_DETECTOR = LanguageDetectorBuilder.from_all_languages().build()
                    logger.debug("Lingua language detector successfully initialized")
                except Exception as exc:
                    logger.warning("Could not initialize lingua detector: %s", exc)
    return _LINGUA_DETECTOR


def _detect_language_lingua(text: str) -> str:
    """Detect language using high-accuracy *lingua* with confidence bounds and script heuristics."""
    stripped = text.strip()
    if not stripped or len(stripped) < 2:
        return "auto"

    # 1. Fast pre-check: Text must contain alphabet/word characters
    if not re.search(r"[a-zA-Z\u00C0-\u024F\u0400-\u04FF\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]", stripped):
        return "auto"

    # 2. Fast-path script heuristics (0ms overhead for distinctive alphabets)
    if any(c in stripped for c in "іїєґІЇЄҐ"):
        return "uk"
    if any(c in stripped for c in "ыэъЫЭЪ"):
        return "ru"

    # 3. Lingua Language Detector (trained on n-grams for short sentences)
    try:
        detector = _get_lingua_detector()
        if detector is not None:
            sample = stripped[:200]
            lang = detector.detect_language_of(sample)
            if lang is not None and hasattr(lang, "iso_code_639_1"):
                code: str = lang.iso_code_639_1.name.lower()
                logger.debug("Lingua detected language: %s", code)
                return code
    except Exception as exc:
        logger.debug("Lingua detection error: %s", exc)

    return "auto"


# ---------------------------------------------------------------------------
# Language normalization and detection mapping
# ---------------------------------------------------------------------------
NAME_TO_CODE_MAP: dict[str, str] = {
    "english": "en",
    "russian": "ru",
    "ukrainian": "uk",
    "swedish": "sv",
    "german": "de",
    "french": "fr",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "polish": "pl",
    "dutch": "nl",
    "chinese": "zh-CN",
    "chinese (simplified)": "zh-CN",
    "chinese (traditional)": "zh-TW",
    "japanese": "ja",
    "korean": "ko",
    "arabic": "ar",
    "turkish": "tr",
    "czech": "cs",
    "danish": "da",
    "finnish": "fi",
    "greek": "el",
    "hebrew": "he",
    "hindi": "hi",
    "hungarian": "hu",
    "indonesian": "id",
    "norwegian": "no",
    "romanian": "ro",
    "slovak": "sk",
    "bulgarian": "bg",
    "croatian": "hr",
    "serbian": "sr",
    "macedonian": "mk",
}


def normalize_detected_language(detected: str, fallback_text: str = "") -> str:
    """Normalize language code or name to standard ISO-639-1 code."""
    if not detected or detected.strip().lower() in ("auto", "unknown", "none"):
        if fallback_text:
            return _detect_language_lingua(fallback_text)
        return "auto"

    det_lower = detected.strip().lower()
    if det_lower in NAME_TO_CODE_MAP:
        return NAME_TO_CODE_MAP[det_lower]

    # Check prefix or dialect codes like "en-US", "ru-RU", "sv-SE"
    base = det_lower.split("-")[0].split("_")[0]
    if base in NAME_TO_CODE_MAP.values():
        # Do not allow false English detection for texts containing Cyrillic characters
        if base == "en" and fallback_text and any("\u0400" <= c <= "\u04FF" for c in fallback_text):
            return _detect_language_lingua(fallback_text)
        return base

    # Try matching against full name in NAME_TO_CODE_MAP substring
    for name, code in NAME_TO_CODE_MAP.items():
        if name in det_lower:
            return code

    if fallback_text:
        return _detect_language_lingua(fallback_text)
    return det_lower


_MYMEMORY_LANG_MAP: dict[str, str] = {
    "en": "en-GB",
    "uk": "uk-UA",
    "de": "de-DE",
    "fr": "fr-FR",
    "es": "es-ES",
    "it": "it-IT",
    "pl": "pl-PL",
    "pt": "pt-PT",
    "nl": "nl-NL",
    "ru": "ru-RU",
    "zh-CN": "zh-CN",
    "ja": "ja-JP",
    "ko": "ko-KR",
}


def detect_language(text: str) -> str:
    """Detect the language code of the given text using Lingua or heuristics."""
    detected = _detect_language_lingua(text)
    if detected:
        return normalize_detected_language(detected, fallback_text=text)
    return normalize_detected_language("", fallback_text=text)


def _cache_get(text: str, source: str, target: str) -> tuple[str, str] | None:
    with _CACHE_LOCK:
        val = _TRANSLATION_CACHE.get((source, target, text.strip()))
        if val is None:
            return None
        if isinstance(val, tuple):
            return val
        return (val, source if source != "auto" else "")


def _cache_put(text: str, source: str, target: str, result: str, detected: str = "") -> None:
    with _CACHE_LOCK:
        if len(_TRANSLATION_CACHE) > _MAX_CACHE_SIZE:
            # Evict oldest 100 items
            for k in list(_TRANSLATION_CACHE.keys())[:100]:
                del _TRANSLATION_CACHE[k]
        _TRANSLATION_CACHE[(source, target, text.strip())] = (result, detected)


def _fallback_mymemory(text: str, source_lang: str, target_lang: str) -> str:
    """Attempt translation with MyMemory as free fallback when Google throttles."""
    from deep_translator import MyMemoryTranslator  # type: ignore[import-untyped]

    if source_lang == "auto":
        # Check if text contains Cyrillic characters
        if any("\u0400" <= c <= "\u04FF" for c in text):
            src = "uk-UA"
        else:
            src = "en-GB"
    else:
        src = _MYMEMORY_LANG_MAP.get(source_lang, "en-GB")

    tgt = _MYMEMORY_LANG_MAP.get(target_lang, "en-GB")
    if src == tgt:
        tgt = "uk-UA" if src == "en-GB" else "en-GB"

    translator = MyMemoryTranslator(source=src, target=tgt)
    res = translator.translate(text)
    if res and not res.startswith("MYMEMORY WARNING"):
        return res
    raise TranslationError("MyMemory fallback did not return valid result")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------
class TranslationError(Exception):
    """Custom exception raised when a translation operation fails.

    Attributes:
        message: Human-readable error description.
        original: The original exception that caused this error, if any.
    """

    def __init__(self, message: str, original: Exception | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.original = original


# ---------------------------------------------------------------------------
# Abstract backend
# ---------------------------------------------------------------------------
class TranslationBackend(ABC):
    """Abstract base class for all translation backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Return a short, unique identifier for this backend."""
        ...

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate *text* from *source_lang* to *target_lang*.

        Args:
            text: The text to translate.
            source_lang: BCP-47 language code or ``"auto"`` for detection.
            target_lang: BCP-47 language code for the target language.

        Returns:
            The translated text.

        Raises:
            TranslationError: If the translation fails.
        """
        result, _ = self.translate_with_detection(text, source_lang, target_lang)
        return result

    @abstractmethod
    def translate_with_detection(
        self, text: str, source_lang: str, target_lang: str
    ) -> tuple[str, str]:
        """Translate *text* and return (translated_text, detected_source_language)."""
        ...

    @abstractmethod
    def detect_language(self, text: str) -> str:
        """Detect the language of *text*.

        Returns:
            A language code string, or ``"auto"`` if detection is unavailable.
        """
        ...

    @abstractmethod
    def get_supported_languages(self) -> dict[str, str]:
        """Return a mapping of language code → human-readable name."""
        ...


# ---------------------------------------------------------------------------
# Google Translate backend
# ---------------------------------------------------------------------------
class GoogleTranslateBackend(TranslationBackend):
    """Backend powered by Google Translate with connection pooling and fast JSON API."""

    def __init__(self) -> None:
        self._session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=15,
            pool_maxsize=25,
            max_retries=1,
            pool_block=False,
        )
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        })

    @property
    def name(self) -> str:  # noqa: D401
        """Backend identifier."""
        return "google"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate *text* using Google Translate with persistent connection pooling."""
        result, _ = self.translate_with_detection(text, source_lang, target_lang)
        return result

    def translate_with_detection(
        self, text: str, source_lang: str, target_lang: str
    ) -> tuple[str, str]:
        """Translate *text* and return (translated_text, detected_language) via single API call.

        Args:
            text: The text to translate.
            source_lang: Source language code, or ``"auto"`` for auto-detection.
            target_lang: Target language code.

        Returns:
            Tuple of (translated_string, detected_source_language_code).

        Raises:
            TranslationError: On network or API errors.
        """
        stripped = text.strip()
        if not stripped:
            return "", source_lang if source_lang != "auto" else ""

        cached = _cache_get(stripped, source_lang, target_lang)
        if cached is not None:
            logger.debug("Cache hit for [%s→%s]: %s", source_lang, target_lang, stripped[:30])
            return cached

        # 1. Fast path: Direct GTX endpoint using persistent connection pool
        src = "auto" if source_lang == "auto" else source_lang
        try:
            url = "https://translate.googleapis.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": src,
                "tl": target_lang,
                "dt": "t",
                "q": stripped,
            }
            resp = self._session.get(url, params=params, timeout=3.5)
            if resp.status_code == 200:
                data = resp.json()
                if data and isinstance(data, list) and len(data) > 0 and isinstance(data[0], list):
                    result = "".join(seg[0] for seg in data[0] if seg and seg[0])
                    # Extract API detected language directly from GTX response (index 2)
                    detected = source_lang
                    if len(data) > 2 and isinstance(data[2], str) and data[2]:
                        raw_det = data[2]
                        # Don't trust 'en' blindly if text contains Cyrillic or non-Latin characters
                        if raw_det == "en" and any("\u0400" <= c <= "\u04FF" for c in stripped):
                            detected = self.detect_language(stripped)
                        else:
                            detected = normalize_detected_language(raw_det, stripped)
                    elif source_lang == "auto":
                        detected = self.detect_language(stripped)
                    if result:
                        _cache_put(stripped, source_lang, target_lang, result, detected)
                        return result, detected
        except Exception as exc:
            logger.debug("Fast GTX request failed, falling back to deep_translator: %s", exc)

        # 2. Secondary path: deep_translator.GoogleTranslator
        try:
            from deep_translator import GoogleTranslator  # type: ignore[import-untyped]

            translator = GoogleTranslator(source=src, target=target_lang)
            result = translator.translate(stripped)
            detected = source_lang
            if source_lang == "auto":
                detected = self.detect_language(stripped)
            if result:
                _cache_put(stripped, source_lang, target_lang, result, detected)
            return result, detected
        except ImportError as exc:
            raise TranslationError(
                "deep_translator package is not installed", original=exc
            ) from exc
        except Exception as exc:
            err_str = str(exc)
            logger.warning("Google Translate encountered error: %s", err_str)

            # 3. Tertiary fallback: MyMemoryTranslator
            if "too many requests" in err_str.lower() or "5 requests per second" in err_str:
                logger.info("Google rate limited — attempting MyMemory fallback...")
                try:
                    res = _fallback_mymemory(stripped, source_lang, target_lang)
                    detected = self.detect_language(stripped) if source_lang == "auto" else source_lang
                    if res:
                        _cache_put(stripped, source_lang, target_lang, res, detected)
                        return res, detected
                except Exception as fallback_exc:
                    logger.warning("MyMemory fallback also failed: %s", fallback_exc)

            lower_err = err_str.lower()
            if "nameresolutionerror" in lower_err or "failed to resolve" in lower_err or "connection refused" in lower_err:
                friendly_msg = "Network connection failed. Please check your internet connection."
            elif "timed out" in lower_err or "timeoutexpired" in lower_err:
                friendly_msg = "Translation server request timed out. Please try again."
            elif "too many requests" in lower_err or "rate limit" in lower_err:
                friendly_msg = "Translation rate limit exceeded. Please wait a moment."
            elif "forbidden" in lower_err or "unauthorized" in lower_err or "403" in lower_err:
                friendly_msg = "API authentication or access error."
            else:
                friendly_msg = f"Translation failed: {exc}"

            raise TranslationError(friendly_msg, original=exc) from exc

    def detect_language(self, text: str) -> str:
        """Detect language using high-accuracy *lingua* with confidence bounds and script heuristics."""
        return _detect_language_lingua(text)

    def get_supported_languages(self) -> dict[str, str]:
        """Return Google Translate's supported languages.

        Returns:
            A ``{code: name}`` dict, e.g. ``{"en": "english", …}``.
        """
        try:
            from deep_translator import GoogleTranslator  # type: ignore[import-untyped]

            langs: dict[str, str] = GoogleTranslator.get_supported_languages(
                as_dict=True
            )
            return langs
        except Exception:
            logger.exception("Failed to fetch Google supported languages")
            return {}


# ---------------------------------------------------------------------------
# DeepL backend
# ---------------------------------------------------------------------------

# Hardcoded DeepL language table (source & target codes).
_DEEPL_LANGUAGES: dict[str, str] = {
    "BG": "Bulgarian",
    "CS": "Czech",
    "DA": "Danish",
    "DE": "German",
    "EL": "Greek",
    "EN": "English",
    "EN-GB": "English (British)",
    "EN-US": "English (American)",
    "ES": "Spanish",
    "ET": "Estonian",
    "FI": "Finnish",
    "FR": "French",
    "HU": "Hungarian",
    "ID": "Indonesian",
    "IT": "Italian",
    "JA": "Japanese",
    "KO": "Korean",
    "LT": "Lithuanian",
    "LV": "Latvian",
    "NB": "Norwegian Bokmål",
    "NL": "Dutch",
    "PL": "Polish",
    "PT": "Portuguese",
    "PT-BR": "Portuguese (Brazilian)",
    "PT-PT": "Portuguese (European)",
    "RO": "Romanian",
    "RU": "Russian",
    "SK": "Slovak",
    "SL": "Slovenian",
    "SV": "Swedish",
    "TR": "Turkish",
    "UK": "Ukrainian",
    "ZH": "Chinese (simplified)",
}


class DeepLBackend(TranslationBackend):
    """Backend powered by the DeepL API via *deep_translator*.

    Args:
        api_key: Your DeepL API authentication key.
        use_free_api: If ``True``, use the free DeepL API endpoint.
    """

    def __init__(self, api_key: str = "", use_free_api: bool | None = None) -> None:
        self._api_key = (api_key or "").strip()
        # DeepL keys ending with :fx strictly belong to DeepL Free accounts
        if self._api_key.endswith(":fx"):
            self._use_free_api = True
        elif use_free_api is not None:
            self._use_free_api = bool(use_free_api)
        else:
            self._use_free_api = False

    @property
    def name(self) -> str:  # noqa: D401
        """Backend identifier."""
        return "deepl"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate *text* using the DeepL API.

        Raises:
            TranslationError: On network or authentication errors.
        """
        result, _ = self.translate_with_detection(text, source_lang, target_lang)
        return result

    def translate_with_detection(
        self, text: str, source_lang: str, target_lang: str
    ) -> tuple[str, str]:
        """Translate *text* using DeepL and return (translated_text, detected_language)."""
        stripped = text.strip()
        if not stripped:
            return "", source_lang if source_lang != "auto" else ""
        cached = _cache_get(stripped, source_lang, target_lang)
        if cached is not None:
            return cached

        if not self._api_key:
            raise TranslationError(
                "⚠️ Translation Error: DeepL API Key is missing. Please configure your key in Settings."
            )

        try:
            from deep_translator import DeeplTranslator  # type: ignore[import-untyped]
            from deep_translator.exceptions import (  # type: ignore[import-untyped]
                AuthorizationException,
                ApiKeyException,
                TooManyRequests,
                ServerException,
                RequestError,
            )

            # DeepL free vs pro routing: keys ending with :fx MUST use use_free_api=True
            is_free = True if self._api_key.endswith(":fx") else self._use_free_api

            source = source_lang if source_lang != "auto" else "auto"
            translator = DeeplTranslator(
                api_key=self._api_key,
                source=source,
                target=target_lang,
                use_free_api=is_free,
            )
            result: str = translator.translate(stripped)
            detected = self.detect_language(stripped) if source_lang == "auto" else source_lang
            logger.debug(
                "DeepL translated [%s→%s]: %s … → %s …",
                source_lang,
                target_lang,
                text[:40],
                result[:40] if result else "",
            )
            if result:
                _cache_put(stripped, source_lang, target_lang, result, detected)
            return result, detected
        except ImportError as exc:
            raise TranslationError(
                "⚠️ Translation Error: deep_translator package is not installed", original=exc
            ) from exc
        except AuthorizationException as exc:
            msg = "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings."
            logger.warning("DeepL authorization error: %s", exc)
            raise TranslationError(msg, original=exc) from exc
        except ApiKeyException as exc:
            msg = "⚠️ Translation Error: DeepL API Key is missing or invalid. Please check your settings."
            logger.warning("DeepL API key error: %s", exc)
            raise TranslationError(msg, original=exc) from exc
        except TooManyRequests as exc:
            msg = "⚠️ Translation Error: DeepL quota or rate limit exceeded. Please check your account."
            logger.warning("DeepL rate limit error: %s", exc)
            raise TranslationError(msg, original=exc) from exc
        except ServerException as exc:
            msg = f"⚠️ Translation Error: DeepL server error ({exc}). Please try again later."
            logger.warning("DeepL server error: %s", exc)
            raise TranslationError(msg, original=exc) from exc
        except RequestError as exc:
            msg = f"⚠️ Translation Error: DeepL network error ({exc}). Please check your connection."
            logger.warning("DeepL network error: %s", exc)
            raise TranslationError(msg, original=exc) from exc
        except TranslationError:
            raise
        except Exception as exc:
            exc_name = type(exc).__name__
            exc_str = str(exc)
            if "AuthorizationException" in exc_name or "unauthorized" in exc_str.lower() or "403" in exc_str:
                msg = "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings."
            elif "ApiKeyException" in exc_name or "api_key" in exc_str.lower():
                msg = "⚠️ Translation Error: DeepL API Key is missing or invalid. Please check your settings."
            elif "TooManyRequests" in exc_name or "quota" in exc_str.lower() or "429" in exc_str:
                msg = "⚠️ Translation Error: DeepL quota or rate limit exceeded. Please check your account."
            else:
                msg = f"⚠️ Translation Error: DeepL translation failed ({exc_str})."
            logger.warning("DeepL translation error: %s", exc)
            raise TranslationError(msg, original=exc) from exc

    def detect_language(self, text: str) -> str:
        """Detect language using high-accuracy *lingua* with confidence bounds."""
        return _detect_language_lingua(text)

    def get_supported_languages(self) -> dict[str, str]:
        """Return the hardcoded set of DeepL-supported languages."""
        return dict(_DEEPL_LANGUAGES)


# ---------------------------------------------------------------------------
# LibreTranslate backend
# ---------------------------------------------------------------------------
class LibreTranslateBackend(TranslationBackend):
    """Backend powered by a LibreTranslate instance via *deep_translator*.

    Args:
        base_url: URL of the LibreTranslate server.
    """

    def __init__(self, base_url: str = "https://libretranslate.com") -> None:
        self._base_url = base_url

    @property
    def name(self) -> str:  # noqa: D401
        """Backend identifier."""
        return "libretranslate"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        """Translate *text* using a LibreTranslate server.

        Raises:
            TranslationError: On network or server errors.
        """
        result, _ = self.translate_with_detection(text, source_lang, target_lang)
        return result

    def translate_with_detection(
        self, text: str, source_lang: str, target_lang: str
    ) -> tuple[str, str]:
        """Translate *text* using LibreTranslate and return (translated_text, detected_language)."""
        stripped = text.strip()
        if not stripped:
            return "", source_lang if source_lang != "auto" else ""
        cached = _cache_get(stripped, source_lang, target_lang)
        if cached is not None:
            return cached
        try:
            from deep_translator import LibreTranslator  # type: ignore[import-untyped]

            source = "auto" if source_lang == "auto" else source_lang
            translator = LibreTranslator(
                source=source,
                target=target_lang,
                base_url=self._base_url,
            )
            result: str = translator.translate(stripped)
            detected = self.detect_language(stripped) if source_lang == "auto" else source_lang
            logger.debug(
                "LibreTranslate [%s→%s]: %s … → %s …",
                source_lang,
                target_lang,
                text[:40],
                result[:40] if result else "",
            )
            if result:
                _cache_put(stripped, source_lang, target_lang, result, detected)
            return result, detected
        except ImportError as exc:
            raise TranslationError(
                "deep_translator package is not installed", original=exc
            ) from exc
        except Exception as exc:
            logger.exception("LibreTranslate error")
            raise TranslationError(
                f"LibreTranslate failed: {exc}", original=exc
            ) from exc

    def detect_language(self, text: str) -> str:
        """Detect language using high-accuracy *lingua* with confidence bounds."""
        return _detect_language_lingua(text)

    def get_supported_languages(self) -> dict[str, str]:
        """Query the LibreTranslate server for supported languages."""
        try:
            from deep_translator import LibreTranslator  # type: ignore[import-untyped]

            langs: dict[str, str] = LibreTranslator.get_supported_languages(
                as_dict=True
            )
            return langs
        except Exception:
            logger.exception("Failed to fetch LibreTranslate languages")
            return {}


# ---------------------------------------------------------------------------
# NVIDIA NIM (LLM) backend
# ---------------------------------------------------------------------------
class NvidiaNimBackend(TranslationBackend):
    """Backend powered by NVIDIA NIM (Inference Microservice) / LLM Chat Completion API.

    Uses an OpenAI-compatible /v1/chat/completions endpoint with a strict system
    prompt returning a structured JSON response:
    {"detected_language": "...", "translated_text": "..."}
    """

    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
    DEFAULT_MODEL = "meta/llama-3.1-8b-instruct"

    def __init__(
        self,
        api_key: str = "",
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
    ) -> None:
        import os

        self._api_key = (
            api_key
            or os.environ.get("NVIDIA_API_KEY", "")
            or os.environ.get("NIM_API_KEY", "")
        )
        self._base_url = (base_url or self.DEFAULT_BASE_URL).rstrip("/")
        self._model = model or self.DEFAULT_MODEL
        self._session = requests.Session()
        adapter = HTTPAdapter(pool_connections=5, pool_maxsize=10, max_retries=1)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)

    @property
    def name(self) -> str:
        return "nvidia_nim"

    def translate(self, text: str, source_lang: str, target_lang: str) -> str:
        result, _ = self.translate_with_detection(text, source_lang, target_lang)
        return result

    def translate_with_detection(
        self, text: str, source_lang: str, target_lang: str
    ) -> tuple[str, str]:
        stripped = text.strip()
        if not stripped:
            return "", source_lang if source_lang != "auto" else ""

        cached = _cache_get(stripped, source_lang, target_lang)
        if cached is not None:
            return cached

        if not self._api_key:
            raise TranslationError(
                "NVIDIA NIM API key is not configured. Please enter your API key in Settings."
            )

        system_prompt = (
            f"You are an expert translator. Translate the given text accurately into target language: '{target_lang}'.\n"
            "You must STRICTLY respond with a single valid JSON object only, with no markdown code fences, no backticks, and no extra text or commentary.\n"
            "The JSON object must have this exact structure:\n"
            '{\n'
            '  "detected_language": "<detected language name or ISO 639-1 code>",\n'
            '  "translated_text": "<translated string>"\n'
            '}\n'
            "If the source language is known, use that for detected_language. "
            "If the source language is 'auto', accurately detect the source language of the input text."
        )

        user_prompt = f"Source text:\n{stripped}"
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
            "max_tokens": 1024,
        }

        try:
            resp = self._session.post(url, headers=headers, json=payload, timeout=10.0)
            if resp.status_code != 200:
                raise TranslationError(
                    f"NVIDIA NIM API error ({resp.status_code}): {resp.text[:200]}"
                )

            data = resp.json()
            choices = data.get("choices", [])
            if not choices or not isinstance(choices, list):
                raise TranslationError("Empty response from NVIDIA NIM")

            message_content = choices[0].get("message", {}).get("content", "").strip()
            translated_text, detected_lang = self._parse_json_response(message_content, stripped)

            # Fallback if detection still empty or auto
            if not detected_lang or detected_lang == "auto":
                detected_lang = self.detect_language(stripped)

            if translated_text:
                _cache_put(stripped, source_lang, target_lang, translated_text, detected_lang)
            return translated_text, detected_lang

        except TranslationError:
            raise
        except Exception as exc:
            logger.exception("NVIDIA NIM translation failed")
            raise TranslationError(f"NVIDIA NIM translation error: {exc}", original=exc) from exc

    def _parse_json_response(self, content: str, original_text: str) -> tuple[str, str]:
        """Parse structured JSON from LLM response with fallback extraction."""
        cleaned = content.strip()
        # Strip markdown code blocks if the model enclosed response in ```json ... ```
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned)
            cleaned = cleaned.strip()

        translated = ""
        detected = ""

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                translated = str(parsed.get("translated_text", "")).strip()
                raw_detected = str(parsed.get("detected_language", "")).strip()
                detected = normalize_detected_language(raw_detected, original_text)
        except Exception as json_err:
            logger.debug("Direct JSON parse failed: %s; trying regex extraction", json_err)
            match_trans = re.search(r'"translated_text"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned, re.DOTALL)
            match_det = re.search(r'"detected_language"\s*:\s*"((?:[^"\\]|\\.)*)"', cleaned)
            if match_trans:
                try:
                    translated = json.loads(f'"{match_trans.group(1)}"')
                except Exception:
                    translated = match_trans.group(1)
            if match_det:
                detected = normalize_detected_language(match_det.group(1), original_text)

        # Fallback if model returned plain string
        if not translated and cleaned and not cleaned.startswith("{"):
            translated = cleaned
            detected = self.detect_language(original_text)

        return translated, detected

    def detect_language(self, text: str) -> str:
        return _detect_language_lingua(text)

    def get_supported_languages(self) -> dict[str, str]:
        from app.ui.ui_main import LANGUAGES
        return {code: name for code, name in LANGUAGES if code != "auto"}


# ---------------------------------------------------------------------------
# Backend registry helper
# ---------------------------------------------------------------------------
_BACKEND_NAMES: list[str] = ["google", "deepl", "libretranslate", "nvidia_nim"]


def _make_backend(name: str, **kwargs: Any) -> TranslationBackend:
    """Instantiate a backend by its short name.

    Args:
        name: One of ``"google"``, ``"deepl"``, ``"libretranslate"``, ``"nvidia_nim"``.
        **kwargs: Extra keyword arguments forwarded to the backend constructor
                  (e.g. ``api_key`` for DeepL or NVIDIA NIM).

    Returns:
        A ready-to-use :class:`TranslationBackend`.

    Raises:
        ValueError: If *name* is unknown.
    """
    if name == "google":
        return GoogleTranslateBackend()
    if name == "deepl":
        api_key = kwargs.get("api_key", "").strip()
        use_free_api = True if api_key.endswith(":fx") else kwargs.get("use_free_api", False)
        return DeepLBackend(api_key=api_key, use_free_api=use_free_api)
    if name == "libretranslate":
        base_url = kwargs.get("base_url", "https://libretranslate.com")
        return LibreTranslateBackend(base_url=base_url)
    if name in ("nvidia_nim", "nim", "llm"):
        api_key = kwargs.get("api_key", "")
        base_url = kwargs.get("base_url", NvidiaNimBackend.DEFAULT_BASE_URL)
        model = kwargs.get("model", NvidiaNimBackend.DEFAULT_MODEL)
        return NvidiaNimBackend(api_key=api_key, base_url=base_url, model=model)
    raise ValueError(f"Unknown backend: {name!r}")


# ---------------------------------------------------------------------------
# Translation manager
# ---------------------------------------------------------------------------
class TranslationManager:
    """Central manager that owns the active backend and persists config.

    Configuration is stored in ``~/.config/lingoflash/config.json`` and
    includes the selected backend name plus any backend-specific settings
    (API keys, server URLs, etc.).
    """

    def __init__(self, config_path: Optional[Path | str] = None) -> None:
        if config_path:
            self._config_file = Path(config_path)
        elif os.environ.get("LINGOFLASH_CONFIG_PATH"):
            self._config_file = Path(os.environ["LINGOFLASH_CONFIG_PATH"])
        else:
            self._config_file = _CONFIG_FILE

        self._config: dict[str, Any] = {}
        self._backend: TranslationBackend | None = None
        self.load_config()
        self._init_backend()

    # -- public API --------------------------------------------------------

    @property
    def backend(self) -> TranslationBackend:
        """Return the currently active backend.

        Raises:
            TranslationError: If no backend is initialised.
        """
        if self._backend is None:
            raise TranslationError("No translation backend is active")
        return self._backend

    def set_backend(self, backend_name: str, **kwargs: Any) -> None:
        """Switch the active backend and persist the choice.

        Args:
            backend_name: One of ``"google"``, ``"deepl"``, ``"libretranslate"``.
            **kwargs: Backend-specific configuration forwarded to the
                      constructor (e.g. ``api_key``).
        """
        self._backend = _make_backend(backend_name, **kwargs)
        self._config["backend"] = backend_name
        # Persist backend-specific kwargs (API keys, URLs).
        self._config.setdefault("backends", {})[backend_name] = kwargs
        self.save_config()
        logger.info("Switched to backend: %s", backend_name)

    def get_interface_language(self) -> str:
        """Return the user's preferred interface language code ('en', 'ru', 'uk', 'sv')."""
        return self._config.get("interface_language", "en")

    def set_interface_language(self, lang_code: str) -> None:
        """Persist user's preferred interface language code."""
        if lang_code in ("en", "ru", "uk", "sv"):
            self._config["interface_language"] = lang_code
            self.save_config()
            logger.info("Interface language set to: %s", lang_code)

    def get_target_language(self) -> str:
        """Return the preferred target language code (defaults to 'uk' or saved)."""
        return self._config.get("target_language", "uk")

    def set_target_language(self, lang_code: str) -> None:
        """Persist user's preferred target language code."""
        if lang_code and lang_code != "auto":
            self._config["target_language"] = lang_code
            self.save_config()
            logger.info("Target language set to: %s", lang_code)

    def get_theme(self) -> str:
        """Return the user's preferred UI theme identifier (defaults to 'system')."""
        return self._config.get("theme", "system")

    def set_theme(self, theme_id: str) -> None:
        """Persist the user's preferred UI theme."""
        if theme_id:
            self._config["theme"] = theme_id
            self.save_config()
            logger.info("UI theme set to: %s", theme_id)

    def get_ui_font_size(self) -> int:
        """Return the user's preferred global UI font size in points (defaults to 13)."""
        val = self._config.get("ui_font_size", 13)
        try:
            return max(10, min(36, int(val)))
        except (ValueError, TypeError):
            return 13

    def set_ui_font_size(self, font_size: int) -> None:
        """Persist the user's preferred global UI font size (range 10 to 36)."""
        clamped = max(10, min(36, int(font_size)))
        self._config["ui_font_size"] = clamped
        self.save_config()
        logger.info("UI font size set to: %d pt", clamped)

    def get_text_font_size(self) -> int:
        """Return the user's preferred translation editor font size in points (defaults to 14)."""
        val = self._config.get("text_font_size", self._config.get("font_size", 14))
        try:
            return max(10, min(36, int(val)))
        except (ValueError, TypeError):
            return 14

    def set_text_font_size(self, font_size: int) -> None:
        """Persist the user's preferred translation editor font size (range 10 to 36)."""
        clamped = max(10, min(36, int(font_size)))
        self._config["text_font_size"] = clamped
        self._config["font_size"] = clamped
        self.save_config()
        logger.info("Text font size set to: %d pt", clamped)

    def get_font_size(self) -> int:
        """Compatibility alias for get_text_font_size."""
        return self.get_text_font_size()

    def set_font_size(self, font_size: int) -> None:
        """Compatibility alias for set_text_font_size."""
        self.set_text_font_size(font_size)

    def get_shortcuts(self) -> dict[str, str]:
        """Return configured shortcuts merged with default shortcuts."""
        configured = self._config.get("shortcuts", {})
        merged = dict(DEFAULT_SHORTCUTS)
        if isinstance(configured, dict):
            merged.update(configured)
        return merged

    def get_shortcut(self, action: str) -> str:
        """Return the shortcut string for a specific action."""
        return self.get_shortcuts().get(action, DEFAULT_SHORTCUTS.get(action, ""))

    def set_shortcuts(self, shortcuts: dict[str, str]) -> None:
        """Persist updated shortcuts dictionary to config.json."""
        self._config["shortcuts"] = shortcuts
        self.save_config()
        logger.info("Persisted shortcuts to config: %s", shortcuts)

    def translate(self, text: str, source: str, target: str) -> str:
        """Translate *text* via the active backend.

        This is the primary entry-point used by UI code.

        Args:
            text: Source text.
            source: Source language code or ``"auto"``.
            target: Target language code.

        Returns:
            The translated text.
        """
        return self.backend.translate(text, source, target)

    def translate_with_detection(
        self, text: str, source: str, target: str
    ) -> tuple[str, str]:
        """Translate *text* and return (translated_text, detected_language) via active backend."""
        if hasattr(self.backend, "translate_with_detection"):
            return self.backend.translate_with_detection(text, source, target)
        translated = self.backend.translate(text, source, target)
        detected = self.backend.detect_language(text) if source == "auto" else source
        return translated, detected

    @staticmethod
    def get_backends() -> list[str]:
        """Return the list of registered backend identifiers."""
        return list(_BACKEND_NAMES)

    # -- config persistence ------------------------------------------------

    def save_config(self) -> None:
        """Write the current configuration to disk atomically."""
        self._config_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._config_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(
                json.dumps(self._config, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_file.replace(self._config_file)
            logger.debug("Config saved to %s", self._config_file)
        except OSError:
            logger.exception("Failed to save config")
            if tmp_file.is_file():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    def load_config(self) -> None:
        """Read configuration from disk (no-op if the file doesn't exist)."""
        if self._config_file.is_file():
            try:
                self._config = json.loads(
                    self._config_file.read_text(encoding="utf-8")
                )
                logger.debug("Config loaded from %s", self._config_file)
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to load config; using defaults")
                self._config = {}
        else:
            self._config = {}

    # -- internals ---------------------------------------------------------

    def _init_backend(self) -> None:
        """Initialise the backend based on the loaded config."""
        backend_name: str = self._config.get("backend", "google")
        kwargs: dict[str, Any] = (
            self._config.get("backends", {}).get(backend_name, {})
        )

        # Detect invalid or dummy placeholder API keys on startup and fall back safely
        if backend_name == "deepl":
            key = str(kwargs.get("api_key", "")).strip()
            if not key or key == "invalid-key:fx":
                logger.warning(
                    "DeepL API key is empty or dummy placeholder (%r); falling back to google backend",
                    key,
                )
                backend_name = "google"
                kwargs = self._config.get("backends", {}).get("google", {})
                self._config["backend"] = "google"
                self.save_config()
        elif backend_name == "nvidia_nim":
            key = str(kwargs.get("api_key", "")).strip()
            if not key or "invalid" in key.lower():
                logger.warning(
                    "NVIDIA NIM API key is empty or invalid; falling back to google backend"
                )
                backend_name = "google"
                kwargs = self._config.get("backends", {}).get("google", {})
                self._config["backend"] = "google"
                self.save_config()

        try:
            self._backend = _make_backend(backend_name, **kwargs)
        except ValueError:
            logger.warning(
                "Unknown backend %r in config; falling back to google",
                backend_name,
            )
            self._backend = GoogleTranslateBackend()


# ---------------------------------------------------------------------------
# Async translation worker (QThread)
# ---------------------------------------------------------------------------
class TranslationWorker(QThread):
    """Runs a translation in a background thread and emits Qt signals with generation IDs.

    Signals:
        finished(str, str, str, str, int): Emitted with
            ``(source_text, translated_text, source_lang, target_lang, request_id)``
            when the translation succeeds.
        error(str, int): Emitted with ``(error_message, request_id)`` on failure.
    """

    finished = pyqtSignal(str, str, str, str, int)
    error = pyqtSignal(str, int)
    worker_done = pyqtSignal()

    def __init__(
        self,
        manager: TranslationManager,
        text: str,
        source: str = "auto",
        target: str = "en",
        request_id: int = 0,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._text = text
        self._source = source
        self._target = target
        self._request_id = request_id
        self._is_cancelled = False

    def cancel(self) -> None:
        """Mark this worker as cancelled so any pending response is discarded."""
        self._is_cancelled = True

    def run(self) -> None:
        """Execute the translation and emit the appropriate signal."""
        try:
            if self._is_cancelled:
                return

            target_lang = self._target
            source_lang = self._source

            if self._is_cancelled:
                return

            # Smart single-call translation + detection
            translated = ""
            detected = source_lang
            use_single_call = False

            if hasattr(self._manager, "translate_with_detection"):
                try:
                    res = self._manager.translate_with_detection(self._text, source_lang, target_lang)
                    if isinstance(res, tuple) and len(res) == 2:
                        translated, detected = res
                        use_single_call = True
                except TranslationError:
                    raise
                except Exception as exc:
                    exc_name = type(exc).__name__
                    if "AuthorizationException" in exc_name or "unauthorized" in str(exc).lower():
                        raise TranslationError(
                            "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings.",
                            original=exc,
                        ) from exc
                    logger.debug("translate_with_detection failed (%s), falling back to two-step", exc)

            if not use_single_call:
                if source_lang == "auto":
                    try:
                        detected = self._manager.backend.detect_language(self._text)
                    except Exception:
                        detected = "auto"
                detected = normalize_detected_language(detected, self._text)

                if self._is_cancelled:
                    return

                if detected and detected != "auto" and detected == target_lang:
                    target_lang = "en" if target_lang != "en" else "uk"

                translated = self._manager.translate(self._text, source_lang, target_lang)
            else:
                if self._is_cancelled:
                    return

                # Normalize and verify detected language
                if source_lang == "auto":
                    if not detected or detected == "auto":
                        try:
                            detected = self._manager.backend.detect_language(self._text)
                        except Exception:
                            detected = "auto"
                    elif detected == "en" and any("\u0400" <= c <= "\u04FF" for c in self._text):
                        try:
                            detected = self._manager.backend.detect_language(self._text)
                        except Exception:
                            pass
                    detected = normalize_detected_language(detected, self._text)

                # If detected matches target_lang (e.g. text already in target lang), swap target and re-translate
                if source_lang == "auto" and detected and detected != "auto" and detected == target_lang:
                    swapped_target = "en" if target_lang != "en" else "uk"
                    if hasattr(self._manager, "translate_with_detection"):
                        try:
                            res = self._manager.translate_with_detection(self._text, detected, swapped_target)
                            if isinstance(res, tuple) and len(res) == 2:
                                translated, detected = res
                            else:
                                translated = self._manager.translate(self._text, detected, swapped_target)
                        except Exception:
                            translated = self._manager.translate(self._text, detected, swapped_target)
                    else:
                        translated = self._manager.translate(self._text, detected, swapped_target)
                    target_lang = swapped_target
                    detected = normalize_detected_language(detected, self._text)

            if self._is_cancelled:
                return

            self.finished.emit(self._text, translated, detected, target_lang, self._request_id)
        except TranslationError as exc:
            logger.warning("TranslationWorker error: %s", exc.message)
            if not self._is_cancelled:
                err_msg = exc.message
                if not err_msg.startswith("⚠️"):
                    err_msg = f"⚠️ Translation Error: {err_msg}"
                self.error.emit(err_msg, self._request_id)
        except Exception as exc:
            logger.warning("Unexpected error in TranslationWorker: %s", exc)
            if not self._is_cancelled:
                exc_str = str(exc)
                if "AuthorizationException" in type(exc).__name__ or "unauthorized" in exc_str.lower():
                    err_msg = "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings."
                elif "ApiKeyException" in type(exc).__name__:
                    err_msg = "⚠️ Translation Error: DeepL API Key is missing or invalid. Please check your settings."
                else:
                    err_msg = f"⚠️ Translation Error: {exc_str}"
                self.error.emit(err_msg, self._request_id)
        finally:
            self.worker_done.emit()


# ---------------------------------------------------------------------------
# Translation history
# ---------------------------------------------------------------------------
class TranslationHistory:
    """Persistent store for the last *max_entries* translations.

    History is serialised as JSON in
    ``~/.local/share/lingoflash/history.json``.
    """

    def __init__(
        self,
        max_entries: int = _MAX_HISTORY,
        history_path: Optional[Path | str] = None,
    ) -> None:
        if history_path:
            self._history_file = Path(history_path)
        elif os.environ.get("LINGOFLASH_HISTORY_PATH"):
            self._history_file = Path(os.environ["LINGOFLASH_HISTORY_PATH"])
        else:
            self._history_file = _HISTORY_FILE

        self._max_entries = max_entries
        self._entries: list[dict[str, str]] = []
        self._load()

    # -- public API --------------------------------------------------------

    def add(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str,
        target_lang: str,
        timestamp: str | None = None,
    ) -> None:
        """Append a translation record and auto-prune old entries.

        Args:
            source_text: Original text.
            translated_text: Translated text.
            source_lang: Source language code.
            target_lang: Target language code.
            timestamp: ISO-8601 timestamp. Defaults to *now* (UTC).
        """
        if timestamp is None:
            timestamp = datetime.now(tz=timezone.utc).isoformat()
        entry: dict[str, str] = {
            "source_text": source_text,
            "translated_text": translated_text,
            "source_lang": source_lang,
            "target_lang": target_lang,
            "timestamp": timestamp,
        }
        self._entries.append(entry)
        # Prune oldest entries beyond the limit.
        if len(self._entries) > self._max_entries:
            self._entries = self._entries[-self._max_entries :]
        self._save()
        logger.debug("History entry added (%d total)", len(self._entries))

    def get_all(self) -> list[dict[str, str]]:
        """Return all stored history entries (newest last)."""
        return list(self._entries)

    def clear(self) -> None:
        """Delete all history entries and persist the empty list."""
        self._entries.clear()
        self._save()
        logger.info("Translation history cleared")

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        """Load history from disk."""
        if self._history_file.is_file():
            try:
                data = json.loads(
                    self._history_file.read_text(encoding="utf-8")
                )
                if isinstance(data, list):
                    self._entries = data[-self._max_entries :]
                else:
                    self._entries = []
                logger.debug(
                    "Loaded %d history entries from %s",
                    len(self._entries),
                    self._history_file,
                )
            except (json.JSONDecodeError, OSError):
                logger.exception("Failed to load history; starting fresh")
                self._entries = []
        else:
            self._entries = []

    def _save(self) -> None:
        """Persist history to disk atomically."""
        self._history_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = self._history_file.with_suffix(".tmp")
        try:
            tmp_file.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            tmp_file.replace(self._history_file)
        except OSError:
            logger.exception("Failed to save history")
            if tmp_file.is_file():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass
