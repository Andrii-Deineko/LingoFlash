"""Tests for IPCServer single-instance enforcement and command dispatch."""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from app.ipc.server import IPCServer


class TestIPCServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

    def setUp(self) -> None:
        self.socket_name = f"LingoFlash_Test_{os.getpid()}_{id(self)}"
        QLocalServer.removeServer(self.socket_name)

    def tearDown(self) -> None:
        QLocalServer.removeServer(self.socket_name)

    def test_single_instance_detection_and_command_exchange(self) -> None:
        """Verify that a second instance detects the first and transmits commands."""
        server = IPCServer(app_name=self.socket_name)
        self.assertFalse(server.is_already_running())

        # Start server
        started = server.start_listening()
        self.assertTrue(started)

        received_commands: list[str] = []
        server.command_received.connect(received_commands.append)

        # Second instance should detect running server
        client_ipc = IPCServer(app_name=self.socket_name)
        self.assertTrue(client_ipc.is_already_running())

        # Send command from client
        sent = client_ipc.send_command("translate_selection")
        self.assertTrue(sent)

        # Process Qt events so server receives connection and processes payload
        for _ in range(10):
            self._app.processEvents()

        self.assertIn("translate_selection", received_commands)

        # Clean up
        server.cleanup()
        client_ipc.cleanup()

    def test_handle_invalid_payload_gracefully(self) -> None:
        """Verify server does not crash on malformed payloads."""
        server = IPCServer(app_name=self.socket_name)
        server.start_listening()

        received_commands: list[str] = []
        server.command_received.connect(received_commands.append)

        # Connect a raw socket and send invalid json bytes
        socket = QLocalSocket()
        socket.connectToServer(self.socket_name)
        self.assertTrue(socket.waitForConnected(1000))
        socket.write(b"NOT_A_VALID_JSON{")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()

        for _ in range(5):
            self._app.processEvents()

        # No command emitted, server remains healthy
        self.assertEqual(len(received_commands), 0)
        server.cleanup()

    def test_ipc_command_forwards_environment_variables(self) -> None:
        """Verify IPC command forwards and updates graphical session environment variables."""
        server = IPCServer(app_name=self.socket_name)
        server.start_listening()

        client_ipc = IPCServer(app_name=self.socket_name)
        custom_env = {"WAYLAND_DISPLAY": "wayland-test-99", "XDG_CURRENT_DESKTOP": "GNOME"}
        sent = client_ipc.send_command("ocr_capture", env=custom_env)
        self.assertTrue(sent)

        for _ in range(10):
            self._app.processEvents()

        self.assertEqual(os.environ.get("WAYLAND_DISPLAY"), "wayland-test-99")
        server.cleanup()
        client_ipc.cleanup()

    def test_handle_payload_without_action_key(self) -> None:
        """Verify server ignores JSON lacking 'action' key without error."""
        server = IPCServer(app_name=self.socket_name)
        server.start_listening()

        received_commands: list[str] = []
        server.command_received.connect(received_commands.append)

        socket = QLocalSocket()
        socket.connectToServer(self.socket_name)
        self.assertTrue(socket.waitForConnected(1000))
        socket.write(b'{"key": "value"}')
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()

        for _ in range(5):
            self._app.processEvents()

        self.assertEqual(len(received_commands), 0)
        server.cleanup()


if __name__ == "__main__":
    unittest.main()
