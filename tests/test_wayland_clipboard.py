"""Unit tests for Wayland clipboard thread-safety, QClipboard operations, and wl-copy fallback."""

import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QRect
from PyQt6.QtWidgets import QApplication

from app.clipboard.clipboard_manager import ClipboardManager
from app.translation.translator import TranslationHistory, TranslationManager
from app.ui.ui_main import MainWindow
from app.ui.ui_ocr_overlay import OCROverlayWindow, TranslatedBubbleCard
from app.ui.ui_popup import TranslationPopup


class TestWaylandClipboard(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_subprocess_timeout_reduced_to_1s(self) -> None:
        """Verify the subprocess timeout is reduced to 1.0s to avoid UI freezes."""
        cb = ClipboardManager()
        self.assertEqual(cb._SUBPROCESS_TIMEOUT, 1.0)

    def test_copy_to_clipboard_main_thread_uses_qclipboard_directly(self) -> None:
        """On the main GUI thread, copy_to_clipboard writes to QClipboard without calling subprocess."""
        cb = ClipboardManager()
        test_text = "Test direct QClipboard text 12345"

        with patch("subprocess.run") as mock_run:
            ok = cb.copy_to_clipboard(test_text)
            self.assertTrue(ok)
            mock_run.assert_not_called()

        qt_cb = self._app.clipboard()
        self.assertIsNotNone(qt_cb)
        self.assertEqual(qt_cb.text(), test_text)

    def test_copy_to_clipboard_fallback_to_wl_copy_on_wayland(self) -> None:
        """When QClipboard fails or force_native is used on Wayland, it invokes wl-copy with encoded bytes."""
        cb = ClipboardManager()
        cb._session_type = "wayland"
        test_text = "Wayland native copy test"

        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        with patch("subprocess.run", return_value=mock_proc) as mock_run:
            ok = cb.copy_to_clipboard(test_text, force_native=True)
            self.assertTrue(ok)
            mock_run.assert_called_once_with(
                ["wl-copy"],
                input=test_text.encode("utf-8"),
                capture_output=True,
                timeout=1.0,
                check=True,
            )

    def test_copy_to_clipboard_handles_timeout_gracefully(self) -> None:
        """Verify TimeoutExpired does not raise an unhandled exception."""
        cb = ClipboardManager()
        cb._session_type = "wayland"

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["wl-copy"], timeout=1.0)):
            ok = cb.copy_to_clipboard("Timeout text", force_native=True)
            self.assertFalse(ok)

    def test_copy_to_clipboard_handles_filenotfound_gracefully(self) -> None:
        """Verify FileNotFoundError (e.g. wl-clipboard not installed) is handled safely."""
        cb = ClipboardManager()
        cb._session_type = "wayland"

        with patch("subprocess.run", side_effect=FileNotFoundError):
            ok = cb.copy_to_clipboard("No wl-copy text", force_native=True)
            self.assertFalse(ok)

    def test_main_window_set_clipboard_text_slot(self) -> None:
        """Verify MainWindow.set_clipboard_text safely writes to the clipboard."""
        trans_mgr = TranslationManager()
        history_mgr = TranslationHistory()

        window = MainWindow(translation_manager=trans_mgr, history=history_mgr)
        test_text = "MainWindow slot test clipboard"

        result = window.set_clipboard_text(test_text)
        self.assertTrue(result)

        qt_cb = self._app.clipboard()
        self.assertIsNotNone(qt_cb)
        self.assertEqual(qt_cb.text(), test_text)

        window.close()

    def test_main_window_copy_signal_triggers_slot(self) -> None:
        """Emitting copy_to_clipboard_requested updates clipboard via the connected slot."""
        trans_mgr = TranslationManager()
        history_mgr = TranslationHistory()

        window = MainWindow(translation_manager=trans_mgr, history=history_mgr)
        test_text = "Signal emitted text"

        window.copy_to_clipboard_requested.emit(test_text)
        self._app.processEvents()

        qt_cb = self._app.clipboard()
        self.assertEqual(qt_cb.text(), test_text)
        window.close()

    def test_main_window_copy_result_emits_signal(self) -> None:
        """MainWindow._copy_result emits copy_to_clipboard_requested."""
        trans_mgr = TranslationManager()
        history_mgr = TranslationHistory()

        window = MainWindow(translation_manager=trans_mgr, history=history_mgr)
        window._txt_target.setPlainText("Target translation to copy")

        emitted = []
        window.copy_to_clipboard_requested.connect(emitted.append)
        window._copy_result()

        self.assertIn("Target translation to copy", emitted)
        window.close()

    def test_popup_emits_copy_signal(self) -> None:
        """TranslationPopup emits copy_to_clipboard_requested when copy button is pressed."""
        popup = TranslationPopup()
        popup._translated_text = "Popup translated text"

        emitted_texts = []
        popup.copy_to_clipboard_requested.connect(emitted_texts.append)

        popup._copy_translation()
        self.assertEqual(emitted_texts, ["Popup translated text"])
        popup.close()

    def test_ocr_overlay_emits_copy_signal(self) -> None:
        """TranslatedBubbleCard and OCROverlayWindow forward copy_to_clipboard_requested."""
        blocks = [
            {
                "text": "Original speech",
                "translated": "Translated bubble speech",
                "box": (10, 10, 100, 50),
            }
        ]
        overlay = OCROverlayWindow(blocks=blocks, screen_rect=QRect(0, 0, 800, 600))

        emitted_texts = []
        overlay.copy_to_clipboard_requested.connect(emitted_texts.append)

        # Trigger card copy signal
        self.assertEqual(len(overlay._cards), 1)
        card = overlay._cards[0]
        card.copy_requested.emit("Translated bubble speech")

        self.assertEqual(emitted_texts, ["Translated bubble speech"])
        overlay.close()


if __name__ == "__main__":
    unittest.main()
