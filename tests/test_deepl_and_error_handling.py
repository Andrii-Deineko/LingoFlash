"""Unit tests for DeepL Free/Pro routing and translation error handling & UI feedback."""

import unittest
from unittest.mock import patch, MagicMock

from PyQt6.QtWidgets import QApplication

from app.translation.translator import (
    DeepLBackend,
    TranslationError,
    TranslationManager,
    TranslationHistory,
    TranslationWorker,
    _make_backend,
)
from app.ui.ui_main import MainWindow
from app.ui.ui_popup import TranslationPopup
from deep_translator.exceptions import AuthorizationException, ApiKeyException

app = QApplication.instance() or QApplication([])


class TestDeepLFreeProRouting(unittest.TestCase):
    """Test suite covering DeepL API key endpoint routing (:fx check)."""

    def test_deepl_free_key_sets_use_free_api_true(self) -> None:
        """DeepL API keys ending with :fx must strictly use free endpoint."""
        free_key = "3b08e244-1234-5678-abcd-123456789abc:fx"
        backend = DeepLBackend(api_key=free_key)
        self.assertTrue(backend._use_free_api)

        # Even if use_free_api=False is explicitly passed, :fx key forces True
        backend_forced = DeepLBackend(api_key=free_key, use_free_api=False)
        self.assertTrue(backend_forced._use_free_api)

        # Factory _make_backend must also configure use_free_api=True for :fx keys
        backend_factory = _make_backend("deepl", api_key=free_key)
        self.assertIsInstance(backend_factory, DeepLBackend)
        self.assertTrue(backend_factory._use_free_api)

    def test_deepl_pro_key_sets_use_free_api_false(self) -> None:
        """DeepL Pro keys (not ending with :fx) must use pro endpoint by default."""
        pro_key = "3b08e244-1234-5678-abcd-123456789abc"
        backend = DeepLBackend(api_key=pro_key)
        self.assertFalse(backend._use_free_api)

        backend_factory = _make_backend("deepl", api_key=pro_key)
        self.assertIsInstance(backend_factory, DeepLBackend)
        self.assertFalse(backend_factory._use_free_api)

    def test_deepl_translator_instantiated_with_use_free_api(self) -> None:
        """Verify DeeplTranslator receives use_free_api=True when key has :fx."""
        free_key = "test-key-123:fx"
        backend = DeepLBackend(api_key=free_key)

        with patch("deep_translator.DeeplTranslator.translate", return_value="Hallo") as mock_trans:
            with patch("deep_translator.DeeplTranslator.__init__", return_value=None) as mock_init:
                res, _ = backend.translate_with_detection("Hello", "en", "de")
                self.assertEqual(res, "Hallo")
                mock_init.assert_called_once()
                kwargs = mock_init.call_args[1]
                self.assertTrue(kwargs.get("use_free_api"))
                self.assertEqual(kwargs.get("api_key"), free_key)


class TestDeepLErrorHandling(unittest.TestCase):
    """Test suite covering AuthorizationException and error propagation."""

    def test_authorization_exception_produces_friendly_error(self) -> None:
        """AuthorizationException must be caught and converted to user-friendly TranslationError."""
        backend = DeepLBackend(api_key="invalid-key:fx")

        with patch("deep_translator.DeeplTranslator.translate", side_effect=AuthorizationException("Unauthorized access")):
            with self.assertRaises(TranslationError) as ctx:
                backend.translate_with_detection("Hello", "en", "de")

            err_msg = str(ctx.exception)
            self.assertIn("⚠️ Translation Error", err_msg)
            self.assertIn("Invalid or unauthorized DeepL API Key", err_msg)

    def test_missing_api_key_raises_friendly_error(self) -> None:
        """Empty DeepL key must produce clear configuration error."""
        backend = DeepLBackend(api_key="")
        with self.assertRaises(TranslationError) as ctx:
            backend.translate_with_detection("Hello", "en", "de")
        self.assertIn("DeepL API Key is missing", str(ctx.exception))

    def test_translation_worker_emits_friendly_error(self) -> None:
        """TranslationWorker must emit clean error signal on AuthorizationException."""
        manager = TranslationManager()
        manager.set_backend("deepl", api_key="invalid-key:fx")

        worker = TranslationWorker(
            manager=manager,
            text="Testing error signal",
            source="en",
            target="de",
            request_id=42,
        )

        received_errors = []
        worker.error.connect(lambda msg, req: received_errors.append((msg, req)))

        with patch("deep_translator.DeeplTranslator.translate", side_effect=AuthorizationException("Unauthorized")):
            worker.run()

        self.assertEqual(len(received_errors), 1)
        err_msg, req_id = received_errors[0]
        self.assertEqual(req_id, 42)
        self.assertIn("⚠️ Translation Error", err_msg)
        self.assertIn("Invalid or unauthorized DeepL API Key", err_msg)


class TestUIErrorVisibility(unittest.TestCase):
    """Test suite verifying UI updates, error display, and re-enabling."""

    def setUp(self) -> None:
        self.manager = TranslationManager()
        self.history = TranslationHistory()
        self.win = MainWindow(translation_manager=self.manager, history=self.history)
        self.popup = TranslationPopup()

    def tearDown(self) -> None:
        self.win.close()
        self.popup.close()

    def test_main_window_set_translation_error(self) -> None:
        """MainWindow error state sets text in target area, stops loading, and updates status bar."""
        self.win.set_loading(True)
        self.assertTrue(self.win._spinner_timer.isActive())
        self.assertFalse(self.win._lbl_spinner.isHidden())

        err_msg = "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings."
        self.win.set_translation_error(err_msg)

        # Loading spinner must be stopped and hidden
        self.assertFalse(self.win._spinner_timer.isActive())
        self.assertTrue(self.win._lbl_spinner.isHidden())
        # Target text area must show the error message
        self.assertEqual(self.win._txt_target.toPlainText(), err_msg)
        # History list must NOT include the error
        history_items = [self.win._lst_history.item(i).text() for i in range(self.win._lst_history.count())]
        self.assertFalse(any("Translation Error" in it for it in history_items))

    def test_popup_set_error(self) -> None:
        """TranslationPopup error state sets text in target area and stops loading indicator."""
        self.popup._lbl_loading.show()
        self.popup._loading_timer.start()

        err_msg = "⚠️ Translation Error: Invalid or unauthorized DeepL API Key. Please check your settings."
        self.popup.set_error(err_msg)

        # Loading indicator must be hidden and timer stopped
        self.assertFalse(self.popup._lbl_loading.isVisible())
        self.assertFalse(self.popup._loading_timer.isActive())
        # Target result must display the error message
        self.assertEqual(self.popup._txt_result.toPlainText(), err_msg)


if __name__ == "__main__":
    unittest.main()
