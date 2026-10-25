#!/bin/bash
# Install system dependencies for LingoFlash on Fedora
set -e
echo "Installing system dependencies..."
sudo dnf install -y \
    python3-pip \
    python3-devel \
    tesseract \
    tesseract-langpack-eng \
    tesseract-langpack-deu \
    tesseract-langpack-fra \
    tesseract-langpack-spa \
    tesseract-langpack-ukr \
    tesseract-langpack-rus \
    wl-clipboard \
    gnome-screenshot \
    slurp \
    grim \
    gnome-shell-extension-appindicator

echo ""
echo "Installing Python dependencies..."
pip install -r requirements.txt

echo ""
echo "Done! You can now run: python main.py"
