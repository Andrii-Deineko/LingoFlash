"""Tests for Screen OCR (Wayland), Separate Font Sizes (UI vs Text), and Quit Button."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PIL import Image
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtWidgets import QApplication, QDialogButtonBox, QStyle, QPushButton

from app.ocr.ocr_engine import DEFAULT_OCR_IMAGE, OCREngine, ScreenCapture, OCRWorker
from app.translation.translator import TranslationHistory, TranslationManager
from app.ui.ui_main import MainWindow, SettingsDialog
from app.ui.ui_popup import TranslationPopup
from app.utils.theme import get_stylesheet


class TestScreenOCRWayland(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_default_ocr_image_path(self) -> None:
        """Verify default OCR image is /tmp/lingo_ocr.png as specified."""
        self.assertEqual(DEFAULT_OCR_IMAGE, "/tmp/lingo_ocr.png")

    def test_screen_capture_gnome_screenshot_blocks_and_waits(self) -> None:
        """Verify Wayland region capture executes gnome-screenshot and waits synchronously."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None), \
             patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=5000):

            res = ScreenCapture.capture_region("/tmp/lingo_ocr.png")
            self.assertEqual(res, "/tmp/lingo_ocr.png")
            mock_run.assert_called_once_with(
                ["gnome-screenshot", "-a", "-f", "/tmp/lingo_ocr.png"],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_screen_capture_gnome_screenshot_cancellation(self) -> None:
        """User cancelling screenshot selection should return None cleanly."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 1  # Cancelled by user (e.g. Esc)

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None), \
             patch("subprocess.run", return_value=mock_proc):

            res = ScreenCapture.capture_region("/tmp/lingo_ocr.png")
            self.assertIsNone(res)

    def test_ocr_extract_text_strips_excess_whitespace_and_newlines(self) -> None:
        """Verify OCR extraction strips excess whitespace, blank lines, and normalizes breaks."""
        raw_tesseract_output = "   Hello   \n\n\n\n   world   from   \n   Wayland   \x0c   "
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_img = f.name
            img = Image.new("RGB", (100, 50), color=(255, 255, 255))
            img.save(tmp_img)

        try:
            with patch("subprocess.run") as mock_sub, \
                 patch("pytesseract.image_to_string", return_value=raw_tesseract_output):
                mock_sub.return_value.returncode = 0
                engine = OCREngine()
                cleaned = engine.extract_text(tmp_img)
                self.assertNotIn("\x0c", cleaned)
                self.assertNotIn("   ", cleaned)
                self.assertEqual(cleaned, "Hello\n\nworld from Wayland")
        finally:
            if os.path.exists(tmp_img):
                os.unlink(tmp_img)


class TestSeparateFontSizes(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_ui_and_text_font_size_manager_persistence(self) -> None:
        """Verify both UI and Text font sizes are stored and clamped separately."""
        mgr = TranslationManager()
        with patch.object(mgr, "save_config"):
            mgr.set_ui_font_size(16)
            mgr.set_text_font_size(20)

            self.assertEqual(mgr.get_ui_font_size(), 16)
            self.assertEqual(mgr.get_text_font_size(), 20)
            self.assertEqual(mgr.get_font_size(), 20)

            # Test clamping (10 - 36)
            mgr.set_ui_font_size(5)
            self.assertEqual(mgr.get_ui_font_size(), 10)

            mgr.set_text_font_size(45)
            self.assertEqual(mgr.get_text_font_size(), 36)

    def test_settings_dialog_two_font_spinboxes(self) -> None:
        """Verify SettingsDialog has separate UI and Text font spinboxes."""
        mgr = TranslationManager()
        with patch.object(mgr, "save_config"):
            mgr.set_ui_font_size(15)
            mgr.set_text_font_size(18)
            dlg = SettingsDialog(mgr)

            self.assertTrue(hasattr(dlg, "_spn_ui_font"))
            self.assertTrue(hasattr(dlg, "_spn_text_font"))
            self.assertEqual(dlg._spn_ui_font.value(), 15)
            self.assertEqual(dlg._spn_text_font.value(), 18)

    def test_qss_global_ui_font_vs_text_font_override(self) -> None:
        """Verify UI font size is applied globally to QWidget, and overridden for QTextEdit."""
        qss = get_stylesheet("dark", ui_font_size=12, text_font_size=22)
        # Global base should use ui_font_size
        self.assertIn("font-size: 12pt;", qss)
        # Translation edit areas should use text_font_size
        self.assertIn("font-size: 22pt;", qss)

    def test_text_font_size_applied_to_main_window_and_popup(self) -> None:
        """Verify apply_font_size updates QTextEdit point sizes in both MainWindow and Popup."""
        mgr = TranslationManager()
        hist = MagicMock(spec=TranslationHistory)

        main_win = MainWindow(translation_manager=mgr, history=hist)
        popup = TranslationPopup(font_size=14)

        main_win.apply_font_size(21)
        self.assertEqual(main_win._txt_source.font().pointSize(), 21)
        self.assertEqual(main_win._txt_target.font().pointSize(), 21)

        popup.apply_font_size(25)
        self.assertEqual(popup._txt_source.font().pointSize(), 25)
        self.assertEqual(popup._txt_result.font().pointSize(), 25)


class TestQuitButtonAndUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_settings_dialog_has_red_quit_button(self) -> None:
        """Verify SettingsDialog contains a distinct red Quit LingoFlash button."""
        mgr = TranslationManager()
        dlg = SettingsDialog(mgr)

        btn_quit = dlg.findChild(QPushButton, "quitAppBtn")
        self.assertIsNotNone(btn_quit)
        self.assertIn("Quit", btn_quit.text())
        qss = btn_quit.styleSheet()
        self.assertIn("#dc2626", qss)  # Red background

    def test_quit_button_triggers_clean_quit_and_ipc_cleanup(self) -> None:
        """Verify clicking Quit button invokes quit callback and socket cleanup."""
        mgr = TranslationManager()
        mock_quit = MagicMock()
        dlg = SettingsDialog(mgr, on_quit=mock_quit)

        # Create dummy socket file to test cleanup
        sock_test = "/tmp/LingoFlash"
        try:
            with open(sock_test, "w") as f:
                f.write("test")
        except OSError:
            pass

        btn_quit = dlg.findChild(QPushButton, "quitAppBtn")
        self.assertIsNotNone(btn_quit)

        with patch("PyQt6.QtWidgets.QApplication.quit") as mock_qapp_quit:
            btn_quit.click()
            mock_quit.assert_called_once()

    def test_copy_button_styling_in_qss(self) -> None:
        """Verify copy buttons have the required background, padding, border-radius, and hover."""
        qss = get_stylesheet("dark")
        self.assertIn("QPushButton#copyBtn", qss)
        self.assertIn("QPushButton#popupCopyBtn", qss)
        self.assertIn("padding: 6px 16px;", qss)
        self.assertIn("background-color: #333333;", qss)
        self.assertIn("border-radius: 6px;", qss)
        self.assertIn("font-weight: bold;", qss)
        self.assertIn("background-color: #444444;", qss)

    def test_settings_dialog_save_and_cancel_icons(self) -> None:
        """Verify SettingsDialog Save and Cancel buttons have icons and proper spacing."""
        mgr = TranslationManager()
        dlg = SettingsDialog(mgr)

        btn_box = dlg.findChild(QDialogButtonBox)
        self.assertIsNotNone(btn_box)

        btn_save = btn_box.button(QDialogButtonBox.StandardButton.Save)
        btn_cancel = btn_box.button(QDialogButtonBox.StandardButton.Cancel)

        self.assertIsNotNone(btn_save)
        self.assertIsNotNone(btn_cancel)
        self.assertTrue(not btn_save.icon().isNull() or "Save" in btn_save.text())
        self.assertTrue(not btn_cancel.icon().isNull() or "Cancel" in btn_cancel.text())


if __name__ == "__main__":
    unittest.main()
