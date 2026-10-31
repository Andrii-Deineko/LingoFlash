"""Setup script for LingoFlash desktop translation client."""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

try:
    from setuptools import setup, find_packages, Command
    HAVE_SETUPTOOLS = True
except ImportError:
    HAVE_SETUPTOOLS = False


def install_desktop() -> None:
    """Standalone installer for the .desktop launcher."""
    from app.utils.desktop import install_desktop_file

    dest = install_desktop_file()
    print(f"Successfully generated and installed desktop launcher at: {dest}")


if not HAVE_SETUPTOOLS:
    if len(sys.argv) > 1 and "install" in sys.argv[1]:
        install_desktop()
        sys.exit(0)
    else:
        print("Note: setuptools is not installed in this environment.")
        print("To install the desktop launcher, run: python3 setup.py install_desktop")
        if len(sys.argv) == 1:
            install_desktop()
            sys.exit(0)


class InstallDesktopCommand(Command):
    """Custom command to install the Linux desktop launcher (.desktop file)."""

    description = "Install the .desktop launcher into ~/.local/share/applications"
    user_options = []

    def initialize_options(self) -> None:
        pass

    def finalize_options(self) -> None:
        pass

    def run(self) -> None:
        install_desktop()


setup(
    name="lingoflash",
    version="0.1.0",
    description="LingoFlash — Quick desktop translation client with Wayland & X11 OCR",
    author="Andrii Deineko",
    author_email="lingoflash@example.com",
    url="https://github.com/Andrii-Deineko/LingoFlash",
    packages=find_packages(),
    include_package_data=True,
    python_requires=">=3.9",
    install_requires=[
        "PyQt6>=6.4.0",
        "requests>=2.28.0",
        "deep-translator>=1.11.0",
        "pytesseract>=0.3.10",
        "Pillow>=9.0.0",
        "gTTS>=2.3.0",
        "lingua-language-detector>=2.0.0",
        "SpeechRecognition>=3.10.0",
    ],
    entry_points={
        "gui_scripts": [
            "lingoflash=main:main",
        ],
        "console_scripts": [
            "lingoflash-cli=main:main",
        ],
    },
    cmdclass={
        "install_desktop": InstallDesktopCommand,
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Environment :: X11 Applications :: Qt",
        "Intended Audience :: End Users/Desktop",
        "License :: OSI Approved :: MIT License",
        "Operating System :: POSIX :: Linux",
        "Programming Language :: Python :: 3",
        "Topic :: Desktop Environment",
        "Topic :: Education",
    ],
)
