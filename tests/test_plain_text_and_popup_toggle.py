"""Unit tests for PlainTextEdit, strip_formatting utility, and show_quick_popup settings toggle."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import QMimeData
from PyQt6.QtWidgets import QApplication

from app.translation.translator import TranslationHistory, TranslationManager
from app.ui.ui_main import MainWindow, PlainTextEdit, SettingsDialog
from app.ui.ui_popup import TranslationPopup
from app.utils.text import strip_formatting
from main import LingoFlashApp


class TestPlainTextFormatting(unittest.TestCase):
    """Test text format stripping and PlainTextEdit widgets."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance()
        if cls._app is None:
            cls._app = QApplication([])

    def test_strip_formatting_html_tags(self) -> None:
        """HTML and XML tags are stripped cleanly."""
        raw = "<b>Bold</b> and <i>italic</i> with <span style='color:red;'>color</span>."
        result = strip_formatting(raw)
        self.assertEqual(result, "Bold and italic with color.")

    def test_strip_formatting_block_tags_and_breaks(self) -> None:
        """Block tags and line break tags are replaced with clean newlines."""
        raw = "<p>First paragraph</p><p>Second paragraph</p><br/>Third line"
        result = strip_formatting(raw)
        self.assertIn("First paragraph", result)
        self.assertIn("Second paragraph", result)
        self.assertIn("Third line", result)
        self.assertNotIn("<p>", result)
        self.assertNotIn("<br/>", result)

    def test_strip_formatting_entities(self) -> None:
        """HTML entities are decoded back to human-readable characters."""
        raw = "&lt;hello&gt; &amp; &quot;world&quot; &#39;test&#39;"
        result = strip_formatting(raw)
        self.assertEqual(result, "<hello> & \"world\" 'test'")

    def test_strip_formatting_math_comparisons(self) -> None:
        """Mathematical comparisons using < and > are preserved."""
        code_str = "if a < b and c > d:\n    return True"
        result = strip_formatting(code_str)
        self.assertEqual(result, code_str)

    def test_plain_text_edit_rejects_rich_text(self) -> None:
        """PlainTextEdit disables rich text acceptance."""
        edit = PlainTextEdit()
        self.assertFalse(edit.acceptRichText())

    def test_plain_text_edit_set_plain_text_strips_formatting(self) -> None:
        """setPlainText passes text through strip_formatting."""
        edit = PlainTextEdit()
        edit.setPlainText("<b>Formatted</b> <i>String</i>")
        self.assertEqual(edit.toPlainText(), "Formatted String")

    def test_plain_text_edit_insert_mime_data_rich_text(self) -> None:
        """Pasting rich HTML into PlainTextEdit inserts only stripped plain text."""
        edit = PlainTextEdit()
        mime = QMimeData()
        mime.setHtml("<h1>Header</h1><p>Body <span style='font-weight:bold;'>Text</span></p>")
        mime.setText("Header\nBody Text")

        edit.insertFromMimeData(mime)
        content = edit.toPlainText().strip()
        self.assertNotIn("<h1>", content)
        self.assertNotIn("<span", content)
        self.assertIn("Body Text", content)


