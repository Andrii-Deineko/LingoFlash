"""OCR engine for screen region capture and text extraction.

Provides Wayland screen capture via external tools (gnome-screenshot)
and text extraction via Tesseract OCR and Pillow with image preprocessing.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytesseract
from PIL import Image, ImageEnhance
from PyQt6.QtCore import QThread, pyqtSignal

logger = logging.getLogger(__name__)

DEFAULT_OCR_IMAGE = "/tmp/lingo_ocr.png"


class ScreenCapture:
    """Captures a user-selected screen region to a PNG file.

    On Fedora GNOME (Wayland), delegates directly to gnome-screenshot (-a -f)
    and waits for the interactive area selection to complete.
    """

    last_geometry: Optional[tuple[int, int, int, int]] = None

    @staticmethod
    def _detect_session_type() -> str:
        """Detect the current display session type.

        Returns:
            ``'wayland'`` or ``'x11'``.
        """
        session = os.environ.get("XDG_SESSION_TYPE", "").lower()
        if session == "wayland":
            return "wayland"
        wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
        if wayland_display:
            return "wayland"
        return "x11"

    @classmethod
    def get_session_env(cls) -> dict[str, str]:
        """Construct a complete, active graphical session environment for subprocesses."""
        env = os.environ.copy()
        uid = os.getuid()
        runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{uid}"
        if os.path.isdir(runtime_dir):
            env["XDG_RUNTIME_DIR"] = runtime_dir

        if not env.get("DBUS_SESSION_BUS_ADDRESS"):
            bus_path = f"{runtime_dir}/bus"
            if os.path.exists(bus_path):
                env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

        if not env.get("WAYLAND_DISPLAY"):
            if os.path.isdir(runtime_dir):
                for fname in ("wayland-0", "wayland-1"):
                    if os.path.exists(os.path.join(runtime_dir, fname)):
                        env["WAYLAND_DISPLAY"] = fname
                        break

        if not env.get("DISPLAY"):
            env["DISPLAY"] = ":0"

        if not env.get("XDG_SESSION_TYPE"):
            env["XDG_SESSION_TYPE"] = "wayland" if env.get("WAYLAND_DISPLAY") else "x11"

        if not env.get("XDG_CURRENT_DESKTOP"):
            env["XDG_CURRENT_DESKTOP"] = "GNOME"

        if env.get("WAYLAND_DISPLAY"):
            env.setdefault("GDK_BACKEND", "wayland,x11,*")

        for k in (
            "WAYLAND_DISPLAY",
            "DISPLAY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "XDG_SESSION_TYPE",
            "XDG_CURRENT_DESKTOP",
        ):
            if k in env and not os.environ.get(k):
                os.environ[k] = env[k]

        return env

    @classmethod
    def _capture_wayland(cls, output_path: str = DEFAULT_OCR_IMAGE) -> Optional[str]:
        """Capture a screen region on Wayland using gnome-screenshot."""
        return cls.capture_region(output_path)

    @classmethod
    def _capture_x11(cls, output_path: str = DEFAULT_OCR_IMAGE) -> Optional[str]:
        """Capture a screen region."""
        return cls.capture_region(output_path)

    @classmethod
    def _parse_dbus_response(
        cls, stdout: str, output_path: str, env: dict[str, str]
    ) -> Optional[str]:
        """Parse gdbus SelectArea/ScreenshotArea response and ensure image at output_path."""
        if not stdout:
            return None

        # 1. Check if the response returned a file path or URI directly
        # E.g. (true, '/path/to/img.png') or ('file:///path/to/img.png') or ('/tmp/...')
        path_match = re.search(r"'(?:file://)?(/[^']+)'", stdout) or re.search(r'"(?:file://)?(/[^"]+)"', stdout)
        if path_match:
            raw_path = path_match.group(1)
            if os.path.isfile(raw_path) and os.path.getsize(raw_path) > 0:
                if os.path.abspath(raw_path) != os.path.abspath(output_path):
                    shutil.copy2(raw_path, output_path)
                if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    logger.info("Extracted screenshot from DBus response path: %s", output_path)
                    return output_path

        # 2. Check if the response returned area coordinates: (x, y, width, height)
        # GNOME Shell SelectArea signature: (out i x, out i y, out i width, out i height)
        # E.g. (int32 100, int32 200, int32 300, int32 400) or (100, 200, 300, 400)
        clean_text = re.sub(r"(?:int|uint)\d+\s+", "", stdout)
        nums = [int(n) for n in re.findall(r"-?\d+", clean_text)]
        if len(nums) >= 4:
            x, y, w, h = nums[0], nums[1], nums[2], nums[3]
            if w > 0 and h > 0:
                cls.last_geometry = (x, y, w, h)
                logger.info("GNOME Shell DBus SelectArea coordinates: x=%d, y=%d, w=%d, h=%d", x, y, w, h)
                # Capture the selected region via ScreenshotArea
                area_cmd = [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell.Screenshot",
                    "--object-path", "/org/gnome/Shell/Screenshot",
                    "--method", "org.gnome.Shell.Screenshot.ScreenshotArea",
                    str(x), str(y), str(w), str(h), "false", output_path,
                ]
                area_res = subprocess.run(
                    area_cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                if area_res.stderr:
                    logger.debug("DBus ScreenshotArea stderr: %s", area_res.stderr.strip())

                if area_res.stdout:
                    sub_match = re.search(r"'(?:file://)?(/[^']+)'", area_res.stdout) or re.search(r'"(?:file://)?(/[^"]+)"', area_res.stdout)
                    if sub_match:
                        raw_sub = sub_match.group(1)
                        if os.path.isfile(raw_sub) and os.path.abspath(raw_sub) != os.path.abspath(output_path):
                            shutil.copy2(raw_sub, output_path)

                if os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    logger.info("Captured area via DBus ScreenshotArea: %s", output_path)
                    return output_path
            else:
                logger.info("User cancelled DBus area selection (empty dimensions: %dx%d)", w, h)
                return None

        return None

    @classmethod
    def capture_region(cls, output_path: str = DEFAULT_OCR_IMAGE) -> Optional[str]:
        """Capture a user-selected screen region to a PNG file.

        On GNOME Wayland (Fedora), executes ``gnome-screenshot -a -f <path>``
        using ``subprocess.run()`` with ``env=os.environ.copy()``. If that fails
        or produces no image, falls back to the native GNOME Shell DBus API
        (``SelectArea``). Completely blocks and waits for the user to finish
        drawing the box.

        Args:
            output_path: Target PNG file path (defaults to ``/tmp/lingo_ocr.png``).

        Returns:
            Absolute path to the captured PNG image, or ``None`` if the
            user cancelled the selection or tools failed.
        """
        # Ensure any stale image from a previous capture is removed
        if os.path.isfile(output_path):
            try:
                os.remove(output_path)
            except OSError:
                pass

        logger.info("Initiating interactive screen capture (target=%s)", output_path)
        env = cls.get_session_env()

        # 1. Primary GNOME / Wayland tool: gnome-screenshot -a -f output_path
        # Blocks and waits synchronously for the user to drag and select screen area
        if shutil.which("gnome-screenshot"):
            try:
                logger.debug("Running gnome-screenshot -a -f %s with environment copy", output_path)
                res = subprocess.run(
                    ["gnome-screenshot", "-a", "-f", output_path],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                if res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                    logger.info("Captured area via gnome-screenshot: %s", output_path)
                    return output_path

                stderr = getattr(res, "stderr", "") or ""
                stdout = getattr(res, "stdout", "") or ""
                if stderr:
                    logger.warning("gnome-screenshot failed (rc=%d): stderr=%s", res.returncode, stderr.strip())
                elif stdout:
                    logger.info("gnome-screenshot output (rc=%d): stdout=%s", res.returncode, stdout.strip())
                else:
                    logger.info("gnome-screenshot returned rc=%d with no image output", res.returncode)

            except FileNotFoundError:
                logger.debug("gnome-screenshot binary not found")
            except Exception as exc:
                logger.warning("gnome-screenshot execution exception: %s", exc)

        # 2. Native GNOME Shell DBus API alternative: SelectArea
        if shutil.which("gdbus"):
            try:
                logger.debug("Attempting interactive area selection via GNOME Shell DBus API")
                dbus_cmd = [
                    "gdbus", "call", "--session",
                    "--dest", "org.gnome.Shell.Screenshot",
                    "--object-path", "/org/gnome/Shell/Screenshot",
                    "--method", "org.gnome.Shell.Screenshot.SelectArea",
                ]
                dbus_res = subprocess.run(
                    dbus_cmd,
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                dbus_stdout = getattr(dbus_res, "stdout", "") or ""
                dbus_stderr = getattr(dbus_res, "stderr", "") or ""
                if dbus_res.returncode == 0 and dbus_stdout.strip():
                    dbus_file = cls._parse_dbus_response(dbus_stdout.strip(), output_path, env)
                    if dbus_file and os.path.isfile(dbus_file) and os.path.getsize(dbus_file) > 0:
                        return dbus_file
                else:
                    if dbus_stderr:
                        logger.warning("DBus SelectArea error (rc=%d): %s", dbus_res.returncode, dbus_stderr.strip())
            except Exception as exc:
                logger.warning("DBus SelectArea exception: %s", exc)

        # 3. Fallback for wlroots Wayland compositors (grim + slurp)
        if shutil.which("grim") and shutil.which("slurp"):
            try:
                slurp_res = subprocess.run(
                    ["slurp"],
                    capture_output=True,
                    text=True,
                    env=env,
                    check=False,
                )
                if slurp_res.returncode == 0 and slurp_res.stdout.strip():
                    region = slurp_res.stdout.strip()
                    grim_res = subprocess.run(
                        ["grim", "-g", region, output_path],
                        capture_output=True,
                        text=True,
                        env=env,
                        check=False,
                    )
                    if grim_res.returncode == 0 and os.path.isfile(output_path) and os.path.getsize(output_path) > 0:
                        logger.info("Captured area via grim+slurp: %s", output_path)
                        return output_path
            except Exception as exc:
                logger.debug("grim/slurp fallback exception: %s", exc)

        if not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            logger.info("Screen capture was cancelled or failed to produce an image")
            return None

        return output_path


class OCREngine:
    """Tesseract-based OCR engine with image preprocessing.

    Args:
        lang: Tesseract language code (e.g. ``"eng"``, ``"ukr"``, or ``"auto"``).

    Raises:
        RuntimeError: If tesseract is not found on the system.
    """

    def __init__(self, lang: str = "auto") -> None:
        self.lang: str = lang
        self._validate_tesseract()
        logger.info("OCREngine initialised with language '%s'", self.lang)

    @staticmethod
    def _validate_tesseract() -> None:
        """Ensure the ``tesseract`` binary is available on *PATH*.

        Raises:
            RuntimeError: If tesseract cannot be executed.
        """
        try:
            subprocess.run(
                ["tesseract", "--version"],
                capture_output=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(
                "Tesseract OCR is not installed or not found on PATH. "
                "Install it with: sudo dnf install tesseract"
            ) from exc

    @staticmethod
    def preprocess_image(image_path: str) -> Image.Image:
        """Open and preprocess an image for better OCR accuracy.

        The pipeline applies, in order:
        1. Grayscale conversion
        2. Contrast enhancement (factor 1.5)
        3. Sharpness enhancement (factor 2.0)
        4. Binary thresholding (pixel > 128 → 255, else 0)

        Args:
            image_path: Absolute path to the source image file.

        Returns:
            A preprocessed PIL :class:`~PIL.Image.Image`.
        """
        logger.debug("Preprocessing image: %s", image_path)

        img = Image.open(image_path)

        # Convert to grayscale
        img = img.convert("L")

        # Enhance contrast
        img = ImageEnhance.Contrast(img).enhance(1.5)

        # Enhance sharpness
        img = ImageEnhance.Sharpness(img).enhance(2.0)

        # Binary thresholding
        img = img.point(lambda px: 255 if px > 128 else 0, mode="1")

        logger.debug("Image preprocessing complete")
        return img

    def extract_text(self, image_path: str = DEFAULT_OCR_IMAGE) -> str:
        """Extract text from an image via Tesseract OCR and Pillow.

        The image is preprocessed first, then passed to Tesseract. If the preprocessed
        image yields minimal or no text, it falls back to the original image.

        Args:
            image_path: Absolute path to the source image file.

        Returns:
            Cleaned extracted text.
        """
        if not os.path.isfile(image_path) or os.path.getsize(image_path) == 0:
            logger.warning("OCR image path does not exist or is empty: %s", image_path)
            return ""

        logger.info("Extracting text from %s (lang=%s)", image_path, self.lang)

        # Multi-language detection
        ocr_lang = self.lang
        if not ocr_lang or ocr_lang == "auto":
            available = self.get_available_languages()
            if available:
                preferred = ["eng", "ukr", "rus", "deu", "fra", "spa"]
                active = [l for l in preferred if l in available]
                ocr_lang = "+".join(active) if active else "+".join(available)
            else:
                ocr_lang = "eng"

        raw_text = ""
        # 1. Attempt OCR with preprocessed image
        try:
            processed_img = self.preprocess_image(image_path)
            raw_text = pytesseract.image_to_string(processed_img, lang=ocr_lang)
        except Exception as exc:
            logger.debug("Preprocessed OCR attempt error: %s", exc)

        # 2. Fallback to raw PIL image if preprocessing produced no text
        if not raw_text or len(raw_text.strip()) < 2:
            try:
                raw_img = Image.open(image_path)
                raw_text = pytesseract.image_to_string(raw_img, lang=ocr_lang)
            except Exception as exc:
                logger.warning("Raw image OCR error: %s", exc)

        logger.debug("Raw OCR output length: %d chars", len(raw_text))

        cleaned = self._postprocess_text(raw_text)
        logger.info("Extracted %d chars of cleaned text", len(cleaned))
        return cleaned

    @staticmethod
    def _postprocess_text(text: str) -> str:
        """Normalise OCR output: remove form feeds, excess newlines, and garbage characters.

        - Strips leading/trailing whitespace and Tesseract page breaks (\\x0c).
        - Preserves paragraph breaks (double newlines).
        - Replaces single newlines with spaces (joining broken lines).
        - Collapses runs of spaces into a single space.

        Args:
            text: Raw text from Tesseract.

        Returns:
            Cleaned text string.
        """
        text = text.strip() if text else ""
        if not text:
            return ""

        # Remove form feed (\x0c) and null bytes
        text = text.replace("\x0c", "").replace("\x00", "")

        # Normalise line endings
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # Strip non-printable ASCII/control characters except \n and \t
        text = "".join(c for c in text if c in ("\n", "\t") or c.isprintable())

        # Preserve paragraph breaks: replace double-newlines with a placeholder
        placeholder = "\x00PARA\x00"
        text = re.sub(r"[ \t]*\n{2,}[ \t]*", placeholder, text)

        # Join broken lines (single newlines → spaces)
        text = re.sub(r"[ \t]*\n[ \t]*", " ", text)

        # Restore paragraph breaks
        text = text.replace(placeholder, "\n\n")

        # Collapse runs of spaces
        text = re.sub(r" {2,}", " ", text)

        # Clean spaces around paragraph breaks
        text = re.sub(r"[ \t]*\n\n[ \t]*", "\n\n", text)

        return text.strip()

    def extract_overlay_blocks(self, image_path: str = DEFAULT_OCR_IMAGE) -> list[dict[str, Any]]:
        """Extract positioned text blocks/bubbles for in-place overlay rendering.

        Returns:
            List of dicts: ``{"box": (x, y, w, h), "text": str}``.
        """
        if not os.path.isfile(image_path) or os.path.getsize(image_path) == 0:
            return []

        img = Image.open(image_path)
        available = self.get_available_languages()
        ocr_lang = self.lang
        if not ocr_lang or ocr_lang == "auto":
            if available:
                preferred = ["eng", "ukr", "rus", "deu", "fra", "spa"]
                active = [l for l in preferred if l in available]
                ocr_lang = "+".join(active) if active else "+".join(available)
            else:
                ocr_lang = "eng"

        try:
            data = pytesseract.image_to_data(img, lang=ocr_lang, output_type=pytesseract.Output.DICT)
        except Exception as exc:
            logger.warning("image_to_data failed: %s", exc)
            return []

        blocks: dict[int, dict[str, Any]] = {}
        n_boxes = len(data.get("text", []))
        for i in range(n_boxes):
            txt = data["text"][i].strip()
            # Filter noise and empty words
            if not txt or len(txt) == 0 or txt in ("|", "~", "_"):
                continue
            b_id = data["block_num"][i]
            x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
            if b_id not in blocks:
                blocks[b_id] = {
                    "x1": x,
                    "y1": y,
                    "x2": x + w,
                    "y2": y + h,
                    "words": [txt],
                }
            else:
                b = blocks[b_id]
                b["x1"] = min(b["x1"], x)
                b["y1"] = min(b["y1"], y)
                b["x2"] = max(b["x2"], x + w)
                b["y2"] = max(b["y2"], y + h)
                b["words"].append(txt)

        result: list[dict[str, Any]] = []
        for _, b in blocks.items():
            text = " ".join(b["words"]).strip()
            cleaned = self._postprocess_text(text)
            if cleaned and any(c.isalnum() for c in cleaned):
                bw = b["x2"] - b["x1"]
                bh = b["y2"] - b["y1"]
                result.append({
                    "box": (b["x1"], b["y1"], bw, bh),
                    "text": cleaned,
                })

        return result

    @staticmethod
    def get_available_languages() -> list[str]:
        """Query Tesseract for installed language data files.

        Returns:
            A sorted list of available language codes (e.g.
            ``["eng", "fra", "ukr"]``).  Returns an empty list if
            tesseract is not available.
        """
        try:
            result = subprocess.run(
                ["tesseract", "--list-langs"],
                capture_output=True,
                text=True,
                check=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError):
            logger.warning("Could not query Tesseract for available languages")
            return []

        # The first line is a header ("List of available languages …");
        # language codes follow, one per line.
        lines = result.stdout.strip().splitlines()
        langs = [line.strip() for line in lines[1:] if line.strip()]
        langs.sort()
        logger.debug("Available Tesseract languages: %s", langs)
        return langs


class OCRWorker(QThread):
    """Asynchronous worker that captures a screen region and runs OCR.

    Signals:
        finished(str): Emitted with the extracted text on success.
        error(str): Emitted with an error description on failure.
        worker_done(): Emitted on thread completion for lifecycle cleanup.

    Args:
        lang: Tesseract language code forwarded to :class:`OCREngine`.
        output_path: File path to save the screenshot.
    """

    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    worker_done = pyqtSignal()

    def __init__(self, lang: str = "auto", output_path: str = DEFAULT_OCR_IMAGE) -> None:
        super().__init__()
        self.lang: str = lang
        self.output_path: str = output_path

    def run(self) -> None:
        """Execute screen capture followed by OCR in a background thread."""
        image_path: Optional[str] = None
        try:
            logger.info("OCRWorker started (lang=%s, target=%s)", self.lang, self.output_path)

            image_path = ScreenCapture.capture_region(self.output_path)
            if image_path is None or not os.path.isfile(image_path) or os.path.getsize(image_path) == 0:
                # User cancelled or tool failed
                logger.info("Screen capture was cancelled or produced no file")
                self.finished.emit("")
                return

            engine = OCREngine(lang=self.lang)
            text = engine.extract_text(image_path)
            self.finished.emit(text)
            logger.info("OCRWorker finished successfully with %d characters", len(text))

        except Exception as exc:  # noqa: BLE001
            logger.exception("OCRWorker encountered an error")
            self.error.emit(str(exc))
        finally:
            self.worker_done.emit()


class OCROverlayWorker(QThread):
    """Asynchronous worker for Comic/Game in-place overlay OCR and translation.

    Signals:
        overlay_ready(list, object): Emitted with ``(translated_blocks, screen_rect)``.
        error(str): Emitted on failure.
        worker_done(): Emitted on thread completion.
    """

    overlay_ready = pyqtSignal(list, object)
    error = pyqtSignal(str)
    worker_done = pyqtSignal()

    def __init__(
        self,
        translation_manager: Any,
        target_lang: str = "uk",
        ocr_lang: str = "auto",
        output_path: str = DEFAULT_OCR_IMAGE,
    ) -> None:
        super().__init__()
        self._manager = translation_manager
        self._target_lang = target_lang
        self._ocr_lang = ocr_lang
        self._output_path = output_path

    def run(self) -> None:
        """Capture region, extract blocks, translate each block, and emit overlay data."""
        try:
            logger.info("OCROverlayWorker started (target_lang=%s)", self._target_lang)
            img_path = ScreenCapture.capture_region(self._output_path)
            screen_geom = ScreenCapture.last_geometry

            if img_path is None or not os.path.isfile(img_path):
                logger.info("Screen capture was cancelled for overlay")
                self.overlay_ready.emit([], None)
                return

            engine = OCREngine(lang=self._ocr_lang)
            blocks = engine.extract_overlay_blocks(img_path)

            if not blocks:
                # Fallback: try whole-image text extraction
                full_text = engine.extract_text(img_path)
                if full_text:
                    blocks = [{"box": (10, 10, 200, 60), "text": full_text}]

            # Translate each block
            translated_blocks = []
            for b in blocks:
                orig_text = b["text"]
                try:
                    if hasattr(self._manager, "translate"):
                        trans = self._manager.translate(orig_text, source="auto", target=self._target_lang)
                    else:
                        trans = orig_text
                except Exception as exc:
                    logger.warning("Failed to translate block %r: %s", orig_text[:20], exc)
                    trans = orig_text

                b["translated"] = trans
                translated_blocks.append(b)

            self.overlay_ready.emit(translated_blocks, screen_geom)
            logger.info("OCROverlayWorker completed with %d blocks", len(translated_blocks))

        except Exception as exc:
            logger.exception("OCROverlayWorker encountered error")
            self.error.emit(str(exc))
        finally:
            self.worker_done.emit()
