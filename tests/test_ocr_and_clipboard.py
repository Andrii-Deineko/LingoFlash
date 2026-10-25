"""Tests for OCR engine preprocessing, screen capture, and clipboard manager."""

import os
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.clipboard.clipboard_manager import ClipboardManager
from app.ocr.ocr_engine import OCREngine, ScreenCapture


class TestOCREngine(unittest.TestCase):
    def test_postprocess_text_normalisation(self) -> None:
        """Verify OCR text post-processing collapses linebreaks and preserves paragraphs."""
        raw_ocr = "  This is line one\nof a paragraph. \n\nThis is paragraph two,\nstill line two.  "
        expected = "This is line one of a paragraph.\n\nThis is paragraph two, still line two."
        cleaned = OCREngine._postprocess_text(raw_ocr)
        self.assertEqual(cleaned, expected)

    def test_postprocess_empty_text(self) -> None:
        self.assertEqual(OCREngine._postprocess_text("   \n\n  "), "")

    def test_postprocess_multiple_spaces(self) -> None:
        raw = "Word1     Word2   \n  Word3"
        self.assertEqual(OCREngine._postprocess_text(raw), "Word1 Word2 Word3")


class TestScreenCapture(unittest.TestCase):
    def test_detect_session_type(self) -> None:
        """Test session detection via WAYLAND_DISPLAY and XDG_SESSION_TYPE."""
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=True):
            self.assertEqual(ScreenCapture._detect_session_type(), "wayland")

        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "wayland"}, clear=True):
            self.assertEqual(ScreenCapture._detect_session_type(), "wayland")

        with patch.dict(os.environ, {"XDG_SESSION_TYPE": "x11"}, clear=True):
            self.assertEqual(ScreenCapture._detect_session_type(), "x11")

    def test_capture_region_gnome_screenshot_success(self) -> None:
        """Verify capture_region calls gnome-screenshot -a -f /tmp/lingoflash_ocr.png."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None), \
             patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=1234):

            target = "/tmp/test_lingoflash_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            mock_run.assert_called_once_with(
                ["gnome-screenshot", "-a", "-f", target],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_capture_region_maim_fallback(self) -> None:
        """Verify capture_region falls back to maim if gnome-screenshot is absent."""
        maim_proc = MagicMock(spec=subprocess.CompletedProcess)
        maim_proc.returncode = 0

        def mock_which(cmd: str):
            if cmd == "maim":
                return "/usr/bin/maim"
            return None

        with patch("shutil.which", side_effect=mock_which), \
             patch("subprocess.run", return_value=maim_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=5678):

            target = "/tmp/test_lingo_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            mock_run.assert_called_once_with(["maim", "-s", target], capture_output=True, text=True, check=False)

    def test_capture_region_cancelled_returns_none(self) -> None:
        """User cancelling screenshot selection should return None cleanly."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 1  # Cancelled

        with patch("shutil.which", side_effect=lambda cmd: "/usr/bin/gnome-screenshot" if cmd == "gnome-screenshot" else None), \
             patch("subprocess.run", return_value=mock_proc):

            res = ScreenCapture.capture_region("/tmp/test_lingoflash_ocr.png")
            self.assertIsNone(res)


class TestClipboardManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_read_command_success(self) -> None:
        """Test reading clipboard via subprocess output."""
        cb = ClipboardManager()
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0
        mock_proc.stdout = "  Selected text from screen  \n"

        with patch("subprocess.run", return_value=mock_proc):
            text = cb.read_primary()
            self.assertEqual(text, "Selected text from screen")

    def test_read_fallback_to_qt_clipboard(self) -> None:
        """When CLI tool is missing or fails, read_clipboard falls back to QGuiApplication."""
        cb = ClipboardManager()

        # Set text in Qt clipboard
        qt_cb = QApplication.clipboard()
        if qt_cb is not None:
            qt_cb.setText("Fallback Qt text")

        with patch("subprocess.run", side_effect=FileNotFoundError):
            text = cb.read_clipboard()
            self.assertEqual(text, "Fallback Qt text")

    def test_copy_to_clipboard_syncs_with_qt(self) -> None:
        """copy_to_clipboard should write to QApplication clipboard even if tool fails."""
        cb = ClipboardManager()

        with patch("subprocess.run", side_effect=FileNotFoundError):
            ok = cb.copy_to_clipboard("Synced text")
            self.assertTrue(ok)

        qt_cb = QApplication.clipboard()
        self.assertIsNotNone(qt_cb)
        self.assertEqual(qt_cb.text(), "Synced text")


class TestOCRWorkflow(unittest.TestCase):
    def test_ocr_extract_text_cleans_form_feed_and_newlines(self) -> None:
        """Verify OCREngine cleans form-feeds and normalises text."""
        engine = OCREngine()
        with patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=500), \
             patch("PIL.Image.open"), \
             patch("pytesseract.image_to_string", return_value="Hello World\x0c\n\n\nSecond line"):

            text = engine.extract_text("/tmp/lingoflash_ocr.png")
            self.assertEqual(text, "Hello World\n\nSecond line")

    def test_ocr_workflow_empty_triggers_notification(self) -> None:
        """When OCR finds no text or capture is cancelled, notification is shown and translation is aborted."""
        from main import LingoFlashApp

        mock_app = MagicMock(spec=LingoFlashApp)
        mock_app._translate_text_in_popup = MagicMock()
        mock_app._show_ocr_notification = MagicMock()

        # Call the actual unbound method on the mock instance
        LingoFlashApp._on_ocr_finished(mock_app, "")
        mock_app._show_ocr_notification.assert_called_once_with("No text detected in screenshot")
        mock_app._translate_text_in_popup.assert_not_called()

    def test_ocr_workflow_with_text_opens_popup_and_translates(self) -> None:
        """When OCR extracts text, popup translation is triggered immediately."""
        from main import LingoFlashApp

        mock_app = MagicMock(spec=LingoFlashApp)
        mock_app._translate_text_in_popup = MagicMock()
        mock_app._show_ocr_notification = MagicMock()

        LingoFlashApp._on_ocr_finished(mock_app, "Captured screen text")
        mock_app._translate_text_in_popup.assert_called_once_with("Captured screen text")
        mock_app._show_ocr_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
