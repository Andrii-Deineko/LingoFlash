"""Voice input worker using SpeechRecognition."""

from __future__ import annotations

import logging
from typing import Optional

from PyQt6.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger(__name__)

# Map 2-letter language codes to BCP-47 speech recognition locales
LANGUAGE_TO_SPEECH_LOCALE: dict[str, str] = {
    "auto": "en-US",
    "en": "en-US",
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


class VoiceInputWorker(QThread):
    """Background worker for capturing and transcribing microphone audio.

    Signals:
        text_captured(str): Emitted with the transcribed text.
        status_changed(str): Emitted on state transitions (Listening, Processing).
        error(str): Emitted on microphone or recognition errors.
        finished(): Emitted when recording / processing ends.
    """

    text_captured = pyqtSignal(str)
    status_changed = pyqtSignal(str)
    error = pyqtSignal(str)
    finished = pyqtSignal()

    def __init__(
        self,
        language_code: str = "en",
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._lang_code = language_code
        self._speech_locale = LANGUAGE_TO_SPEECH_LOCALE.get(language_code, "en-US")
        self._is_active = True

    def stop(self) -> None:
        """Request immediate worker stop."""
        self._is_active = False

    def run(self) -> None:
        """Record audio from default microphone and transcribe via Google Speech API."""
        try:
            import speech_recognition as sr
        except ImportError:
            self.error.emit("SpeechRecognition is not installed. Run: pip install SpeechRecognition")
            self.finished.emit()
            return

        recognizer = sr.Recognizer()
        recognizer.energy_threshold = 300
        recognizer.dynamic_energy_threshold = True

        try:
            with sr.Microphone() as source:
                if not self._is_active:
                    self.finished.emit()
                    return

                self.status_changed.emit("Adjusting for ambient noise…")
                recognizer.adjust_for_ambient_noise(source, duration=0.4)

                if not self._is_active:
                    self.finished.emit()
                    return

                self.status_changed.emit("Listening… Speak into microphone")
                logger.info("VoiceInputWorker listening (locale=%s)", self._speech_locale)

                # Listen for speech with a timeout
                audio = recognizer.listen(source, timeout=6.0, phrase_time_limit=15.0)

            if not self._is_active:
                self.finished.emit()
                return

            self.status_changed.emit("Transcribing audio…")
            text = recognizer.recognize_google(audio, language=self._speech_locale)
            if text and text.strip():
                logger.info("Transcribed text: %s", text)
                self.text_captured.emit(text.strip())
            else:
                self.status_changed.emit("No speech recognized")

        except sr.WaitTimeoutError:
            logger.info("Voice recording timed out without speech")
            self.status_changed.emit("Listening timed out")
        except sr.UnknownValueError:
            logger.info("Could not understand audio")
            self.status_changed.emit("Could not understand audio")
        except sr.RequestError as exc:
            logger.warning("Speech recognition request error: %s", exc)
            self.error.emit(f"Speech recognition service error: {exc}")
        except OSError as exc:
            logger.warning("Microphone device error: %s", exc)
            self.error.emit(f"Microphone device error: {exc}")
        except Exception as exc:
            logger.exception("Unexpected error in VoiceInputWorker")
            self.error.emit(f"Voice input error: {exc}")
        finally:
            self.finished.emit()
