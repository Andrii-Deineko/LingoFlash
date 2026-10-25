"""Unit tests for Voice Input Auto-Detect (i18n separation) and Text-to-Speech (TTS) playback."""

import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

from app.audio.voice_input import VoiceInputWorker, LANGUAGE_TO_SPEECH_LOCALE
from app.audio.tts_engine import (
    TTSPlaybackWorker,
    TTSManager,
    resolve_tts_language,
    get_audio_player_command,
)
from app.ui.ui_main import SearchableComboBox, MainWindow, LANGUAGES
from app.translation.translator import TranslationManager, TranslationHistory
from app.utils.i18n import set_current_locale, tr

app = QApplication.instance() or QApplication([])


class TestVoiceAndComboBox(unittest.TestCase):
    """Test suite covering SearchableComboBox DisplayRole/UserRole separation and VoiceInputWorker."""

    def setUp(self) -> None:
        set_current_locale("en")
        self.combo = SearchableComboBox()
        for code, name in LANGUAGES:
            self.combo.add_language_item(name, code)

    def test_combobox_display_and_user_roles(self) -> None:
        """Verify DisplayRole stores localized name while UserRole stores code."""
        # Index 0 is Auto Detect
        self.assertEqual(self.combo.itemData(0, Qt.ItemDataRole.DisplayRole), "Auto Detect")
        self.assertEqual(self.combo.itemData(0, Qt.ItemDataRole.UserRole), "auto")
        self.assertEqual(self.combo.get_selected_code(), "auto")

        # Select Swedish (sv)
        success = self.combo.set_selected_code("sv")
        self.assertTrue(success)
        self.assertEqual(self.combo.get_selected_code(), "sv")
        self.assertEqual(self.combo.itemData(self.combo.currentIndex(), Qt.ItemDataRole.DisplayRole), "Swedish")
        self.assertEqual(self.combo.itemData(self.combo.currentIndex(), Qt.ItemDataRole.UserRole), "sv")

    def test_combobox_update_display_preserves_code(self) -> None:
        """Updating localized display text must never alter UserRole or get_selected_code."""
        self.combo.set_selected_code("auto")
        self.assertEqual(self.combo.get_selected_code(), "auto")

        # Simulate i18n change to Ukrainian: "Автовизначення"
        self.combo.update_item_display_text("auto", "Автовизначення")
        self.assertEqual(self.combo.currentText(), "Автовизначення")
        # Code must STILL strictly be "auto"
        self.assertEqual(self.combo.get_selected_code(), "auto")
        self.assertEqual(self.combo.currentData(Qt.ItemDataRole.UserRole), "auto")

        # Simulate i18n change to Swedish: "Identifiera automatiskt"
        self.combo.update_item_display_text("auto", "Identifiera automatiskt")
        self.assertEqual(self.combo.currentText(), "Identifiera automatiskt")
        self.assertEqual(self.combo.get_selected_code(), "auto")
        self.assertEqual(self.combo.currentData(Qt.ItemDataRole.UserRole), "auto")

    def test_combobox_direct_code_input_resolves(self) -> None:
        """Typing a 2-letter standard code into the combobox resolves to that code."""
        self.combo.setEditText("sv")
        self.assertEqual(self.combo.get_selected_code(), "sv")

        self.combo.setEditText("auto")
        self.assertEqual(self.combo.get_selected_code(), "auto")

    def test_voice_worker_strictly_receives_auto(self) -> None:
        """Voice worker initialized with 'auto' must configure its speech locale to 'auto'."""
        worker = VoiceInputWorker(language_code="auto")
        self.assertEqual(worker.language_code, "auto")
        self.assertEqual(worker._speech_locale, "auto")

        # Verify language mappings for Swedish, Ukrainian, Russian
        worker_sv = VoiceInputWorker(language_code="sv")
        self.assertEqual(worker_sv.language_code, "sv")
        self.assertEqual(worker_sv._speech_locale, "sv-SE")

        worker_uk = VoiceInputWorker(language_code="uk")
        self.assertEqual(worker_uk.language_code, "uk")
        self.assertEqual(worker_uk._speech_locale, "uk-UA")

        worker_ru = VoiceInputWorker(language_code="ru")
        self.assertEqual(worker_ru.language_code, "ru")
        self.assertEqual(worker_ru._speech_locale, "ru-RU")


