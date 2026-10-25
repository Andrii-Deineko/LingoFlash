"""Text-to-Speech (TTS) engine and background playback worker for LingoFlash.

Supports high-quality synthesis via gTTS with automatic language mapping (Swedish,
English, Ukrainian, Russian, etc.), instant cancellation, and fallback to QtTextToSpeech.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Standard ISO-639-1 to gTTS/TTS language code mappings
TTS_LANGUAGE_MAP: dict[str, str] = {
    "auto": "en",
    "af": "af",
    "ar": "ar",
    "bg": "bg",
    "bn": "bn",
    "bs": "bs",
    "ca": "ca",
    "cs": "cs",
    "cy": "cy",
    "da": "da",
    "de": "de",
    "el": "el",
    "en": "en",
    "eo": "eo",
    "es": "es",
    "et": "et",
    "fi": "fi",
    "fr": "fr",
    "gu": "gu",
    "he": "iw",  # Google TTS legacy Hebrew code
    "hi": "hi",
    "hr": "hr",
    "hu": "hu",
    "hy": "hy",
    "id": "id",
    "is": "is",
    "it": "it",
    "ja": "ja",
    "jw": "jw",
    "km": "km",
    "kn": "kn",
    "ko": "ko",
    "la": "la",
    "lv": "lv",
    "mk": "mk",
    "ml": "ml",
    "mr": "mr",
    "my": "my",
    "ne": "ne",
    "nl": "nl",
    "no": "no",
    "pl": "pl",
    "pt": "pt",
    "ro": "ro",
    "ru": "ru",
    "si": "si",
    "sk": "sk",
    "sq": "sq",
    "sr": "sr",
    "su": "su",
    "sv": "sv",
    "sw": "sw",
    "ta": "ta",
    "te": "te",
    "th": "th",
    "tl": "tl",
    "tr": "tr",
    "uk": "uk",
    "ur": "ur",
    "vi": "vi",
    "zh-CN": "zh-CN",
    "zh-TW": "zh-TW",
    "zh": "zh-CN",
}


def resolve_tts_language(lang_code: str) -> str:
    """Normalize language code to a valid TTS language code.

    Defaults to 'en' if 'auto' or unrecognized.
    """
    clean = (lang_code or "").strip().lower()
    if clean in ("auto", "", "detect"):
        return "en"

    # Exact or mapped code
    if lang_code in TTS_LANGUAGE_MAP:
        return TTS_LANGUAGE_MAP[lang_code]
    if clean in TTS_LANGUAGE_MAP:
        return TTS_LANGUAGE_MAP[clean]

    # Prefix match (e.g. en_US -> en)
    prefix = clean.split("-")[0].split("_")[0]
    return TTS_LANGUAGE_MAP.get(prefix, "en")


def get_audio_player_command() -> Optional[list[str]]:
    """Determine the optimal command line for non-blocking audio playback."""
    if shutil.which("pw-play"):
        return ["pw-play"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    if shutil.which("paplay"):
        return ["paplay"]
    if shutil.which("aplay"):
        return ["aplay"]
    return None


class TTSPlaybackWorker(QThread):
    """Background thread that synthesizes speech and plays audio without blocking GUI.

    Signals:
        playback_started(str): Emitted when audio playback actually begins, passing the role.
        playback_finished(str): Emitted when playback completes or is stopped.
        playback_error(str, str): Emitted on failure: (role, error_message).
    """

    playback_started = pyqtSignal(str)
    playback_finished = pyqtSignal(str)
    playback_error = pyqtSignal(str, str)

    def __init__(
        self,
        text: str,
        language_code: str,
        role: str = "source",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._text = text
        self._language_code = language_code
        self._role = role
        self._is_stopped = False
        self._process: Optional[subprocess.Popen] = None
        self._temp_file: Optional[str] = None

    @property
    def role(self) -> str:
        return self._role

    def stop(self) -> None:
        """Immediately terminate active audio synthesis or playback."""
        self._is_stopped = True
        if self._process is not None:
            try:
                self._process.terminate()
                self._process.wait(timeout=0.3)
            except Exception:
                try:
                    self._process.kill()
                except Exception:
                    pass
            finally:
                self._process = None

    def _cleanup_temp_file(self) -> None:
        if self._temp_file and os.path.isfile(self._temp_file):
            try:
                os.remove(self._temp_file)
            except OSError:
                pass
            self._temp_file = None

    def run(self) -> None:
        """Synthesize speech and play audio in background thread."""
        text = self._text.strip()
        if not text or self._is_stopped:
            self.playback_finished.emit(self._role)
            return

        tts_lang = resolve_tts_language(self._language_code)
        logger.info(
            "TTSPlaybackWorker starting (role=%s, lang=%s, orig_code=%s, chars=%d)",
            self._role,
            tts_lang,
            self._language_code,
            len(text),
        )

        # 1. Attempt gTTS synthesis (Google neural TTS)
        gtts_success = False
        try:
            from gtts import gTTS

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                self._temp_file = f.name

            tts = gTTS(text=text, lang=tts_lang, slow=False)
            tts.save(self._temp_file)

            if self._is_stopped:
                self._cleanup_temp_file()
                self.playback_finished.emit(self._role)
                return

            player_cmd = get_audio_player_command()
            if player_cmd:
                self.playback_started.emit(self._role)
                self._process = subprocess.Popen(
                    player_cmd + [self._temp_file],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                # Poll until process terminates or stop requested
                while self._process.poll() is None:
                    if self._is_stopped:
                        self._process.terminate()
                        try:
                            self._process.wait(timeout=0.2)
                        except Exception:
                            self._process.kill()
                        break
                    time.sleep(0.05)

                gtts_success = True
            else:
                logger.warning("No command-line audio player (pw-play, ffplay, paplay) found.")

        except Exception as exc:
            logger.warning("gTTS synthesis or playback failed: %s; trying QtTextToSpeech fallback", exc)

        finally:
            self._cleanup_temp_file()

        if self._is_stopped:
            self.playback_finished.emit(self._role)
            return

        # 2. Fallback to QtTextToSpeech if gTTS was unavailable or failed
        if not gtts_success:
            try:
                from PyQt6.QtTextToSpeech import QTextToSpeech
                from PyQt6.QtCore import QLocale

                tts_engine = QTextToSpeech()
                loc = QLocale(tts_lang)
                tts_engine.setLocale(loc)

                self.playback_started.emit(self._role)
                tts_engine.say(text)

                # Wait for speech to complete
                while tts_engine.state() == QTextToSpeech.State.Speaking:
                    if self._is_stopped:
                        tts_engine.stop()
                        break
                    time.sleep(0.05)

            except Exception as exc:
                logger.error("QtTextToSpeech fallback failed: %s", exc)
                self.playback_error.emit(self._role, f"Audio playback failed: {exc}")

        self.playback_finished.emit(self._role)


class TTSManager(QObject):
    """Centralized manager coordinating TTS playback, state, and mutual exclusion."""

    state_changed = pyqtSignal(str, bool)  # (role, is_playing)
    error_occurred = pyqtSignal(str, str)  # (role, error_message)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._active_worker: Optional[TTSPlaybackWorker] = None
        self._active_role: str = ""

    def is_playing(self, role: Optional[str] = None) -> bool:
        """Check if audio is currently playing globally or for a specific role."""
        if self._active_worker is None:
            return False
        if role is None:
            return True
        return self._active_role == role

    def stop(self) -> None:
        """Stop any active audio playback immediately."""
        if self._active_worker is not None:
            old_role = self._active_role
            try:
                self._active_worker.stop()
                self._active_worker.wait(200)
            except RuntimeError:
                pass
            finally:
                self._active_worker = None
                self._active_role = ""
            if old_role:
                self.state_changed.emit(old_role, False)

    def play_or_toggle(self, role: str, text: str, language_code: str) -> None:
        """Toggle playback: if active for this role, stop; otherwise stop other and play."""
        if self.is_playing(role):
            # Same button clicked while playing -> toggle off / stop
            self.stop()
            return

        # Stop previous playback if any
        self.stop()

        if not text.strip():
            return

        worker = TTSPlaybackWorker(
            text=text,
            language_code=language_code,
            role=role,
            parent=self,
        )
        self._active_worker = worker
        self._active_role = role

        worker.playback_started.connect(self._on_started)
        worker.playback_finished.connect(self._on_finished)
        worker.playback_error.connect(self._on_error)
        worker.start()

    def _on_started(self, role: str) -> None:
        self.state_changed.emit(role, True)

    def _on_finished(self, role: str) -> None:
        if self._active_role == role:
            self._active_worker = None
            self._active_role = ""
        self.state_changed.emit(role, False)

    def _on_error(self, role: str, msg: str) -> None:
        self.error_occurred.emit(role, msg)
        self._on_finished(role)
