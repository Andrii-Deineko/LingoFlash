"""Tests for Advanced Theming Engine and Smart OCR Overlay (Comic/Game Mode)."""

from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QApplication

from app.translation.translator import TranslationManager
from app.ui.ui_main import SettingsDialog
from app.ui.ui_ocr_overlay import OCROverlayWindow, TranslatedBubbleCard
from app.ocr.ocr_engine import OCROverlayWorker
from app.utils.theme import THEMES, get_available_themes, get_stylesheet


class TestThemes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_get_available_themes(self) -> None:
        """Verify get_available_themes returns all 11 themes plus system option."""
        themes = get_available_themes()
        self.assertGreaterEqual(len(themes), 12)
        theme_ids = [t[0] for t in themes]
        self.assertIn("system", theme_ids)
        self.assertIn("dark", theme_ids)
        self.assertIn("light", theme_ids)
        self.assertIn("nord", theme_ids)
        self.assertIn("dracula", theme_ids)
        self.assertIn("amoled", theme_ids)
        self.assertIn("catppuccin", theme_ids)
        self.assertIn("tokyo_night", theme_ids)
        self.assertIn("solarized_dark", theme_ids)
        self.assertIn("solarized_light", theme_ids)
        self.assertIn("gruvbox", theme_ids)
        self.assertIn("high_contrast", theme_ids)

    def test_theme_qss_compilation(self) -> None:
        """Verify every theme generates valid, non-empty QSS stylesheet."""
        for theme_id in THEMES:
            qss = get_stylesheet(theme_id)
            self.assertIsInstance(qss, str)
            self.assertGreater(len(qss), 500)
            # Verify critical components are styled
            self.assertIn("QMainWindow", qss)
            self.assertIn("QSplitter::handle", qss)
            self.assertIn("QPushButton#swapBtn", qss)
            self.assertIn("QComboBox", qss)

    def test_theme_manager_persistence(self) -> None:
        """Verify theme preference persists via TranslationManager."""
        mgr = TranslationManager()
        with patch.object(mgr, "save_config"):
            mgr.set_theme("nord")
            self.assertEqual(mgr.get_theme(), "nord")

            mgr.set_theme("dracula")
            self.assertEqual(mgr.get_theme(), "dracula")

    def test_settings_dialog_appearance_tab(self) -> None:
        """Verify theme preview and cancel/revert in SettingsDialog."""
        mgr = TranslationManager()
        with patch.object(mgr, "save_config"):
            mgr.set_theme("dark")

            app = QApplication.instance()
            app.setStyleSheet(get_stylesheet("dark"))

            dlg = SettingsDialog(mgr)
            # Find and select 'nord'
            idx = dlg._cmb_theme.findData("nord")
            self.assertGreaterEqual(idx, 0)
            dlg._cmb_theme.setCurrentIndex(idx)

            # Check that live preview was triggered
            current_qss = app.styleSheet()
            self.assertIn(THEMES["nord"]["bg_app"], current_qss)

            # Reject/Cancel reverts to initial theme
            dlg.reject()
            reverted_qss = app.styleSheet()
            self.assertIn(THEMES["dark"]["bg_app"], reverted_qss)


class TestOCROverlay(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_bubble_card_creation_and_copy(self) -> None:
        """Verify TranslatedBubbleCard sets up labels and copies text on click."""
        card = TranslatedBubbleCard(
            original_text="Hallo Welt",
            translated_text="Hello World",
            box=(50, 100, 120, 30),
        )
        self.assertEqual(card._original, "Hallo Welt")
        self.assertEqual(card._translated, "Hello World")
        self.assertEqual(card._box, (50, 100, 120, 30))
        self.assertEqual(card._lbl_text.text(), "Hello World")
        self.assertIn("Hallo Welt", card.toolTip())

        with patch.object(card._clipboard, "copy_to_clipboard") as mock_copy:
            click_event = QMouseEvent(
                QMouseEvent.Type.MouseButtonPress,
                QPointF(10.0, 10.0),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            card.mousePressEvent(click_event)
            mock_copy.assert_called_once_with("Hello World")
            self.assertEqual(card._lbl_copied.text(), "✓ Copied!")

    def test_overlay_window_creation_and_esc_dismissal(self) -> None:
        """Verify OCROverlayWindow positions bubbles and closes on Esc key."""
        blocks = [
            {"box": (20, 30, 100, 25), "text": "Konnichiwa", "translated": "Hello"},
            {"box": (150, 80, 120, 30), "text": "Arigatou", "translated": "Thank you"},
        ]
        window = OCROverlayWindow(blocks=blocks, screen_rect=(100, 100, 400, 300))
        self.assertEqual(len(window._cards), 2)

        # Verify Esc key dismisses window
        esc_event = QKeyEvent(
            QKeyEvent.Type.KeyPress,
            Qt.Key.Key_Escape,
            Qt.KeyboardModifier.NoModifier,
        )
        window.keyPressEvent(esc_event)
        self.assertFalse(window.isVisible())

    def test_overlay_worker_translation(self) -> None:
        """Verify OCROverlayWorker extracts blocks and translates them."""
        mock_mgr = MagicMock()
        mock_mgr.translate.return_value = "Translated Bubble"

        worker = OCROverlayWorker(
            translation_manager=mock_mgr,
            target_lang="en",
            ocr_lang="eng",
            output_path="/tmp/fake_ocr.png",
        )

        with patch("app.ocr.ocr_engine.ScreenCapture.capture_region", return_value="/tmp/fake_ocr.png"), \
             patch("os.path.isfile", return_value=True), \
             patch("app.ocr.ocr_engine.OCREngine.extract_overlay_blocks") as mock_extract:
            mock_extract.return_value = [
                {"box": (10, 15, 80, 20), "text": "Original Bubble"}
            ]

            results = []
            worker.overlay_ready.connect(lambda blocks, geom: results.append((blocks, geom)))
            worker.run()

            self.assertEqual(len(results), 1)
            blocks, _ = results[0]
            self.assertEqual(len(blocks), 1)
            self.assertEqual(blocks[0]["text"], "Original Bubble")
            self.assertEqual(blocks[0]["translated"], "Translated Bubble")


if __name__ == "__main__":
    unittest.main()