class TestTextToSpeech(unittest.TestCase):
    """Test suite covering TTS engine, language mapping, and manager."""

    def test_resolve_tts_language(self) -> None:
        """Verify normalization of language codes for TTS."""
        self.assertEqual(resolve_tts_language("sv"), "sv")
        self.assertEqual(resolve_tts_language("en"), "en")
        self.assertEqual(resolve_tts_language("uk"), "uk")
        self.assertEqual(resolve_tts_language("ru"), "ru")
        self.assertEqual(resolve_tts_language("de"), "de")
        self.assertEqual(resolve_tts_language("fr"), "fr")
        self.assertEqual(resolve_tts_language("auto"), "en")
        self.assertEqual(resolve_tts_language(""), "en")
        self.assertEqual(resolve_tts_language("zh-CN"), "zh-CN")

    def test_audio_player_available(self) -> None:
        """Verify system has a supported audio player."""
        cmd = get_audio_player_command()
        self.assertIsNotNone(cmd)
        self.assertIn(cmd[0], ["pw-play", "ffplay", "paplay", "aplay"])

    def test_tts_manager_toggle_and_mutual_exclusion(self) -> None:
        """Verify TTSManager toggles playback and prevents overlapping audio."""
        mgr = TTSManager()
        states = []
        mgr.state_changed.connect(lambda role, active: states.append((role, active)))

        # Mock TTSPlaybackWorker to avoid real hardware audio output during test
        with patch("app.audio.tts_engine.TTSPlaybackWorker.start") as mock_start:
            mgr.play_or_toggle("source", "Hello world", "en")
            self.assertTrue(mgr.is_playing("source"))
            self.assertEqual(mgr._active_role, "source")

            # Clicking the same role toggles it off
            mgr.play_or_toggle("source", "Hello world", "en")
            self.assertFalse(mgr.is_playing())

            # Clicking target stops source and switches
            mgr.play_or_toggle("source", "Hello world", "en")
            mgr.play_or_toggle("target", "Hej världen", "sv")
            self.assertTrue(mgr.is_playing("target"))
            self.assertEqual(mgr._active_role, "target")
            mgr.stop()
            self.assertFalse(mgr.is_playing())


class TestMainWindowVoiceAndTTSIntegration(unittest.TestCase):
    """Integration test verifying UI buttons, icons, and language flow in MainWindow."""

    def setUp(self) -> None:
        self.manager = TranslationManager()
        self.history = TranslationHistory()
        self.win = MainWindow(translation_manager=self.manager, history=self.history)

    def tearDown(self) -> None:
        self.win._tts_manager.stop()
        self.win._stop_voice_input()
        self.win.close()

    def test_tts_buttons_exist_with_speaker_icons(self) -> None:
        """Verify Speaker buttons exist in both Source and Target panes."""
        self.assertTrue(hasattr(self.win, "_btn_tts_source"))
        self.assertTrue(hasattr(self.win, "_btn_tts_target"))
        self.assertFalse(self.win._btn_tts_source.icon().isNull())
        self.assertFalse(self.win._btn_tts_target.icon().isNull())

    def test_voice_input_passes_auto_to_worker(self) -> None:
        """Starting voice input when Auto Detect is active passes 'auto' to worker."""
        self.win._cmb_source_lang.set_selected_code("auto")
        # Mock Worker run to prevent real microphone capture
        with patch.object(VoiceInputWorker, "start"):
            self.win._start_voice_input()
            self.assertIsNotNone(self.win._voice_worker)
            self.assertEqual(self.win._voice_worker.language_code, "auto")
            self.assertEqual(self.win._voice_worker._speech_locale, "auto")
            self.win._stop_voice_input()

    def test_source_tts_dynamic_language_detection(self) -> None:
        """When source is 'auto', TTS uses detected language (e.g. 'sv' for Swedish)."""
        self.win._cmb_source_lang.set_selected_code("auto")
        self.win._txt_source.setPlainText("God morgon, kära vänner!")

        with patch.object(TTSManager, "play_or_toggle") as mock_play:
            self.win._play_source_tts()
            self.assertTrue(mock_play.called)
            args = mock_play.call_args[0]
            self.assertEqual(args[0], "source")
            self.assertEqual(args[1], "God morgon, kära vänner!")
            # Should have detected Swedish ('sv')
            self.assertEqual(args[2], "sv")

    def test_target_tts_uses_selected_target_language(self) -> None:
        """Target TTS strictly uses the combobox target language code."""
        self.win._cmb_target_lang.set_selected_code("sv")
        self.win._txt_target.setPlainText("Tack så mycket")

        with patch.object(TTSManager, "play_or_toggle") as mock_play:
            self.win._play_target_tts()
            self.assertTrue(mock_play.called)
            args = mock_play.call_args[0]
            self.assertEqual(args[0], "target")
            self.assertEqual(args[1], "Tack så mycket")
            self.assertEqual(args[2], "sv")

    def test_tts_active_state_icon_feedback(self) -> None:
        """Active playback sets playing state and changes speaker icon."""
        self.win._on_tts_state_changed("source", True)
        self.assertEqual(self.win._btn_tts_source.property("active"), "true")

        self.win._on_tts_state_changed("source", False)
        self.assertEqual(self.win._btn_tts_source.property("active"), "false")


if __name__ == "__main__":
    unittest.main()
