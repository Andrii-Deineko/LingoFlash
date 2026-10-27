"""Unit tests for desktop dock integration, .desktop generation, and cross-distro OCR compatibility."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PyQt6.QtWidgets import QApplication

from app.ocr.ocr_engine import ScreenCapture
from app.utils.desktop import (
    generate_desktop_entry_content,
    install_desktop_file,
    get_project_root,
)


class TestDesktopDockIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if QApplication.instance() is None:
            cls._app = QApplication(sys.argv)
        else:
            cls._app = QApplication.instance()

        cls._app.setApplicationName("LingoFlash")
        cls._app.setApplicationDisplayName("LingoFlash")
        cls._app.setDesktopFileName("lingoflash.desktop")

    def test_app_identity_metadata_for_wayland(self) -> None:
        """Verify Qt application name and desktop file name are set for Wayland dock pairing."""
        self.assertEqual(self._app.applicationName(), "LingoFlash")
        self.assertEqual(self._app.applicationDisplayName(), "LingoFlash")
        self.assertIn(self._app.desktopFileName(), ("lingoflash", "lingoflash.desktop"))

    def test_generate_desktop_entry_content(self) -> None:
        """Verify .desktop file content has absolute paths and StartupWMClass=LingoFlash."""
        content = generate_desktop_entry_content()
        self.assertIn("[Desktop Entry]", content)
        self.assertIn("Name=LingoFlash", content)
        self.assertIn("StartupWMClass=LingoFlash", content)
        self.assertIn("Type=Application", content)
        self.assertIn("Terminal=false", content)

        # Must have absolute paths for Exec and Icon
        root = get_project_root()
        self.assertIn(str(root / "main.py"), content)
        self.assertIn(str(root / "assets" / "logo.svg"), content)

    def test_install_desktop_file_writes_to_directory(self) -> None:
        """Verify install_desktop_file writes lingoflash.desktop into target directory."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = Path(tmp_dir)
            installed = install_desktop_file(target_dir=target_path, update_database=False)
            self.assertTrue(installed.is_file())
            self.assertEqual(installed.name, "lingoflash.desktop")

            text = installed.read_text(encoding="utf-8")
            self.assertIn("Name=LingoFlash", text)
            self.assertIn("StartupWMClass=LingoFlash", text)


class TestCrossDistroOCR(unittest.TestCase):
    def test_capture_region_spectacle_kde(self) -> None:
        """Verify spectacle is executed when available on KDE Plasma."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        def mock_which(cmd: str):
            if cmd == "spectacle":
                return "/usr/bin/spectacle"
            return None

        with patch("shutil.which", side_effect=mock_which), \
             patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=4096):

            target = "/tmp/test_kde_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            mock_run.assert_called_once_with(
                ["spectacle", "-r", "-b", "-n", "-o", target],
                capture_output=True,
                text=True,
                env=unittest.mock.ANY,
                check=False,
            )

    def test_capture_region_grim_slurp_wlroots(self) -> None:
        """Verify grim and slurp are executed on wlroots compositors (Sway/Hyprland)."""
        slurp_proc = MagicMock(spec=subprocess.CompletedProcess)
        slurp_proc.returncode = 0
        slurp_proc.stdout = "100,200 300x400"

        grim_proc = MagicMock(spec=subprocess.CompletedProcess)
        grim_proc.returncode = 0

        def mock_which(cmd: str):
            if cmd in ("grim", "slurp"):
                return f"/usr/bin/{cmd}"
            return None

        with patch("shutil.which", side_effect=mock_which), \
             patch("subprocess.run", side_effect=[slurp_proc, grim_proc]) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=2048):

            target = "/tmp/test_wlroots_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            self.assertEqual(mock_run.call_count, 2)
            mock_run.assert_any_call(
                ["slurp"],
                capture_output=True,
                text=True,
                env=unittest.mock.ANY,
                check=False,
            )
            mock_run.assert_any_call(
                ["grim", "-g", "100,200 300x400", target],
                capture_output=True,
                text=True,
                env=unittest.mock.ANY,
                check=False,
            )

    def test_capture_region_scrot_x11(self) -> None:
        """Verify scrot is executed on X11 when present."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        def mock_which(cmd: str):
            if cmd == "scrot":
                return "/usr/bin/scrot"
            return None

        with patch("shutil.which", side_effect=mock_which), \
             patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=1024):

            target = "/tmp/test_scrot_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            mock_run.assert_called_once_with(
                ["scrot", "-s", target],
                capture_output=True,
                text=True,
                env=unittest.mock.ANY,
                check=False,
            )

    def test_capture_region_maim_x11(self) -> None:
        """Verify maim is executed as X11 fallback when scrot is absent."""
        mock_proc = MagicMock(spec=subprocess.CompletedProcess)
        mock_proc.returncode = 0

        def mock_which(cmd: str):
            if cmd == "maim":
                return "/usr/bin/maim"
            return None

        with patch("shutil.which", side_effect=mock_which), \
             patch("subprocess.run", return_value=mock_proc) as mock_run, \
             patch("os.path.isfile", return_value=True), \
             patch("os.path.getsize", return_value=1024):

            target = "/tmp/test_maim_ocr.png"
            res = ScreenCapture.capture_region(target)
            self.assertEqual(res, target)
            mock_run.assert_called_once_with(
                ["maim", "-s", target],
                capture_output=True,
                text=True,
                env=unittest.mock.ANY,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