class TestShowQuickPopupSetting(unittest.TestCase):
    """Test show_quick_popup setting persistence, dialog integration, and app routing."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance()
        if cls._app is None:
            cls._app = QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()
        self.config_path = os.path.join(self.temp_dir, "config.json")

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_default_show_quick_popup_is_true(self) -> None:
        """Default value for show_quick_popup is True."""
        mgr = TranslationManager(config_path=self.config_path)
        self.assertTrue(mgr.get_show_quick_popup())

    def test_set_show_quick_popup_persists(self) -> None:
        """Setting show_quick_popup to False saves to config and reloads accurately."""
        mgr = TranslationManager(config_path=self.config_path)
        mgr.set_show_quick_popup(False)
        self.assertFalse(mgr.get_show_quick_popup())

        # Reload from same config file
        mgr2 = TranslationManager(config_path=self.config_path)
        self.assertFalse(mgr2.get_show_quick_popup())

        # Reset back to True
        mgr2.set_show_quick_popup(True)
        self.assertTrue(mgr2.get_show_quick_popup())

    def test_settings_dialog_toggles_quick_popup(self) -> None:
        """SettingsDialog checkbox reflects and updates show_quick_popup."""
        mgr = TranslationManager(config_path=self.config_path)
        self.assertTrue(mgr.get_show_quick_popup())

        dialog = SettingsDialog(mgr)
        self.assertTrue(dialog._chk_quick_popup.isChecked())

        # Uncheck and accept
        dialog._chk_quick_popup.setChecked(False)
        dialog._save_settings()
        self.assertFalse(mgr.get_show_quick_popup())

        # Reopen, check and cancel (reject)
        dialog2 = SettingsDialog(mgr)
        self.assertFalse(dialog2._chk_quick_popup.isChecked())
        dialog2._chk_quick_popup.setChecked(True)
        dialog2.reject()
        self.assertFalse(mgr.get_show_quick_popup())

    def test_main_window_text_fields_strip_formatting(self) -> None:
        """MainWindow source and target text editors strip formatting."""
        mgr = TranslationManager(config_path=self.config_path)
        history = TranslationHistory(history_path=os.path.join(self.temp_dir, "history.json"))
        win = MainWindow(mgr, history)

        self.assertIsInstance(win._txt_source, PlainTextEdit)
        self.assertIsInstance(win._txt_target, PlainTextEdit)

        win.set_source_text("<b>Source</b> <i>Markup</i>")
        self.assertEqual(win._txt_source.toPlainText(), "Source Markup")

        win.set_translation_result(
            source_text="Source Markup",
            translated_text="<p>Translated <span style='color:blue'>Result</span></p>",
            source_lang="en",
            target_lang="ru",
        )
        self.assertEqual(win._txt_target.toPlainText(), "Translated Result")
        win.close()

    def test_popup_text_fields_are_plain_text_edit(self) -> None:
        """TranslationPopup text fields are PlainTextEdit instances."""
        popup = TranslationPopup(font_size=12)
        self.assertIsInstance(popup._txt_source, PlainTextEdit)
        self.assertIsInstance(popup._txt_result, PlainTextEdit)
        popup.close()

    def test_app_routing_translate_selection_with_popup_enabled(self) -> None:
        """When show_quick_popup is True, _do_translate_selection invokes _translate_text_in_popup."""
        mock_app = MagicMock(spec=LingoFlashApp)
        mock_mgr = MagicMock()
        mock_mgr.get_show_quick_popup.return_value = True
        mock_app._translation_manager = mock_mgr

        mock_clip = MagicMock()
        mock_clip.get_selected_text.return_value = "<b>Hello</b> World"
        mock_app._clipboard = mock_clip

        mock_app._translate_text_in_popup = MagicMock()
        mock_app._show_main_window_with_text = MagicMock()

        LingoFlashApp._do_translate_selection(mock_app)

        mock_app._translate_text_in_popup.assert_called_once_with("Hello World")
        mock_app._show_main_window_with_text.assert_not_called()

    def test_app_routing_translate_selection_with_popup_disabled(self) -> None:
        """When show_quick_popup is False, _do_translate_selection invokes _show_main_window_with_text."""
        mock_app = MagicMock(spec=LingoFlashApp)
        mock_mgr = MagicMock()
        mock_mgr.get_show_quick_popup.return_value = False
        mock_app._translation_manager = mock_mgr

        mock_clip = MagicMock()
        mock_clip.get_selected_text.return_value = "<span style='font-size:16px'>Selected Text</span>"
        mock_app._clipboard = mock_clip

        mock_app._translate_text_in_popup = MagicMock()
        mock_app._show_main_window_with_text = MagicMock()

        LingoFlashApp._do_translate_selection(mock_app)

        mock_app._show_main_window_with_text.assert_called_once_with("Selected Text")
        mock_app._translate_text_in_popup.assert_not_called()

    def test_app_routing_ocr_finished_with_popup_disabled(self) -> None:
        """When show_quick_popup is False, _on_ocr_finished invokes _show_main_window_with_text."""
        mock_app = MagicMock(spec=LingoFlashApp)
        mock_mgr = MagicMock()
        mock_mgr.get_show_quick_popup.return_value = False
        mock_app._translation_manager = mock_mgr

        mock_app._translate_text_in_popup = MagicMock()
        mock_app._show_main_window_with_text = MagicMock()
        mock_app._show_ocr_notification = MagicMock()

        LingoFlashApp._on_ocr_finished(mock_app, "<div>OCR Extracted Text</div>")

        mock_app._show_main_window_with_text.assert_called_once_with("OCR Extracted Text")
        mock_app._translate_text_in_popup.assert_not_called()
        mock_app._show_ocr_notification.assert_not_called()


if __name__ == "__main__":
    unittest.main()
