"""Clipboard manager for reading and writing system clipboard on Linux.

Supports Wayland (via ``wl-paste`` / ``wl-copy``) and Qt QClipboard,
auto-detecting the active session from environment variables.
"""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


class ClipboardManager:
    """Read and write the system clipboard on Wayland or X11.

    The session type is detected once at construction time via
    :meth:`detect_session_type` and cached for subsequent calls.

    Example::

        cb = ClipboardManager()
        selected = cb.get_selected_text()
        cb.copy_to_clipboard("Hello, world!")
    """

    _SUBPROCESS_TIMEOUT: float = 1.0  # seconds

    def __init__(self) -> None:
        """Initialise the clipboard manager and detect the session type."""
        self._session_type: str = self.detect_session_type()
        logger.debug("Detected session type: %s", self._session_type)

    # ------------------------------------------------------------------
    # Session detection
    # ------------------------------------------------------------------

    @staticmethod
    def detect_session_type() -> str:
        """Detect whether the current session is Wayland or X11.

        Checks ``$WAYLAND_DISPLAY`` first (most reliable on compositors
        that set it), then falls back to ``$XDG_SESSION_TYPE``.

        Returns:
            ``"wayland"`` or ``"x11"``.  Defaults to ``"x11"`` when the
            environment is ambiguous.
        """
        if os.environ.get("WAYLAND_DISPLAY"):
            return "wayland"

        session_type: str = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session_type == "wayland":
            return "wayland"

        # Default to X11 for tty, unset, or any other value.
        return "x11"

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def read_primary(self) -> str:
        """Read text from the *primary* selection buffer.

        On Wayland this calls ``wl-paste --primary --no-newline``.
        If wl-paste is not available or empty, falls back to Qt selection clipboard.

        Returns:
            The selected text (stripped), or an empty string on failure.
        """
        text = self._run_read_command(["wl-paste", "--primary", "--no-newline"])
        if not text:
            try:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtGui import QClipboard
                app = QApplication.instance()
                if app is not None:
                    cb = app.clipboard()
                    if cb is not None:
                        text = cb.text(QClipboard.Mode.Selection) or ""
            except Exception:
                pass
        return text.strip()

    def read_clipboard(self) -> str:
        """Read text from the system *clipboard* buffer.

        On Wayland this calls ``wl-paste --no-newline``.
        If wl-paste is not available or empty, falls back to Qt clipboard.

        Returns:
            The clipboard text (stripped), or an empty string on failure.
        """
        text = self._run_read_command(["wl-paste", "--no-newline"])
        if not text:
            # Fall back to QGuiApplication clipboard if available
            try:
                from PyQt6.QtGui import QGuiApplication
                cb = QGuiApplication.clipboard()
                if cb is not None:
                    text = cb.text() or ""
            except Exception:
                pass

        return text.strip()

    def get_selected_text(self) -> str:
        """Return the currently selected text.

        Tries the primary selection first (the text the user has
        highlighted).  If that is empty, falls back to the clipboard.

        Returns:
            The selected / clipboard text, or an empty string if both
            are empty.
        """
        text: str = self.read_primary()
        if text:
            return text
        return self.read_clipboard()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def is_text_in_clipboard(self, text: str) -> bool:
        """Check if the given text matches current clipboard content."""
        try:
            from PyQt6.QtWidgets import QApplication

            app = QApplication.instance()
            if app is not None:
                cb = app.clipboard()
                if cb is not None and cb.text() == text:
                    return True
        except Exception:
            pass
        return False

    def copy_to_clipboard(self, text: str, force_native: bool = False) -> bool:
        """Copy *text* to the system clipboard.

        Attempts standard Qt QClipboard operation on the main GUI thread first.
        If QClipboard fails, times out, or compositors reject it (e.g. lack of focus),
        falls back to native Wayland (wl-copy) clipboard utility.

        Args:
            text: The string to place on the clipboard.
            force_native: If True, bypass QClipboard and use native tool directly.

        Returns:
            ``True`` if the operation succeeded, ``False`` otherwise.
        """
        if not text:
            return False

        success = False

        # 1. Thread-safe QClipboard execution on the main GUI thread
        if not force_native:
            try:
                from PyQt6.QtWidgets import QApplication
                from PyQt6.QtCore import QThread

                app = QApplication.instance()
                if app is not None and QThread.currentThread() == app.thread():
                    cb = app.clipboard()
                    if cb is not None:
                        cb.setText(text)
                        if cb.text() == text:
                            success = True
            except Exception as exc:
                logger.debug("QClipboard write failed or called outside main thread: %s", exc)

        # 2. Native Wayland fallback if QClipboard was not successful
        if not success:
            cmd = ["wl-copy"]

            try:
                subprocess.run(
                    cmd,
                    input=text.encode("utf-8"),
                    capture_output=True,
                    timeout=self._SUBPROCESS_TIMEOUT,
                    check=True,
                )
                success = True
            except FileNotFoundError:
                logger.warning(
                    "Clipboard tool not found: %s. Please install wl-clipboard.",
                    cmd[0],
                )
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Native clipboard write timed out after %s s (%s).",
                    self._SUBPROCESS_TIMEOUT,
                    cmd[0],
                )
            except (subprocess.CalledProcessError, OSError) as exc:
                logger.warning("Clipboard write error with %s: %s", cmd[0], exc)

        return success

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_read_command(self, cmd: list[str]) -> str:
        """Execute a clipboard-read command and return its stdout.

        Args:
            cmd: The command and arguments to run.

        Returns:
            Stripped stdout on success, or an empty string on any error.
        """
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._SUBPROCESS_TIMEOUT,
            )
            if result.returncode != 0:
                # Non-zero exit is common when the selection is empty;
                # log at debug rather than warning to reduce noise.
                logger.debug(
                    "Clipboard read returned rc=%d: %s",
                    result.returncode,
                    result.stderr.strip(),
                )
                return ""
            return result.stdout.strip()

        except FileNotFoundError:
            logger.warning(
                "Clipboard tool not found: %s. "
                "Please install wl-clipboard.",
                cmd[0],
            )
            return ""
        except subprocess.TimeoutExpired:
            logger.warning(
                "Clipboard read timed out after %.1f s.",
                self._SUBPROCESS_TIMEOUT,
            )
            return ""
        except OSError as exc:
            logger.warning("Clipboard read OS error: %s", exc)
            return ""
