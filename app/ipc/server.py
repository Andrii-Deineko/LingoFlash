"""Single-instance IPC server using QLocalServer/QLocalSocket.

Ensures only one instance of the application runs at a time.
Additional invocations send commands to the running instance via
a local Unix domain socket.
"""

import json
import logging
import os
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

logger = logging.getLogger(__name__)


class IPCServer(QObject):
    """Manages single-instance enforcement and inter-process commands.

    Uses a QLocalServer bound to a well-known socket name so that
    subsequent launches can detect the running instance and forward
    commands (e.g. "translate_selection") instead of starting a
    duplicate process.

    Signals:
        command_received(str): Emitted with the ``action`` string
            whenever an incoming IPC command is received.
    """

    command_received = pyqtSignal(str)

    def __init__(self, app_name: str = "LingoFlash", parent: Optional[QObject] = None) -> None:
        """Initialise the IPC server.

        Args:
            app_name: Unique application identifier used as the socket
                name (e.g. ``"LingoFlash"``). Defaults to ``"LingoFlash"``.
            parent: Optional parent ``QObject``.
        """
        super().__init__(parent)
        self._app_name: str = app_name
        self._server: Optional[QLocalServer] = None

    def start(self) -> bool:
        """Alias for start_listening()."""
        return self.start_listening()

    def stop(self) -> None:
        """Alias for cleanup()."""
        self.cleanup()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_already_running(self) -> bool:
        """Check whether another instance is already listening.

        Why this approach is used:
        Rather than relying on pidfiles (which can get out of sync or linger after a SIGKILL),
        we perform a test connection to the domain socket via QLocalSocket.
        If connection succeeds within 1 second, the primary daemon is confirmed alive.
        If connection fails, the socket is either dead or orphaned by a previous crash,
        in which case QLocalServer.removeServer() safely unlinks it for re-binding.

        Returns:
            ``True`` if another instance owns the socket.
        """
        socket = QLocalSocket(self)
        socket.connectToServer(self._app_name)
        connected = socket.waitForConnected(1000)  # 1 s timeout
        if connected:
            socket.disconnectFromServer()
            socket.deleteLater()
            logger.info("Another instance of '%s' is already running.", self._app_name)
            return True

        socket.deleteLater()
        # The socket file may be stale (e.g. previous crash).  Remove it
        # so we can bind a new server later.
        try:
            QLocalServer.removeServer(self._app_name)
        except Exception:
            pass
        logger.debug("No running instance found for '%s'.", self._app_name)
        return False

    def send_command(self, action: str, env: Optional[dict[str, str]] = None) -> bool:
        """Send a command to the running instance with active environment context.

        Why environment context is included:
        When secondary commands (e.g. ``main.py --action translate_selection``) are triggered
        by GNOME keyboard shortcuts or user terminal sessions, their graphical environment
        (``WAYLAND_DISPLAY``, ``DISPLAY``, ``DBUS_SESSION_BUS_ADDRESS``) reflects the user's
        active active desktop. Forwarding this dictionary allows the persistent daemon
        (which may have been launched at login before the graphical display was fully ready)
        to synchronize its environment variables before invoking clipboard or screenshot tools.

        Args:
            action: The command action string, e.g. ``"translate_selection"``.
            env: Optional environment dictionary to forward. If None, captures
                 active graphical session variables from ``os.environ``.

        Returns:
            ``True`` if the payload was sent and acknowledged,
            ``False`` on any connection or write error.
        """
        socket = QLocalSocket(self)
        socket.connectToServer(self._app_name)

        if not socket.waitForConnected(1000):
            logger.warning(
                "Failed to connect to running instance: %s",
                socket.errorString(),
            )
            socket.deleteLater()
            return False

        env_vars = env if env is not None else {
            k: v for k, v in os.environ.items()
            if k in (
                "WAYLAND_DISPLAY",
                "DISPLAY",
                "XDG_CURRENT_DESKTOP",
                "XDG_SESSION_TYPE",
                "XDG_SESSION_DESKTOP",
                "DBUS_SESSION_BUS_ADDRESS",
                "XDG_RUNTIME_DIR",
                "GDK_BACKEND",
                "QT_QPA_PLATFORM",
            )
        }
        payload_dict: dict = {"action": action}
        if env_vars:
            payload_dict["env"] = env_vars

        payload: bytes = json.dumps(payload_dict).encode("utf-8")
        socket.write(payload)

        if not socket.waitForBytesWritten(1000):
            logger.warning("Failed to write command to socket.")
            socket.disconnectFromServer()
            socket.deleteLater()
            return False

        socket.flush()
        socket.disconnectFromServer()
        socket.deleteLater()
        logger.info("Sent IPC command: %s", action)
        return True

    def start_listening(self) -> bool:
        """Create and start the local server.

        Binds a ``QLocalServer`` to the application socket name and
        begins accepting incoming connections.

        Returns:
            ``True`` if the server started successfully, ``False``
            otherwise.
        """
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._handle_connection)

        if not self._server.listen(self._app_name):
            logger.error(
                "Could not start IPC server: %s",
                self._server.errorString(),
            )
            return False

        logger.info(
            "IPC server listening on '%s'.",
            self._server.fullServerName(),
        )
        return True

    def cleanup(self) -> None:
        """Shut down the server and remove the socket file."""
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            QLocalServer.removeServer(self._app_name)
        except Exception:
            pass
        logger.debug("IPC server cleaned up for '%s'.", self._app_name)

    # ------------------------------------------------------------------
    # Private slots
    # ------------------------------------------------------------------

    def _handle_connection(self) -> None:
        """Read an incoming JSON command and emit *command_received*."""
        if self._server is None:
            return

        client: Optional[QLocalSocket] = self._server.nextPendingConnection()
        if client is None:
            return

        try:
            # Wait for data to arrive (short timeout — messages are tiny).
            if not client.waitForReadyRead(2000):
                logger.warning("Incoming IPC connection timed out waiting for data.")
                client.disconnectFromServer()
                return

            raw_data: bytes = bytes(client.readAll())
            client.disconnectFromServer()

            if not raw_data:
                logger.warning("Received empty IPC payload.")
                return

            try:
                message: dict = json.loads(raw_data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                logger.warning("Invalid IPC payload: %s", exc)
                return

            action: str = message.get("action", "")
            client_env = message.get("env", {})
            if isinstance(client_env, dict) and client_env:
                logger.debug("Received client graphical environment variables: %s", list(client_env.keys()))
                for k, v in client_env.items():
                    if k and v:
                        os.environ[k] = str(v)

            if action:
                logger.info("Received IPC command: %s", action)
                self.command_received.emit(action)
            else:
                logger.warning("IPC message missing 'action' key: %s", message)
        finally:
            client.deleteLater()
