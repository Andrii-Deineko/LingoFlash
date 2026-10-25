"""Clipboard manager for reading and writing system clipboard on Linux.

Supports both Wayland (via ``wl-paste`` / ``wl-copy``) and X11
(via ``xclip``) session types, auto-detecting the active session
from environment variables.
"""

import logging
import os
import subprocess
from typing import Optional

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

    _SUBPROCESS_TIMEOUT: int = 3  # seconds

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

        On Wayland this calls ``wl-paste --primary --no-newline``;
        on X11 it calls ``xclip -o -selection primary``.

        Returns:
            The selected text (stripped), or an empty string on failure.
        """
        if self._session_type == "wayland":
            return self._run_read_command(
                ["wl-paste", "--primary", "--no-newline"],
            )
        return self._run_read_command(
            ["xclip", "-o", "-selection", "primary"],
        )

    def read_clipboard(self) -> str:
        """Read text from the system *clipboard* buffer.

        On Wayland this calls ``wl-paste --no-newline``;
        on X11 it calls ``xclip -o -selection clipboard``.

        Returns:
            The clipboard text (stripped), or an empty string on failure.
        """
        text = ""
        if self._session_type == "wayland":
            text = self._run_read_command(["wl-paste", "--no-newline"])
        else:
            text = self._run_read_command(
                ["xclip", "-o", "-selection", "clipboard"],
            )

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

    def copy_to_clipboard(self, text: str) -> bool:
        """Copy *text* to the system clipboard.

        On Wayland the text is piped to ``wl-copy``; on X11 it is piped
        to ``xclip -selection clipboard``.

        Args:
            text: The string to place on the clipboard.

        Returns:
            ``True`` if the operation succeeded, ``False`` otherwise.
        """
        if self._session_type == "wayland":
            cmd = ["wl-copy"]
        else:
            cmd = ["xclip", "-selection", "clipboard"]

        success = False
        try:
            result: subprocess.CompletedProcess[str] = subprocess.run(
                cmd,
                input=text,
                capture_output=True,
                text=True,
                timeout=self._SUBPROCESS_TIMEOUT,
            )
            if result.returncode != 0:
                logger.warning(
                    "Clipboard write failed (rc=%d): %s",
                    result.returncode,
                    result.stderr.strip(),
                )
            else:
                success = True

        except FileNotFoundError:
            logger.warning(
                "Clipboard tool not found: %s. "
                "Please install the appropriate package "
                "(wl-clipboard or xclip).",
                cmd[0],
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Clipboard write timed out after %d s.",
                self._SUBPROCESS_TIMEOUT,
            )
        except OSError as exc:
            logger.warning("Clipboard write OS error: %s", exc)

        # Always synchronize with Qt clipboard
        try:
            from PyQt6.QtGui import QGuiApplication
            cb = QGuiApplication.clipboard()
            if cb is not None:
                cb.setText(text)
                success = True
        except Exception:
            pass

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
                "Please install the appropriate package "
                "(wl-clipboard or xclip).",
                cmd[0],
            )
            return ""
        except subprocess.TimeoutExpired:
            logger.warning(
                "Clipboard read timed out after %d s.",
                self._SUBPROCESS_TIMEOUT,
            )
            return ""
        except OSError as exc:
            logger.warning("Clipboard read OS error: %s", exc)
            return ""
