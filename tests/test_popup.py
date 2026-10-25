"""Tests for Quick Translation Popup window interactivity, editable text, and language selectors."""

import os
import sys
import unittest

from PyQt6.QtWidgets import QApplication

from app.ui.ui_popup import TranslationPopup


class TestTranslationPopup(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def setUp(self) -> None:
        self.popup = TranslationPopup()

    def tearDown(self) -> None:
        self.popup.close()

    def test_popup_initialization(self) -> None:
        """Verify popup widgets, editable source area, and language selectors."""
        self.assertFalse(self.popup._txt_source.isReadOnly())
        self.assertTrue(self.popup._txt_result.isReadOnly())

        # Source "From:" ComboBox exists and defaults to Auto Detect
        self.assertIsNotNone(self.popup._cmb_source_lang)
        self.assertEqual(self.popup._cmb_source_lang.currentData(), "auto")
        self.assertEqual(self.popup._cmb_source_lang.currentText(), "Auto Detect")

        # Target "To:" ComboBox exists
        self.assertIsNotNone(self.popup._cmb_target_lang)

        # Detected badge exists and is initially empty
        self.assertEqual(self.popup._lbl_detected.text(), "")

    def test_show_with_text(self) -> None:
        """Verify show_with_text updates source text and sets languages without immediate debounce fire."""
        requests = []
        self.popup.retranslate_requested.connect(lambda txt, s, t: requests.append((txt, s, t)))

        self.popup.show_with_text("Bonjour le monde", source_lang="auto", target_lang="en")

        self.assertEqual(self.popup._txt_source.toPlainText(), "Bonjour le monde")
        self.assertEqual(self.popup._cmb_source_lang.currentData(), "auto")
        self.assertEqual(self.popup._cmb_target_lang.currentData(), "en")
        self.assertEqual(len(requests), 0)

    def test_editable_source_text_debounced_retranslation(self) -> None:
        """Verify typing in the source box starts debounce timer and emits retranslate_requested."""
        requests = []
        self.popup.retranslate_requested.connect(lambda txt, s, t: requests.append((txt, s, t)))

        self.popup.show_with_text("Initial text", target_lang="uk")

        # Simulate user typing new text
        self.popup._txt_source.setPlainText("Updated text by user")
        self.assertTrue(self.popup._debounce_timer.isActive())

        # Trigger timer directly to simulate debounce expiration
        self.popup._debounce_timer.timeout.emit()

        self.assertEqual(len(requests), 1)
        text, src, tgt = requests[0]
        self.assertEqual(text, "Updated text by user")
        self.assertEqual(src, "auto")
        self.assertEqual(tgt, "uk")

    def test_source_language_change_triggers_retranslation(self) -> None:
        """Verify changing the source language selector triggers retranslation."""
        requests = []
        self.popup.retranslate_requested.connect(lambda txt, s, t: requests.append((txt, s, t)))

        self.popup.show_with_text("Hello there", target_lang="uk")

        # Select Spanish ("es") as source language
        idx_es = self.popup._cmb_source_lang.findData("es")
        self.assertGreaterEqual(idx_es, 0)
        self.popup._cmb_source_lang.setCurrentIndex(idx_es)

        self.assertEqual(len(requests), 1)
        text, src, tgt = requests[0]
        self.assertEqual(text, "Hello there")
        self.assertEqual(src, "es")
        self.assertEqual(tgt, "uk")

    def test_target_language_change_triggers_retranslation(self) -> None:
        """Verify changing the target language selector triggers retranslation."""
        requests = []
        self.popup.retranslate_requested.connect(lambda txt, s, t: requests.append((txt, s, t)))

        self.popup.show_with_text("Good morning", source_lang="en", target_lang="uk")

        # Select French ("fr") as target language
        idx_fr = self.popup._cmb_target_lang.findData("fr")
        self.assertGreaterEqual(idx_fr, 0)
        self.popup._cmb_target_lang.setCurrentIndex(idx_fr)

        self.assertEqual(len(requests), 1)
        text, src, tgt = requests[0]
        self.assertEqual(text, "Good morning")
        self.assertEqual(src, "en")
        self.assertEqual(tgt, "fr")

    def test_dynamic_auto_detect_feedback(self) -> None:
        """Verify Auto Detect shows '(Language detected)' badge on result completion."""
        self.popup.show_with_text("Merci beaucoup", source_lang="auto", target_lang="en")
        self.assertEqual(self.popup._lbl_detected.text(), "")

        # Receive translation with detected language 'fr'
        self.popup.set_translation(
            source_text="Merci beaucoup",
            translated_text="Thank you very much",
            source_lang="fr",
            target_lang="en",
        )

        self.assertEqual(self.popup._txt_result.toPlainText(), "Thank you very much")
        self.assertEqual(self.popup._lbl_detected.text(), "(French detected)")

        # If user switches to explicit language (not 'auto'), badge disappears
        idx_de = self.popup._cmb_source_lang.findData("de")
        self.popup._cmb_source_lang.setCurrentIndex(idx_de)
        self.assertEqual(self.popup._lbl_detected.text(), "")

    def test_empty_source_text_clears_result(self) -> None:
        """Verify clearing source text resets the translation result without network requests."""
        requests = []
        self.popup.retranslate_requested.connect(lambda txt, s, t: requests.append((txt, s, t)))

        self.popup.show_with_text("Some text", target_lang="en")
        self.popup.set_translation("Some text", "Якийсь текст", "en", "uk")

        # Clear text
        self.popup._txt_source.setPlainText("")
        self.popup._trigger_retranslation()

        self.assertEqual(self.popup._txt_result.toPlainText(), "")
        self.assertEqual(self.popup._lbl_detected.text(), "")
        self.assertEqual(len(requests), 0)


if __name__ == "__main__":
    unittest.main()
