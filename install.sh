#!/usr/bin/env bash
# ==============================================================================
# LingoFlash Universal Linux Installer
# Supports: Ubuntu, Debian, Linux Mint, Fedora, RHEL, Arch Linux, Manjaro, openSUSE
# ==============================================================================

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}====================================================${NC}"
echo -e "${BLUE}          LingoFlash Universal Installer           ${NC}"
echo -e "${BLUE}====================================================${NC}"

# Detect distribution package manager
if command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
elif command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
elif command -v pacman >/dev/null 2>&1; then
    PKG_MGR="pacman"
elif command -v zypper >/dev/null 2>&1; then
    PKG_MGR="zypper"
else
    echo -e "${YELLOW}Warning: Unknown package manager. Please ensure Python3, Tesseract OCR, and screenshot tools are installed manually.${NC}"
    PKG_MGR="unknown"
fi

echo -e "${GREEN}==> Detected package manager: ${PKG_MGR}${NC}"

case "$PKG_MGR" in
    dnf)
        echo -e "${GREEN}==> Installing dependencies via dnf...${NC}"
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
            tesseract-langpack-swe \
            wl-clipboard \
            xclip \
            gnome-screenshot \
            slurp \
            grim \
            maim \
            desktop-file-utils
        ;;
    apt)
        echo -e "${GREEN}==> Installing dependencies via apt-get...${NC}"
        sudo apt-get update -y
        sudo apt-get install -y \
            python3-pip \
            python3-dev \
            tesseract-ocr \
            tesseract-ocr-eng \
            tesseract-ocr-deu \
            tesseract-ocr-fra \
            tesseract-ocr-spa \
            tesseract-ocr-ukr \
            tesseract-ocr-rus \
            tesseract-ocr-swe \
            wl-clipboard \
            xclip \
            gnome-screenshot \
            slurp \
            grim \
            scrot \
            desktop-file-utils
        ;;
    pacman)
        echo -e "${GREEN}==> Installing dependencies via pacman...${NC}"
        sudo pacman -S --needed --noconfirm \
            python-pip \
            tesseract \
            tesseract-data-eng \
            tesseract-data-deu \
            tesseract-data-fra \
            tesseract-data-spa \
            tesseract-data-ukr \
            tesseract-data-rus \
            tesseract-data-swe \
            wl-clipboard \
            xclip \
            grim \
            slurp \
            maim \
            desktop-file-utils
        ;;
    zypper)
        echo -e "${GREEN}==> Installing dependencies via zypper...${NC}"
        sudo zypper install -y \
            python3-pip \
            python3-devel \
            tesseract-ocr \
            tesseract-ocr-traineddata-english \
            tesseract-ocr-traineddata-german \
            tesseract-ocr-traineddata-french \
            tesseract-ocr-traineddata-spanish \
            tesseract-ocr-traineddata-ukrainian \
            tesseract-ocr-traineddata-russian \
            tesseract-ocr-traineddata-swedish \
            wl-clipboard \
            xclip \
            desktop-file-utils
        ;;
    *)
        echo -e "${YELLOW}Skipping system package installation.${NC}"
        ;;
esac

echo ""
echo -e "${GREEN}==> Installing Python dependencies from requirements.txt...${NC}"
pip3 install --user -r requirements.txt || pip install -r requirements.txt

echo ""
echo -e "${GREEN}==> Registering native desktop launcher (.desktop) with system dock...${NC}"
python3 setup.py install_desktop

echo ""
echo -e "${GREEN}====================================================${NC}"
echo -e "${GREEN}  LingoFlash is successfully installed and ready!  ${NC}"
echo -e "${GREEN}====================================================${NC}"
echo -e "You can now launch LingoFlash:"
echo -e "  1. From your Application Menu / Dock (search for 'LingoFlash')"
echo -e "  2. Or run: python3 main.py"
echo ""
