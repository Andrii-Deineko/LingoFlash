"""Unit tests for codebase robustness, safe JSON loading, fallback themes, and memory cleanup."""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from app.audio.tts_engine import TTSManager, TTSPlaybackWorker
from app.translation.translator import TranslationHistory, TranslationManager
from app.ui.ui_main import SearchableComboBox
from app.ui.ui_ocr_overlay import OCROverlayWindow
from app.utils.i18n import load_locale, reload_locales, tr
from app.utils.json_utils import safe_load_json, safe_save_json
from app.utils.theme import get_stylesheet, load_themes_data, reload_themes


class TestRobustnessAndCleanup(unittest.TestCase):
    """Test resilience against corrupted files, missing keys, and verify memory management."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance()
        if cls._app is None:
            cls._app = QApplication([])

    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self) -> None:
        if os.path.exists(self.temp_dir):
            shutil.rmtree(self.temp_dir)

    def test_safe_load_and_save_json(self) -> None:
        """safe_load_json and safe_save_json handle edge cases and atomic replacement."""
        file_path = os.path.join(self.temp_dir, "test.json")

        # 1. Non-existent file returns default
        self.assertEqual(safe_load_json(file_path, default={"a": 1}), {"a": 1})

        # 2. Save valid JSON
        self.assertTrue(safe_save_json(file_path, {"key": "value"}))
        self.assertEqual(safe_load_json(file_path), {"key": "value"})

        # 3. Corrupted JSON file returns default without throwing
        Path(file_path).write_text("{invalid json", encoding="utf-8")
        self.assertEqual(safe_load_json(file_path, default="fallback"), "fallback")

        # 4. Empty file returns default
        Path(file_path).write_text("", encoding="utf-8")
        self.assertIsNone(safe_load_json(file_path))

    def test_translation_manager_corrupted_config(self) -> None:
        """TranslationManager gracefully handles corrupted or non-dict config files."""
        cfg_file = os.path.join(self.temp_dir, "bad_config.json")

        # Corrupted JSON
        Path(cfg_file).write_text("{malformed: True", encoding="utf-8")
        mgr = TranslationManager(config_path=cfg_file)
        self.assertEqual(mgr.get_target_language(), "uk")
        self.assertTrue(mgr.get_show_quick_popup())

        # Non-dict JSON (e.g. integer or list at root)
        Path(cfg_file).write_text("[1, 2, 3]", encoding="utf-8")
        mgr2 = TranslationManager(config_path=cfg_file)
        self.assertEqual(mgr2.get_target_language(), "uk")

    def test_translation_history_corrupted_file(self) -> None:
        """TranslationHistory gracefully handles corrupted history store."""
        hist_file = os.path.join(self.temp_dir, "bad_history.json")

        Path(hist_file).write_text("not json at all", encoding="utf-8")
        hist = TranslationHistory(history_path=hist_file)
        self.assertEqual(hist.get_all(), [])

        # Non-list JSON
        Path(hist_file).write_text('{"error": "bad format"}', encoding="utf-8")
        hist2 = TranslationHistory(history_path=hist_file)
        self.assertEqual(hist2.get_all(), [])

    def test_themes_fallback_on_missing_or_corrupted(self) -> None:
        """load_themes_data falls back to built-in themes if themes.json is absent or invalid."""
        with patch("app.utils.theme.get_themes_json_path") as mock_path:
            mock_path.return_value = Path(self.temp_dir) / "non_existent_themes.json"
            reload_themes()
            themes, template = load_themes_data()
            self.assertIn("dark", themes)
            self.assertIn("light", themes)
            self.assertTrue(len(template) > 0)

            # Stylesheet compilation still produces valid CSS without crashing
            qss = get_stylesheet("dark", ui_font_size=13, text_font_size=14)
            self.assertIn("13pt", qss)
            self.assertIn("14pt", qss)

        # Restore normal themes
        reload_themes()

    def test_i18n_fallback_on_corrupted_locale(self) -> None:
        """load_locale handles missing keys and invalid JSON gracefully."""
        with patch("app.utils.i18n.get_locales_directory") as mock_dir:
            mock_dir.return_value = Path(self.temp_dir)
            reload_locales()

            # Create broken locale file
            bad_loc = Path(self.temp_dir) / "fr.json"
            bad_loc.write_text("corrupted", encoding="utf-8")

            # Missing or corrupted string returns key itself
            res = tr("test_missing_key", locale="fr")
            self.assertEqual(res, "test_missing_key")

        reload_locales()

    def test_ocr_overlay_window_delete_on_close(self) -> None:
        """OCROverlayWindow has WA_DeleteOnClose enabled to prevent memory leaks."""
        overlay = OCROverlayWindow(blocks=[])
        self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose))
        overlay.close()

    def test_tts_manager_worker_delete_later_on_stop(self) -> None:
        """TTSManager properly calls deleteLater on active worker when stopped."""
        manager = TTSManager()
        mock_worker = MagicMock(spec=TTSPlaybackWorker)
        manager._active_worker = mock_worker
        manager._active_role = "source"

        manager.stop()
        mock_worker.stop.assert_called_once()
        mock_worker.deleteLater.assert_called_once()
        self.assertIsNone(manager._active_worker)

    def test_searchable_combobox_reconciliation_logic(self) -> None:
        """SearchableComboBox reconciles UserRole ISO codes vs DisplayRole names properly."""
        combo = SearchableComboBox()
        combo.add_language_item("English", "en")
        combo.add_language_item("Ukrainian", "uk")
        combo.add_language_item("Svenska", "sv")

        # 1. Exact ISO code lookup
        self.assertTrue(combo.set_selected_code("uk"))
        self.assertEqual(combo.get_selected_code(), "uk")

        # 2. Type 2-letter code into line edit
        line_edit = combo.lineEdit()
        line_edit.setText("sv")
        self.assertEqual(combo.get_selected_code(), "sv")

        # 3. Type partial display name
        line_edit.setText("engl")
        self.assertEqual(combo.get_selected_code(), "en")

        # 4. Reconcile invalid input reverts to last valid selection
        line_edit.setText("non_existent_lang")
        combo._validate_and_reconcile()
        self.assertEqual(combo.get_selected_code(), "uk")


if __name__ == "__main__":
    unittest.main()
