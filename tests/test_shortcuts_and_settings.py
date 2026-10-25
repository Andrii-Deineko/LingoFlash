"""Tests for Shortcut Customization, Conversion, and Settings Dialog."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence
from PyQt6.QtWidgets import QApplication, QSizePolicy

from app.translation.translator import TranslationHistory, TranslationManager
from app.ui.ui_main import MainWindow, SettingsDialog
from app.utils.shortcuts import (
    DEFAULT_SHORTCUTS,
    apply_gnome_shortcuts,
    gnome_to_qt_shortcut,
    qt_to_gnome_shortcut,
)


class TestShortcutsAndSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def test_qt_to_gnome_conversion(self) -> None:
        """Verify Qt shortcut strings map correctly to GNOME gsettings syntax."""
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Alt+T"), "<Ctrl><Alt>t")
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Alt+O"), "<Ctrl><Alt>o")
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Alt+L"), "<Ctrl><Alt>l")
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Shift+S"), "<Ctrl><Shift>s")
        self.assertEqual(qt_to_gnome_shortcut("Super+Alt+T"), "<Super><Alt>t")
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Alt+F5"), "<Ctrl><Alt>F5")
        self.assertEqual(qt_to_gnome_shortcut("Ctrl+Alt+Space"), "<Ctrl><Alt>space")
        self.assertEqual(qt_to_gnome_shortcut(""), "")

    def test_gnome_to_qt_conversion(self) -> None:
        """Verify GNOME gsettings bindings map back to Qt sequence strings."""
        self.assertEqual(gnome_to_qt_shortcut("<Ctrl><Alt>t"), "Ctrl+Alt+T")
        self.assertEqual(gnome_to_qt_shortcut("<Ctrl><Shift>s"), "Ctrl+Shift+S")
        self.assertEqual(gnome_to_qt_shortcut("<Super><Alt>o"), "Super+Alt+O")
        self.assertEqual(gnome_to_qt_shortcut("' <Ctrl><Alt>l '"), "Ctrl+Alt+L")
        self.assertEqual(gnome_to_qt_shortcut(""), "")

    def test_manager_shortcuts_get_and_set(self) -> None:
        """Verify TranslationManager properly stores and merges shortcuts."""
        manager = TranslationManager()
        # Default fallback
        shortcuts = manager.get_shortcuts()
        self.assertEqual(shortcuts["swap_languages"], "Ctrl+Shift+S")
        self.assertEqual(shortcuts["translate_selection"], "Ctrl+Alt+T")

        # Custom shortcuts persistence
        custom = {
            "translate_selection": "Ctrl+Shift+T",
            "ocr_capture": "Ctrl+Shift+O",
            "show_main": "Ctrl+Shift+M",
            "swap_languages": "Ctrl+T",
        }
        with patch.object(manager, "save_config"):
            manager.set_shortcuts(custom)
            self.assertEqual(manager.get_shortcut("swap_languages"), "Ctrl+T")
            self.assertEqual(manager.get_shortcut("show_main"), "Ctrl+Shift+M")

    def test_main_window_swap_shortcut(self) -> None:
        """Verify the in-window swap shortcut activates _swap_languages."""
        manager = MagicMock(spec=TranslationManager)
        manager.get_shortcuts.return_value = dict(DEFAULT_SHORTCUTS)
        manager.get_shortcut.side_effect = lambda k: DEFAULT_SHORTCUTS.get(k, "")
        manager.get_target_language.return_value = "en"
        history = MagicMock(spec=TranslationHistory)
        history.get_all.return_value = []

        window = MainWindow(translation_manager=manager, history=history)
        self.assertTrue(hasattr(window, "_swap_shortcut"))
        self.assertEqual(window._swap_shortcut.key().toString(), "Ctrl+Shift+S")

        # Verify swapping behavior
        window._txt_source.setPlainText("Hello")
        window._txt_target.setPlainText("Привіт")
        window._cmb_source_lang.setCurrentIndex(window._cmb_source_lang.findData("en"))
        window._cmb_target_lang.setCurrentIndex(window._cmb_target_lang.findData("uk"))

        # Trigger swap function (same as shortcut activation)
        window._swap_languages()

        self.assertEqual(window._txt_source.toPlainText(), "Привіт")
        self.assertEqual(window._txt_target.toPlainText(), "Hello")

    def test_main_window_dynamic_language_detection_and_reset(self) -> None:
        """Verify MainWindow shows detected language badge on auto and resets when cleared."""
        manager = MagicMock(spec=TranslationManager)
        manager.get_shortcuts.return_value = dict(DEFAULT_SHORTCUTS)
        manager.get_shortcut.side_effect = lambda k: DEFAULT_SHORTCUTS.get(k, "")
        manager.get_target_language.return_value = "en"
        history = MagicMock(spec=TranslationHistory)
        history.get_all.return_value = []

        window = MainWindow(translation_manager=manager, history=history)
        self.assertEqual(window._lbl_detected.text(), "")

        # 1. Source text provided and Auto Detect translates with detected 'es'
        window._txt_source.setPlainText("Hola mundo")
        window.set_translation_result("Hola mundo", "Hello world", source_lang="es", target_lang="en")
        self.assertEqual(window._lbl_detected.text(), "(Spanish detected)")

        # 2. Clearing source via backspace resets badge
        window._txt_source.setPlainText("")
        self.assertEqual(window._lbl_detected.text(), "")

        # 3. Another translation with 'fr'
        window._txt_source.setPlainText("Bonjour")
        window.set_translation_result("Bonjour", "Hello", source_lang="fr", target_lang="en")
        self.assertEqual(window._lbl_detected.text(), "(French detected)")

        # 4. Clear button resets badge
        window._clear_source()
        self.assertEqual(window._lbl_detected.text(), "")

        # 5. Explicit source language does not display auto-detect badge
        idx_de = window._cmb_source_lang.findData("de")
        window._cmb_source_lang.setCurrentIndex(idx_de)
        window._txt_source.setPlainText("Guten Morgen")
        window.set_translation_result("Guten Morgen", "Good morning", source_lang="de", target_lang="en")
        self.assertEqual(window._lbl_detected.text(), "")

    def test_settings_dialog_saves_shortcuts_and_calls_gnome(self) -> None:
        """Verify SettingsDialog edits shortcuts, writes config, and invokes apply_gnome_shortcuts."""
        manager = MagicMock(spec=TranslationManager)
        manager.get_backends.return_value = ["google", "deepl", "libretranslate"]
        manager._config = {"backend": "google", "target_language": "uk"}
        manager.get_shortcuts.return_value = dict(DEFAULT_SHORTCUTS)
        manager.get_target_language.return_value = "uk"

        dialog = SettingsDialog(manager)

        # Set custom sequences
        dialog._kse_translate.setKeySequence(QKeySequence("Ctrl+Shift+F1"))
        dialog._kse_ocr.setKeySequence(QKeySequence("Ctrl+Shift+F2"))
        dialog._kse_show.setKeySequence(QKeySequence("Ctrl+Shift+F3"))
        dialog._kse_swap.setKeySequence(QKeySequence("Ctrl+T"))

        with patch("app.ui.ui_main.apply_gnome_shortcuts", return_value=(True, "Success")) as mock_apply:
            dialog._save_settings()

            # Manager set_shortcuts should have been called
            manager.set_shortcuts.assert_called_once()
            saved_shortcuts = manager.set_shortcuts.call_args[0][0]
            self.assertEqual(saved_shortcuts["translate_selection"], "Ctrl+Shift+F1")
            self.assertEqual(saved_shortcuts["ocr_capture"], "Ctrl+Shift+F2")
            self.assertEqual(saved_shortcuts["show_main"], "Ctrl+Shift+F3")
            self.assertEqual(saved_shortcuts["swap_languages"], "Ctrl+T")

            # apply_gnome_shortcuts should be called with updated dictionary
            mock_apply.assert_called_once_with(saved_shortcuts)

    def test_main_window_splitter_and_proportional_resizing(self) -> None:
        """Verify MainWindow splitter contains 2 panes, 50/50 sizing, expanding policies, and centered swap button."""
        manager = MagicMock(spec=TranslationManager)
        manager.get_shortcuts.return_value = dict(DEFAULT_SHORTCUTS)
        manager.get_shortcut.side_effect = lambda k: DEFAULT_SHORTCUTS.get(k, "")
        manager.get_target_language.return_value = "en"
        history = MagicMock(spec=TranslationHistory)
        history.get_all.return_value = []

        window = MainWindow(translation_manager=manager, history=history)
        window.show()
        self._app.processEvents()

        # 1. Verify QSplitter contains exactly the two main panels
        self.assertTrue(hasattr(window, "_splitter"))
        self.assertEqual(window._splitter.count(), 2)

        # 2. Verify expanding size policies on text edits
        src_sp = window._txt_source.sizePolicy()
        tgt_sp = window._txt_target.sizePolicy()
        self.assertEqual(src_sp.horizontalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(src_sp.verticalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(tgt_sp.horizontalPolicy(), QSizePolicy.Policy.Expanding)
        self.assertEqual(tgt_sp.verticalPolicy(), QSizePolicy.Policy.Expanding)

        # 3. Verify proportional sizes
        s1, s2 = window._splitter.sizes()
        self.assertAlmostEqual(s1, s2, delta=10)

        # 4. Resize window and verify proportional expansion
        window.resize(1300, 850)
        self._app.processEvents()
        s1_resized, s2_resized = window._splitter.sizes()
        self.assertAlmostEqual(s1_resized, s2_resized, delta=10)

        # 5. Verify swap button stays aligned with splitter handle horizontally and header vertically
        handle = window._splitter.handle(1)
        h_pos = handle.mapTo(window.centralWidget(), handle.rect().topLeft())
        btn_pos = window._btn_swap.pos()
        expected_x = h_pos.x() + (handle.width() - window._btn_swap.width()) // 2
        self.assertAlmostEqual(btn_pos.x(), expected_x, delta=2)
        self.assertEqual(window._btn_swap.width(), 32)
        self.assertEqual(window._btn_swap.height(), 32)

        # Header level vertical positioning (near dropdowns rather than in middle of text areas)
        combo_y = window._cmb_source_lang.mapTo(window.centralWidget(), window._cmb_source_lang.rect().topLeft()).y()
        self.assertAlmostEqual(btn_pos.y(), combo_y, delta=6)

    def test_apply_gnome_shortcuts_dry_run_or_mock(self) -> None:
        """Verify apply_gnome_shortcuts formats gsettings commands properly."""
        with patch("shutil.which", return_value="/usr/bin/gsettings"), \
             patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="['custom0/']", returncode=0)

            success, msg = apply_gnome_shortcuts(DEFAULT_SHORTCUTS, main_py_path="/tmp/main.py")
            self.assertTrue(success)
            self.assertIn("updated successfully", msg)
            self.assertTrue(mock_run.called)


if __name__ == "__main__":
    unittest.main()
