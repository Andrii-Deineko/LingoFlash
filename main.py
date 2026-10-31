#!/usr/bin/env python3
"""LingoFlash — Quick desktop translation client.

Main entry point with CLI argument parsing and single-instance orchestration.
Supports translate-selection, OCR capture, and a persistent main window,
all coordinated through IPC so only one instance runs at a time.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from typing import Optional

from PyQt6.QtCore import QTimer, Qt, qInstallMessageHandler, QtMsgType, QThread
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QColor, QFont
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu

from app.ipc.server import IPCServer
from app.clipboard.clipboard_manager import ClipboardManager
from app.ocr.ocr_engine import ScreenCapture, OCREngine, OCRWorker, OCROverlayWorker
from app.translation.translator import (
    TranslationManager,
    TranslationWorker,
    TranslationHistory,
)
from app.ui.ui_popup import TranslationPopup
from app.ui.ui_ocr_overlay import OCROverlayWindow
from app.ui.ui_main import MainWindow, SettingsDialog
from app.utils.theme import detect_system_theme, get_stylesheet, get_app_icon
from app.utils.shortcuts import apply_gnome_shortcuts
from app.utils.i18n import tr, set_current_locale, get_current_locale
from app.utils.text import strip_formatting

logger = logging.getLogger(__name__)

APP_NAME = "LingoFlash"
APP_ORG = "LingoFlash"
APP_VERSION = "0.1.0"


def _qt_message_handler(msg_type: QtMsgType, context: object, message: str) -> None:
    """Filter benign D-Bus tray warnings on GNOME/Wayland and route others to logging."""
    if "QDBusTrayIcon" in message or "StatusNotifierWatcher" in message:
        logger.debug("Filtered Qt D-Bus tray notice: %s", message)
        return
    if msg_type == QtMsgType.QtWarningMsg:
        logger.warning("Qt: %s", message)
    elif msg_type == QtMsgType.QtCriticalMsg:
        logger.error("Qt: %s", message)
    elif msg_type == QtMsgType.QtFatalMsg:
        logger.critical("Qt: %s", message)
    else:
        logger.debug("Qt: %s", message)


qInstallMessageHandler(_qt_message_handler)


def _create_app_icon() -> QIcon:
    """Return an application icon, falling back to a custom vector-style pixmap."""
    icon = get_app_icon()
    if icon is not None and not icon.isNull():
        return icon
    logo_path = Path(__file__).resolve().parent / "assets" / "logo.svg"
    if logo_path.is_file():
        icon = QIcon(str(logo_path))
        if not icon.isNull():
            return icon
    icon = QIcon.fromTheme("accessories-text-editor")
    if not icon.isNull():
        return icon
    pixmap = QPixmap(64, 64)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#1a73e8"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(4, 4, 56, 56, 12, 12)
    painter.setPen(QColor("#ffffff"))
    font = QFont("SansSerif", 26, QFont.Weight.Bold)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "文")
    painter.end()
    return QIcon(pixmap)


class LingoFlashApp:
    """Top-level application controller.

    Owns the QApplication, IPC server, translation manager, main window,
    system tray, and popup lifecycle.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self._args = args
        self._app: QApplication = self._create_qapp()
        self._ipc = IPCServer(APP_NAME)

        # -- Single-instance gate ------------------------------------------------
        if self._ipc.is_already_running():
            if getattr(args, "restart", False):
                logger.info("Restart requested – stopping existing instance...")
                self._ipc.send_command("quit")
                import time
                time.sleep(0.6)
            elif args.action == "quit":
                logger.info("Quit requested – stopping existing instance...")
                self._ipc.send_command("quit")
                sys.exit(0)
            else:
                action = args.action or "show_main"
                logger.info("Instance already running – forwarding action '%s'", action)
                self._ipc.send_command(action)
                sys.exit(0)

        # -- First (primary) instance -------------------------------------------
        self._ipc.start()

        self._translation_manager = TranslationManager()
        self._history = TranslationHistory()
        self._clipboard = ClipboardManager()

        self._main_window = MainWindow(
            translation_manager=self._translation_manager,
            history=self._history,
        )
        self._main_window.translate_requested.connect(self._on_main_translate_requested)
        self._main_window.quit_app_requested.connect(self._quit)
        self._main_window.locale_changed.connect(self.apply_locale)

        self._popup: Optional[TranslationPopup] = None
        self._active_worker: Optional[TranslationWorker] = None
        self._ocr_worker: Optional[OCRWorker] = None
        self._ocr_overlay_worker: Optional[OCROverlayWorker] = None
        self._overlay_window: Optional[OCROverlayWindow] = None
        self._running_threads: set[QThread] = set()
        self._current_request_id: int = 0

        self._setup_tray()

        # IPC dispatch
        self._ipc.command_received.connect(self.handle_action)

        # Apply interface language locale
        self.apply_locale(self._translation_manager.get_interface_language())

        # Apply theme and configured UI / Text font sizes
        theme = self._translation_manager.get_theme()
        ui_sz = self._translation_manager.get_ui_font_size()
        txt_sz = self._translation_manager.get_text_font_size()
        self._app.setStyleSheet(get_stylesheet(theme, ui_font_size=ui_sz, text_font_size=txt_sz))
        self._main_window.apply_font_size(txt_sz)

        # Synchronize global desktop shortcuts
        apply_gnome_shortcuts(self._translation_manager.get_shortcuts())

        # Automatically ensure native desktop launcher exists
        try:
            from app.utils.desktop import install_desktop_file

            desktop_file = Path.home() / ".local" / "share" / "applications" / "lingoflash.desktop"
            if not desktop_file.is_file() or getattr(args, "install_desktop", False):
                install_desktop_file()
        except Exception as exc:
            logger.debug("Desktop launcher check: %s", exc)

        # If launched with an explicit action, dispatch it (window stays hidden).
        if args.action:
            # Use a single-shot timer so the event loop is running first.
            QTimer.singleShot(0, lambda: self.handle_action(args.action))
        elif getattr(args, "install_desktop", False) and getattr(args, "hidden", False):
            QTimer.singleShot(0, self._quit)
        elif not getattr(args, "hidden", False):
            self._show_main_window()

    # -- QApplication factory ---------------------------------------------------

    def _create_qapp(self) -> QApplication:
        """Create and configure the global QApplication."""
        app = QApplication(sys.argv)
        app.setApplicationName("LingoFlash")
        app.setApplicationDisplayName("LingoFlash")
        app.setDesktopFileName("lingoflash.desktop")
        app.setOrganizationName(APP_ORG)
        app.setApplicationVersion(APP_VERSION)
        app.setQuitOnLastWindowClosed(False)

        self._icon = _create_app_icon()
        app.setWindowIcon(self._icon)
        return app

    # -- System tray ------------------------------------------------------------

    def _setup_tray(self) -> None:
        """Create or update the system-tray icon and its context menu."""
        if not QSystemTrayIcon.isSystemTrayAvailable():
            logger.info("System tray is not available on this desktop environment; running in background mode.")
            self._tray = None
            return

        try:
            if not hasattr(self, "_tray") or self._tray is None:
                self._tray = QSystemTrayIcon(self._icon, self._app)
                self._tray.activated.connect(self._on_tray_activated)

            menu = QMenu()
            menu.addAction(tr("tray_show"), self._show_main_window)
            menu.addAction(tr("tray_translate"), lambda: self.handle_action("translate_selection"))
            menu.addAction(tr("tray_ocr"), lambda: self.handle_action("ocr_capture"))
            menu.addAction(tr("tray_overlay"), lambda: self.handle_action("ocr_overlay"))
            menu.addSeparator()
            menu.addAction(tr("tray_settings"), self._open_settings)
            menu.addSeparator()
            menu.addAction(tr("tray_quit"), self._quit)
            self._tray.setContextMenu(menu)
            self._tray.show()
        except Exception as exc:
            logger.warning("System tray initialization failed: %s; continuing without tray icon.", exc)
            self._tray = None

    def apply_locale(self, locale: str) -> None:
        """Apply active locale across main window, popup, and tray menu."""
        set_current_locale(locale)
        if hasattr(self, "_main_window") and self._main_window is not None:
            self._main_window.apply_locale(locale)
        if hasattr(self, "_popup") and self._popup is not None:
            self._popup.apply_locale(locale)
        self._setup_tray()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Handle tray icon clicks."""
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_main_window()

    # -- Action dispatcher ------------------------------------------------------

    def handle_action(self, action: str) -> None:
        """Dispatch an action received from the CLI or IPC.

        Args:
            action: One of ``"translate_selection"``, ``"ocr_capture"``,
                    or ``"show_main"``.
        """
        logger.info("Dispatching action: %s", action)
        if action == "translate_selection":
            self._do_translate_selection()
        elif action == "ocr_capture":
            self._do_ocr_capture()
        elif action == "ocr_overlay":
            self._do_ocr_overlay()
        elif action == "show_main":
            self._show_main_window()
        elif action == "settings":
            self._open_settings()
        elif action == "quit":
            self._quit()
        else:
            logger.warning("Unknown action: %s", action)

    def _open_settings(self) -> None:
        """Open the settings dialog."""
        if hasattr(self, "_main_window") and self._main_window is not None:
            self._main_window._open_settings()
        else:
            dlg = SettingsDialog(self._translation_manager, on_quit=self._quit)
            dlg.quit_requested.connect(self._quit)
            dlg.exec()
        if hasattr(self, "_popup") and self._popup is not None:
            self._popup.apply_font_size(self._translation_manager.get_text_font_size())

    # -- translate_selection ----------------------------------------------------

    def _do_translate_selection(self) -> None:
        """Grab the current text selection and translate it in a popup or main window."""
        text = self._clipboard.get_selected_text()
        if not text or not text.strip():
            logger.info("No text selected – ignoring translate_selection")
            return
        cleaned = strip_formatting(text).strip()
        if not cleaned:
            return
        show_popup = True
        try:
            show_popup = self._translation_manager.get_show_quick_popup()
        except AttributeError:
            pass
        if show_popup:
            self._translate_text_in_popup(cleaned)
        else:
            self._show_main_window_with_text(cleaned)

    # -- Worker lifecycle management --------------------------------------------

    def _cancel_active_worker(self) -> None:
        """Cleanly cancel and disconnect any in-flight translation worker."""
        try:
            if self._active_worker is not None and self._active_worker.isRunning():
                self._active_worker.cancel()
                try:
                    self._active_worker.finished.disconnect()
                except Exception:
                    pass
                try:
                    self._active_worker.error.disconnect()
                except Exception:
                    pass
                self._active_worker.quit()
                # Bounded wait (100ms) to allow graceful termination without freezing UI
                self._active_worker.wait(100)
        except RuntimeError:
            # The C++ object is already deleted, just ignore it
            pass
        finally:
            self._active_worker = None

    def _track_and_start_worker(self, worker: QThread) -> None:
        """Hold a strong reference to a QThread until it finishes to prevent GC aborts."""
        self._running_threads.add(worker)

        def _cleanup(*args: object) -> None:
            if self._active_worker is worker:
                self._active_worker = None
            if hasattr(self, "_ocr_worker") and self._ocr_worker is worker:
                self._ocr_worker = None
            if hasattr(self, "_ocr_overlay_worker") and self._ocr_overlay_worker is worker:
                self._ocr_overlay_worker = None
            self._running_threads.discard(worker)
            try:
                worker.deleteLater()
            except RuntimeError:
                pass

        if hasattr(worker, "worker_done"):
            worker.worker_done.connect(_cleanup)
        else:
            worker.finished.connect(_cleanup)
        worker.start()

    # -- ocr_capture ------------------------------------------------------------

    def _do_ocr_capture(self) -> None:
        """Launch screen-region OCR, then translate the result in a popup."""
        try:
            if hasattr(self, "_ocr_worker") and self._ocr_worker is not None and self._ocr_worker.isRunning():
                logger.info("OCR worker already running, ignoring duplicate request")
                return
        except RuntimeError:
            self._ocr_worker = None

        self._ocr_worker = OCRWorker()
        self._ocr_worker.finished.connect(self._on_ocr_finished)
        self._ocr_worker.error.connect(self._on_ocr_error)
        self._track_and_start_worker(self._ocr_worker)

    def _on_ocr_finished(self, text: str) -> None:
        """Handle successful OCR extraction or cancelled capture."""
        cleaned = strip_formatting(text).strip() if text else ""
        if not cleaned:
            logger.info("OCR returned empty text or user cancelled selection")
            self._show_ocr_notification("No text detected in screenshot")
            return
        show_popup = True
        try:
            show_popup = self._translation_manager.get_show_quick_popup()
        except AttributeError:
            pass
        if show_popup:
            self._translate_text_in_popup(cleaned)
        else:
            self._show_main_window_with_text(cleaned)

    def _on_ocr_error(self, message: str) -> None:
        """Log OCR errors and notify user."""
        logger.error("OCR failed: %s", message)
        self._show_ocr_notification("No text detected in screenshot")

    # -- Comic/Game OCR Overlay -------------------------------------------------

    def _do_ocr_overlay(self) -> None:
        """Launch comic/game screen-region OCR and display floating translation bubbles in-place."""
        try:
            if hasattr(self, "_ocr_overlay_worker") and self._ocr_overlay_worker is not None and self._ocr_overlay_worker.isRunning():
                logger.info("OCR overlay worker already running, ignoring duplicate request")
                return
        except RuntimeError:
            self._ocr_overlay_worker = None

        target_lang = self._get_active_target_language()
        self._ocr_overlay_worker = OCROverlayWorker(
            translation_manager=self._translation_manager,
            target_lang=target_lang,
        )
        self._ocr_overlay_worker.overlay_ready.connect(self._on_ocr_overlay_ready)
        self._ocr_overlay_worker.error.connect(self._on_ocr_overlay_error)
        self._track_and_start_worker(self._ocr_overlay_worker)

    def _on_ocr_overlay_ready(self, blocks: list, screen_geom: object) -> None:
        """Handle completed OCR blocks with translated speech bubbles."""
        if not blocks:
            logger.info("OCR overlay returned no text or user cancelled selection")
            self._show_ocr_notification("No text detected for overlay")
            return

        try:
            if hasattr(self, "_overlay_window") and self._overlay_window is not None:
                self._overlay_window.close()
        except Exception:
            pass

        self._overlay_window = OCROverlayWindow(blocks=blocks, screen_rect=screen_geom)
        if hasattr(self, "_main_window") and self._main_window is not None:
            self._overlay_window.copy_to_clipboard_requested.connect(self._main_window.set_clipboard_text)
        self._overlay_window.show()
        self._overlay_window.raise_()
        self._overlay_window.activateWindow()

    def _on_ocr_overlay_error(self, message: str) -> None:
        """Log overlay OCR errors and notify user."""
        logger.error("Overlay OCR failed: %s", message)
        self._show_ocr_notification("Overlay OCR failed")

    def _show_ocr_notification(self, message: str = "No text detected in screenshot") -> None:
        """Display a subtle notification or tooltip near cursor or via system tray."""
        from PyQt6.QtWidgets import QToolTip
        from PyQt6.QtGui import QCursor
        from PyQt6.QtCore import QRect

        try:
            pos = QCursor.pos()
            QToolTip.showText(pos, f"ℹ️ {message}", None, QRect(), 3000)
        except Exception as exc:
            logger.debug("Failed to show QToolTip: %s", exc)

        if hasattr(self, "_tray") and self._tray is not None:
            try:
                self._tray.showMessage(APP_NAME, message, QSystemTrayIcon.MessageIcon.Information, 2500)
            except Exception as exc:
                logger.debug("Failed to show tray notification: %s", exc)

    # -- Popup translation flow -------------------------------------------------

    def _get_active_target_language(self) -> str:
        """Return the active target language selected by the user."""
        if hasattr(self, "_main_window") and self._main_window is not None:
            lang = self._main_window.get_target_language()
            if lang:
                return lang
        return self._translation_manager.get_target_language()

    def _translate_text_in_popup(
        self,
        source_text: str,
        source_lang: str = "auto",
        target_lang: Optional[str] = None,
    ) -> None:
        """Show the translation popup and kick off a TranslationWorker.

        Args:
            source_text: The text to translate.
            source_lang: Source language (defaults to "auto").
            target_lang: Optional target language (defaults to user preference).
        """
        if not target_lang:
            target_lang = self._get_active_target_language()

        if self._popup is None:
            self._popup = TranslationPopup(font_size=self._translation_manager.get_text_font_size())
            self._popup.open_main_requested.connect(
                lambda txt: self._show_main_window_with_text(txt)
            )
            self._popup.retranslate_requested.connect(self._on_popup_retranslate_requested)
            self._popup.cancel_worker_requested.connect(self._cancel_active_worker)
            self._popup.closed.connect(self._cancel_active_worker)
            self._popup.copy_to_clipboard_requested.connect(self._main_window.set_clipboard_text)
            self._popup.apply_locale(self._translation_manager.get_interface_language())
        else:
            self._popup.apply_font_size(self._translation_manager.get_text_font_size())

        self._popup.show_with_text(source_text, source_lang=source_lang, target_lang=target_lang)
        self._start_popup_worker(source_text, source_lang=source_lang, target_lang=target_lang)

    def _on_popup_retranslate_requested(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> None:
        """User edited text or switched language from inside the quick popup."""
        self._translation_manager.set_target_language(target_lang)
        self._start_popup_worker(text, source_lang=source_lang, target_lang=target_lang)

    def _start_popup_worker(
        self,
        text: str,
        source_lang: str = "auto",
        target_lang: str = "en",
    ) -> None:
        """Start or restart a background worker for popup translation."""
        self._current_request_id += 1
        req_id = self._current_request_id

        self._cancel_active_worker()

        worker = TranslationWorker(
            manager=self._translation_manager,
            text=text,
            source=source_lang,
            target=target_lang,
            request_id=req_id,
        )
        worker.finished.connect(self._on_popup_translation_finished)
        worker.error.connect(self._on_popup_translation_error)
        self._active_worker = worker
        if self._popup is not None:
            self._popup.set_active_worker(worker)
        self._track_and_start_worker(worker)

    def _on_popup_translation_finished(
        self,
        source: str,
        translated: str,
        source_lang: str,
        target_lang: str,
        request_id: int = 0,
    ) -> None:
        """Push the translation result into the visible popup if still current."""
        if request_id != self._current_request_id:
            logger.debug(
                "Discarding stale popup translation result (req=%d, current=%d)",
                request_id,
                self._current_request_id,
            )
            return

        if self._popup is not None:
            try:
                if not self._popup.isVisible():
                    logger.debug("Popup is hidden/closed; discarding translation result")
                    return
                self._popup.set_translation(
                    source_text=source,
                    translated_text=translated,
                    source_lang=source_lang,
                    target_lang=target_lang,
                )
            except RuntimeError:
                logger.debug("Popup C++ object has been deleted; ignoring")

        self._history.add(source, translated, source_lang, target_lang)

    def _on_popup_translation_error(self, message: str, request_id: int = 0) -> None:
        """Display an error inside the popup if still current."""
        if request_id != self._current_request_id:
            return

        logger.error("Popup translation failed: %s", message)
        if self._popup is not None:
            try:
                if not self._popup.isVisible():
                    return
                err_msg = message if message.startswith("⚠️") else f"⚠️ Translation Error: {message}"
                self._popup.set_error(err_msg)
            except RuntimeError:
                logger.debug("Popup C++ object has been deleted; ignoring")

    # -- Main-window helpers ----------------------------------------------------

    def _on_main_translate_requested(
        self,
        text: str,
        source_lang: str = "auto",
        target_lang: str = "en",
    ) -> None:
        """Handle a translation request originating from the main window."""
        self._current_request_id += 1
        req_id = self._current_request_id

        self._cancel_active_worker()

        worker = TranslationWorker(
            manager=self._translation_manager,
            text=text,
            source=source_lang,
            target=target_lang,
            request_id=req_id,
        )
        worker.finished.connect(self._on_main_translation_finished)
        worker.error.connect(self._on_main_translation_error)
        self._active_worker = worker
        self._track_and_start_worker(worker)

    def _on_main_translation_finished(
        self,
        source: str,
        translated: str,
        source_lang: str,
        target_lang: str,
        request_id: int = 0,
    ) -> None:
        """Relay translation result back to the main window if still current."""
        if request_id != self._current_request_id:
            logger.debug(
                "Discarding stale translation result (request_id=%d, current=%d)",
                request_id,
                self._current_request_id,
            )
            return

        self._main_window.set_translation_result(
            source_text=source,
            translated_text=translated,
            source_lang=source_lang,
            target_lang=target_lang,
        )
        self._history.add(source, translated, source_lang, target_lang)

    def _on_main_translation_error(self, message: str, request_id: int = 0) -> None:
        """Relay translation error back to the main window if still current."""
        if request_id != self._current_request_id:
            return

        err_msg = message if message.startswith("⚠️") else f"⚠️ Translation Error: {message}"
        self._main_window.set_translation_error(err_msg)

    def _show_main_window(self) -> None:
        """Show, raise, and activate the main window."""
        self._main_window.show()
        self._main_window.raise_()
        self._main_window.activateWindow()

    def _show_main_window_with_text(self, text: str) -> None:
        """Show the main window pre-filled with *text*."""
        self._show_main_window()
        self._main_window.set_source_text(text)

    # -- Shutdown ---------------------------------------------------------------

    def _quit(self) -> None:
        """Clean shutdown."""
        logger.info("Shutting down %s", APP_NAME)
        try:
            self._ipc.stop()
        except Exception as exc:
            logger.debug("Error stopping IPC: %s", exc)

        # Ensure IPC socket files are removed cleanly
        import os
        for sock_path in ("/tmp/LingoFlash", "/tmp/LingoFlash.sock"):
            if os.path.exists(sock_path):
                try:
                    os.unlink(sock_path)
                except OSError:
                    pass

        if self._tray is not None:
            try:
                self._tray.hide()
            except Exception as exc:
                logger.debug("Error hiding tray: %s", exc)

        if hasattr(self, "_overlay_window") and self._overlay_window is not None:
            try:
                self._overlay_window.close()
            except Exception as exc:
                logger.debug("Error closing overlay: %s", exc)

        # Safely cancel and wait for all active background threads
        for thread in list(self._running_threads):
            try:
                if hasattr(thread, "cancel"):
                    thread.cancel()
                if hasattr(thread, "stop"):
                    thread.stop()
                thread.quit()
                thread.wait(200)
            except Exception as exc:
                logger.debug("Error shutting down thread %s: %s", thread, exc)
        self._running_threads.clear()

        QApplication.quit()

    # -- Run --------------------------------------------------------------------

    def run(self) -> int:
        """Enter the Qt event loop and return the exit code."""
        return self._app.exec()


def _parse_args(args: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="lingoflash",
        description="LingoFlash — Quick desktop translation client",
    )
    parser.add_argument(
        "--action",
        choices=["translate_selection", "ocr_capture", "ocr_overlay", "show_main", "settings", "quit"],
        default=None,
        help="Action to perform (default: launch / show main window)",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="Restart running instance if one is active",
    )
    parser.add_argument(
        "--hidden",
        action="store_true",
        help="Start minimized in system tray without showing main window",
    )
    parser.add_argument(
        "--install-desktop",
        action="store_true",
        help="Generate and install lingoflash.desktop launcher into ~/.local/share/applications",
    )
    return parser.parse_args(args)


def _install_signal_handlers(app_ctrl: LingoFlashApp) -> None:
    """Allow Ctrl-C and SIGTERM to shut down the application gracefully."""
    def _signal_handler(signum: int, _frame: object) -> None:  # noqa: ANN001
        logger.info("Received signal %s – shutting down", signum)
        app_ctrl._quit()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # Python signal handlers only run between bytecodes; a zero-interval
    # QTimer lets the interpreter regain control so the handler fires.
    timer = QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)


def main() -> None:
    """Application entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = _parse_args()
    app_ctrl = LingoFlashApp(args)
    _install_signal_handlers(app_ctrl)
    sys.exit(app_ctrl.run())


if __name__ == "__main__":
    main()
