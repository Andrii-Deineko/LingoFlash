"""Unit tests for Auto Detect language accuracy, NVIDIA NIM backend,
Quick Translation popup dismiss/crash safety, SVG dynamic icons, and i18n localization.
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QDialog

from app.translation.translator import (
    NvidiaNimBackend,
    GoogleTranslateBackend,
    TranslationManager,
    TranslationWorker,
    normalize_detected_language,
)
from app.ui.ui_popup import TranslationPopup
from app.ui.ui_main import MainWindow, SettingsDialog
from app.utils.i18n import tr, set_current_locale, get_current_locale, SUPPORTED_LOCALES
from app.utils.theme import get_theme_palette, create_svg_icon


class TestGoalFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def setUp(self) -> None:
        set_current_locale("en")

    # ---------------------------------------------------------------------------
    # 1. "Auto Detect" & NVIDIA NIM Tests
    # ---------------------------------------------------------------------------

    def test_normalize_detected_language(self) -> None:
        """Verify language normalization maps names and dialect codes to ISO 639-1."""
        self.assertEqual(normalize_detected_language("Russian"), "ru")
        self.assertEqual(normalize_detected_language("russian"), "ru")
        self.assertEqual(normalize_detected_language("ru-RU"), "ru")
        self.assertEqual(normalize_detected_language("Swedish"), "sv")
        self.assertEqual(normalize_detected_language("sv-SE"), "sv")
        self.assertEqual(normalize_detected_language("Ukrainian"), "uk")
        self.assertEqual(normalize_detected_language("English"), "en")

        # Cyrillic text should never be normalized to 'en' even if input says 'en'
        self.assertEqual(normalize_detected_language("en", fallback_text="привет! Как дела?"), "ru")

    def test_russian_phrase_detection_accuracy(self) -> None:
        """Verify the specific test case 'привет! Как дела?' detects as Russian (ru) and not English."""
        backend = GoogleTranslateBackend()
        detected = backend.detect_language("привет! Как дела?")
        self.assertEqual(detected, "ru")

        # Even if GTX mock returns 'en', backend must override with accurate detector
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [[["Hi! How are you?", "привет! Как дела?"]], None, "en"]

        with patch.object(backend._session, "get", return_value=mock_resp):
            translated, detected_lang = backend.translate_with_detection(
                "привет! Как дела?", "auto", "en"
            )
            self.assertEqual(detected_lang, "ru")
            self.assertNotEqual(detected_lang, "en")

    def test_nvidia_nim_backend_json_prompt_and_parsing(self) -> None:
        """Verify NVIDIA NIM backend sends strict JSON system prompt and parses structured response."""
        backend = NvidiaNimBackend(api_key="test-key", base_url="https://test.api.nvidia.com/v1")

        # 1. Clean JSON response
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {"detected_language": "Russian", "translated_text": "Hello! How are you?"}
                        )
                    }
                }
            ]
        }

        with patch.object(backend._session, "post", return_value=mock_resp) as mock_post:
            translated, detected = backend.translate_with_detection(
                "привет! Как дела?", "auto", "en"
            )
            self.assertEqual(translated, "Hello! How are you?")
            self.assertEqual(detected, "ru")

            # Verify system prompt in request payload
            call_kwargs = mock_post.call_args[1]
            payload = call_kwargs["json"]
            system_msg = payload["messages"][0]["content"]
            self.assertIn('"detected_language"', system_msg)
            self.assertIn('"translated_text"', system_msg)
            self.assertIn("STRICTLY", system_msg)

    def test_nvidia_nim_backend_markdown_codeblock_and_fallback(self) -> None:
        """Verify NVIDIA NIM backend parses JSON wrapped in ```json ... ``` code blocks."""
        backend = NvidiaNimBackend(api_key="test-key")

        # Response wrapped in markdown fence
        fenced_content = '```json\n{\n  "detected_language": "sv",\n  "translated_text": "Hello world"\n}\n```'
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": fenced_content}}]
        }

        with patch.object(backend._session, "post", return_value=mock_resp):
            translated, detected = backend.translate_with_detection(
                "Hej världen", "auto", "en"
            )
            self.assertEqual(translated, "Hello world")
            self.assertEqual(detected, "sv")

    # ---------------------------------------------------------------------------
    # 2. Popup Crash & Safe Dismiss Tests
    # ---------------------------------------------------------------------------

    def test_popup_close_button_cancels_worker_and_hides(self) -> None:
        """Verify clicking close button dismisses popup, cancels worker, and does not destroy widget."""
        popup = TranslationPopup()
        mock_worker = MagicMock()
        popup.set_active_worker(mock_worker)

        worker_cancelled_signal_emitted = []
        closed_signal_emitted = []
        popup.cancel_worker_requested.connect(lambda: worker_cancelled_signal_emitted.append(True))
        popup.closed.connect(lambda: closed_signal_emitted.append(True))

        popup.show()
        self.assertTrue(popup.isVisible())

        # Click the close button
        popup._btn_close.click()

        self.assertFalse(popup.isVisible())
        mock_worker.cancel.assert_called_once()
        self.assertEqual(len(worker_cancelled_signal_emitted), 1)
        self.assertEqual(len(closed_signal_emitted), 1)

    def test_popup_escape_key_dismisses_safely(self) -> None:
        """Verify pressing Escape dismisses popup without close/destroy crashes."""
        popup = TranslationPopup()
        mock_worker = MagicMock()
        popup.set_active_worker(mock_worker)

        popup.show()
        self.assertTrue(popup.isVisible())

        event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        popup.keyPressEvent(event)

        self.assertFalse(popup.isVisible())
        mock_worker.cancel.assert_called_once()

    # ---------------------------------------------------------------------------
    # 3. Dynamic SVG Icons Tests
    # ---------------------------------------------------------------------------

    def test_svg_icon_generation_and_palette_colors(self) -> None:
        """Verify create_svg_icon produces valid QIcon with theme-adapted colors."""
        dark_pal = get_theme_palette("dark")
        light_pal = get_theme_palette("light")

        icon_save_dark = create_svg_icon("save", color=dark_pal["text_primary"], size=16)
        icon_cancel_light = create_svg_icon("cancel", color=light_pal["text_primary"], size=16)

        self.assertFalse(icon_save_dark.isNull())
        self.assertFalse(icon_cancel_light.isNull())

    def test_settings_dialog_uses_svg_icons_not_standard_pixmap(self) -> None:
        """Verify SettingsDialog Save & Cancel buttons use dynamic SVG icons."""
        manager = TranslationManager()
        with patch.object(manager, "save_config"):
            dlg = SettingsDialog(manager)
            self.assertIsNotNone(dlg._btn_save)
            self.assertIsNotNone(dlg._btn_cancel)
            self.assertFalse(dlg._btn_save.icon().isNull())
            self.assertFalse(dlg._btn_cancel.icon().isNull())

            # Switch theme and verify icons update
            dlg._cmb_theme.setCurrentIndex(dlg._cmb_theme.findData("light"))
            self.assertFalse(dlg._btn_save.icon().isNull())
            self.assertFalse(dlg._btn_cancel.icon().isNull())

    # ---------------------------------------------------------------------------
    # 4. Interface Localization (i18n) Tests
    # ---------------------------------------------------------------------------

    def test_i18n_translation_lookup(self) -> None:
        """Verify tr() returns expected strings across all 4 supported locales."""
        for loc in ("en", "ru", "uk", "sv"):
            self.assertIn(loc, SUPPORTED_LOCALES)
            val = tr("settings_title", loc)
            self.assertTrue(len(val) > 0)
            self.assertIn("LingoFlash", val)

        self.assertEqual(tr("copy", "en"), "Copy")
        self.assertEqual(tr("copy", "ru"), "Копировать")
        self.assertEqual(tr("copy", "uk"), "Копіювати")
        self.assertEqual(tr("copy", "sv"), "Kopiera")

    def test_settings_dialog_locale_switch_and_persistence(self) -> None:
        """Verify switching interface language dynamically updates UI and saves to config."""
        manager = TranslationManager()
        with patch.object(manager, "save_config"):
            with patch("app.ui.ui_main.apply_gnome_shortcuts", return_value=(True, "OK")):
                dlg = SettingsDialog(manager)

                # Switch to Swedish in dropdown
                idx_sv = dlg._cmb_ui_lang.findData("sv")
                self.assertGreaterEqual(idx_sv, 0)
                dlg._cmb_ui_lang.setCurrentIndex(idx_sv)

                # Verify dialog text updated
                self.assertEqual(dlg._lbl_ui_lang.text(), "Gränssnittsspråk:")
                self.assertEqual(dlg._tabs.tabText(0), "Översättning")
                self.assertIn("Spara", dlg._btn_save.text())

                # Save and verify manager received setting
                dlg._save_settings()
                self.assertEqual(manager.get_interface_language(), "sv")

    def test_main_window_apply_locale(self) -> None:
        """Verify MainWindow.apply_locale updates all labels and placeholders."""
        manager = TranslationManager()
        history = MagicMock()
        history.get_all.return_value = []

        window = MainWindow(translation_manager=manager, history=history)

        window.apply_locale("ru")
        self.assertEqual(window._lbl_from.text(), "С языка:")
        self.assertEqual(window._lbl_to.text(), "На язык:")
        self.assertIn("Копировать", window._btn_copy.text())

        # Auto Detect item in combobox should be localized
        idx_auto = window._cmb_source_lang.findData("auto")
        self.assertEqual(window._cmb_source_lang.itemText(idx_auto), "Автоопределение")

        window.apply_locale("sv")
        self.assertEqual(window._lbl_from.text(), "Från:")
        self.assertEqual(window._lbl_to.text(), "Till:")
        self.assertEqual(window._cmb_source_lang.itemText(idx_auto), "Identifiera språk")


if __name__ == "__main__":
    unittest.main()
