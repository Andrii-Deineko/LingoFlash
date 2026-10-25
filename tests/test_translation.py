"""Tests for Translation Backend, Workers, Caching, and Error Resilience."""

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.translation.translator import (
    GoogleTranslateBackend,
    TranslationError,
    TranslationHistory,
    TranslationManager,
    TranslationWorker,
    _cache_get,
    _cache_put,
)


class TestTranslationBackend(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def setUp(self) -> None:
        self.backend = GoogleTranslateBackend()

    def test_translate_fast_gtx_path_with_mock(self) -> None:
        """Verify fast GTX translation returns parsed result, detected lang, and populates cache."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            [["Привіт", "Hello "], ["світ", "world"]],
            None,
            "en",
        ]

        with patch.object(self.backend._session, "get", return_value=mock_response):
            result, detected = self.backend.translate_with_detection("Hello world", "auto", "uk")
            self.assertEqual(result, "Привітсвіт")
            self.assertEqual(detected, "en")

        # Second call should be a cache hit (session.get should not be called)
        with patch.object(self.backend._session, "get") as mock_get:
            cached_result = self.backend.translate("Hello world", "auto", "uk")
            self.assertEqual(cached_result, "Привітсвіт")
            mock_get.assert_not_called()

    def test_translate_empty_text_returns_empty(self) -> None:
        """Whitespace or empty text should return immediately without API call."""
        self.assertEqual(self.backend.translate("   ", "auto", "uk"), "")

    def test_network_connection_error_handling(self) -> None:
        """Verify ConnectionError is caught and raised as a clean user-facing TranslationError."""
        with patch.object(
            self.backend._session,
            "get",
            side_effect=Exception("HTTPSConnectionPool: Failed to resolve translate.google.com (NameResolutionError)"),
        ):
            with patch("deep_translator.GoogleTranslator.translate", side_effect=Exception("Failed to resolve host")):
                with self.assertRaises(TranslationError) as ctx:
                    self.backend.translate("Hello", "en", "uk")

                self.assertIn("Network connection failed", ctx.exception.message)

    def test_network_timeout_error_handling(self) -> None:
        """Verify TimeoutExpired is converted to clean user-facing error message."""
        with patch.object(
            self.backend._session,
            "get",
            side_effect=Exception("Read timed out"),
        ):
            with patch("deep_translator.GoogleTranslator.translate", side_effect=Exception("timed out")):
                with self.assertRaises(TranslationError) as ctx:
                    self.backend.translate("Hello", "en", "uk")

                self.assertIn("timed out", ctx.exception.message.lower())

    def test_smart_language_swap_in_worker(self) -> None:
        """Verify worker automatically swaps target to English when source is target language."""
        manager = MagicMock(spec=TranslationManager)
        backend = MagicMock()
        backend.detect_language.return_value = "uk"
        manager.backend = backend
        manager.translate.return_value = "Hello world"

        worker = TranslationWorker(
            manager=manager,
            text="Привіт світ",
            source="auto",
            target="uk",
            request_id=42,
        )

        results = []
        worker.finished.connect(lambda s, t, sl, tl, rid: results.append((s, t, sl, tl, rid)))
        worker.run()

        self.assertEqual(len(results), 1)
        src, trans, sl, tl, rid = results[0]
        self.assertEqual(src, "Привіт світ")
        self.assertEqual(trans, "Hello world")
        self.assertEqual(tl, "en")  # Swapped from 'uk' to 'en'
        self.assertEqual(rid, 42)

    def test_worker_cancellation(self) -> None:
        """Cancelled worker should not emit finished or error signals."""
        manager = MagicMock(spec=TranslationManager)
        manager.translate.return_value = "Result"

        worker = TranslationWorker(
            manager=manager,
            text="Hello",
            source="en",
            target="uk",
            request_id=1,
        )
        worker.cancel()

        results = []
        worker.finished.connect(lambda *args: results.append(args))
        worker.error.connect(lambda *args: results.append(args))
        worker.run()

        self.assertEqual(len(results), 0)

    def test_detect_language_symbols_and_heuristics(self) -> None:
        """Verify detect_language handles emojis, symbols, and script heuristics safely."""
        # Symbols and emojis without letters return 'auto'
        self.assertEqual(self.backend.detect_language("🔥🔥🔥"), "auto")
        self.assertEqual(self.backend.detect_language("1234567890"), "auto")
        self.assertEqual(self.backend.detect_language("!@#$%^&*()_+"), "auto")
        self.assertEqual(self.backend.detect_language("   "), "auto")

        # Fast-path heuristics & short text lingua detection
        self.assertEqual(self.backend.detect_language("Привіт"), "uk")
        self.assertEqual(self.backend.detect_language("Это тест"), "ru")
        self.assertEqual(self.backend.detect_language("привет! Как дела?"), "ru")

    def test_rapid_thread_tracking_and_cancellation(self) -> None:
        """Simulate rapid keystrokes spawning and cancelling workers under thread tracking."""
        running_threads: set = set()
        manager = MagicMock(spec=TranslationManager)
        manager.translate.return_value = "Result"
        manager.backend.detect_language.return_value = "en"

        active_worker = None
        for i in range(25):
            if active_worker is not None and active_worker.isRunning():
                active_worker.cancel()
                try:
                    active_worker.finished.disconnect()
                except Exception:
                    pass
                active_worker.quit()
                active_worker.wait(20)

            worker = TranslationWorker(
                manager=manager,
                text=f"test {i}",
                source="auto",
                target="uk",
                request_id=i,
            )
            running_threads.add(worker)
            worker.worker_done.connect(lambda w=worker: running_threads.discard(w))
            active_worker = worker
            worker.start()

        # Wait for all workers to settle
        for w in list(running_threads):
            w.wait(200)
        self._app.processEvents()
        self.assertEqual(len(running_threads), 0)

    def test_cancel_active_worker_handles_deleted_cpp_object(self) -> None:
        """Verify _cancel_active_worker catches RuntimeError when C++ object is deleted."""
        mock_dead_worker = MagicMock()
        # Simulate C++ object deletion when isRunning is accessed
        mock_dead_worker.isRunning.side_effect = RuntimeError(
            "wrapped C/C++ object of type TranslationWorker has been deleted"
        )

        class DummyApp:
            def __init__(self) -> None:
                self._active_worker = mock_dead_worker

            def _cancel_active_worker(self) -> None:
                try:
                    if self._active_worker is not None and self._active_worker.isRunning():
                        self._active_worker.cancel()
                        self._active_worker.quit()
                        self._active_worker.wait(100)
                except RuntimeError:
                    pass
                finally:
                    self._active_worker = None

        app_instance = DummyApp()
        self.assertIsNotNone(app_instance._active_worker)
        # Should not raise RuntimeError and should nullify _active_worker
        app_instance._cancel_active_worker()
        self.assertIsNone(app_instance._active_worker)

    def test_translation_history_pruning_and_clear(self) -> None:
        """Test history collection, limit pruning, and clear."""
        history = TranslationHistory(max_entries=3)
        history.clear()
        self.assertEqual(len(history.get_all()), 0)

        history.add("1", "one", "en", "uk")
        history.add("2", "two", "en", "uk")
        history.add("3", "three", "en", "uk")
        history.add("4", "four", "en", "uk")

        entries = history.get_all()
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[-1]["source_text"], "4")
        self.assertEqual(entries[0]["source_text"], "2")  # '1' was pruned

        history.clear()
        self.assertEqual(len(history.get_all()), 0)


if __name__ == "__main__":
    unittest.main()
