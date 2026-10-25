"""Smart In-Place OCR Screen Overlay for Comic/Manga/Game translation.

Displays translated floating text bubbles directly on the screen over the
original text blocks, preserving context without opening a separate window.
Dismissible with Esc or backdrop click.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PyQt6.QtCore import Qt, QPoint, QRect, QTimer
from PyQt6.QtGui import QColor, QFont, QCursor, QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QGraphicsDropShadowEffect,
    QApplication,
    QScrollArea,
)

from app.clipboard.clipboard_manager import ClipboardManager

logger = logging.getLogger(__name__)


class TranslatedBubbleCard(QFrame):
    """Floating card placed directly over a detected text block on screen."""

    def __init__(
        self,
        original_text: str,
        translated_text: str,
        box: tuple[int, int, int, int],  # (rel_x, rel_y, width, height)
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._clipboard = ClipboardManager()
        self._original = original_text
        self._translated = translated_text
        self._box = box

        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("ocrBubbleCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"Original: {self._original}\n(Click to copy translation)")

        # Frosted dark floating style with subtle border
        self.setStyleSheet("""
            QFrame#ocrBubbleCard {
                background-color: rgba(18, 18, 24, 0.94);
                border: 1px solid rgba(255, 255, 255, 0.18);
                border-radius: 8px;
            }
            QFrame#ocrBubbleCard:hover {
                background-color: rgba(26, 26, 36, 0.98);
                border: 1px solid #3b82f6;
            }
        """)

        # Drop shadow for clean pop from game/comic background
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 4)
        self.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        # Translated text
        self._lbl_text = QLabel(self._translated, self)
        self._lbl_text.setWordWrap(True)
        self._lbl_text.setStyleSheet("""
            QLabel {
                color: #ffffff;
                font-size: 13px;
                font-weight: 500;
                background: transparent;
                border: none;
            }
        """)
        layout.addWidget(self._lbl_text)

        # Subtle copy badge on click
        self._lbl_copied = QLabel("", self)
        self._lbl_copied.setStyleSheet("color: #60a5fa; font-size: 10px; font-weight: 600; background: transparent;")
        self._lbl_copied.hide()
        layout.addWidget(self._lbl_copied)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._clipboard.copy_to_clipboard(self._translated)
            self._lbl_copied.setText("✓ Copied!")
            self._lbl_copied.show()
            QTimer.singleShot(1500, self._lbl_copied.hide)
            event.accept()
        else:
            super().mousePressEvent(event)


class OCROverlayWindow(QWidget):
    """Transparent, frameless fullscreen/region overlay rendering in-place translation bubbles."""

    def __init__(
        self,
        blocks: list[dict[str, Any]],
        screen_rect: Optional[tuple[int, int, int, int]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._blocks = blocks
        self._screen_rect = screen_rect
        self._cards: list[TranslatedBubbleCard] = []

        self._setup_window()
        self._populate_bubbles()

    def _setup_window(self) -> None:
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        # Position overlay over captured region or near cursor
        if self._screen_rect is not None and len(self._screen_rect) == 4:
            x, y, w, h = self._screen_rect
            # Add padding around the region
            pad = 20
            self.setGeometry(max(0, x - pad), max(0, y - pad), w + pad * 2, h + pad * 2)
            self._offset = (pad, pad)
        else:
            # Fallback to cursor position
            c_pos = QCursor.pos()
            self.setGeometry(max(0, c_pos.x() - 300), max(0, c_pos.y() - 200), 650, 450)
            self._offset = (20, 50)

        # Subtle dark translucent backdrop
        self.setStyleSheet("""
            QWidget#overlayBackdrop {
                background-color: rgba(10, 10, 14, 0.45);
            }
        """)

    def _populate_bubbles(self) -> None:
        ox, oy = self._offset

        # 1. Floating top banner
        banner = QFrame(self)
        banner.setObjectName("overlayBanner")
        banner.setStyleSheet("""
            QFrame#overlayBanner {
                background-color: rgba(15, 15, 20, 0.95);
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 10px;
            }
        """)
        banner_layout = QHBoxLayout(banner)
        banner_layout.setContentsMargins(12, 6, 12, 6)
        banner_layout.setSpacing(10)

        lbl_icon = QLabel("🎮 Comic / Game OCR Mode", banner)
        lbl_icon.setStyleSheet("color: #60a5fa; font-weight: 600; font-size: 12px; background: transparent;")

        lbl_hint = QLabel("• Click any bubble to copy • Press Esc to close", banner)
        lbl_hint.setStyleSheet("color: #a1a1aa; font-size: 11px; background: transparent;")

        btn_close = QPushButton("✕", banner)
        btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_close.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #d4d4d8;
                font-weight: bold;
                padding: 2px 6px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.1);
                color: #ffffff;
            }
        """)
        btn_close.clicked.connect(self.close)

        banner_layout.addWidget(lbl_icon)
        banner_layout.addWidget(lbl_hint)
        banner_layout.addStretch()
        banner_layout.addWidget(btn_close)

        banner.setGeometry(15, 10, min(500, self.width() - 30), 36)

        # 2. Place translation bubble cards over each detected block
        for block in self._blocks:
            orig = block.get("text", "").strip()
            trans = block.get("translated", "").strip()
            if not orig or not trans:
                continue

            bx, by, bw, bh = block.get("box", (0, 0, 120, 30))
            # Calculate geometry with offset
            card_x = ox + bx
            card_y = oy + by + 40  # Account for banner clearance if overlapping
            card_w = max(140, min(bw + 24, 380))
            card_h = max(44, bh + 16)

            card = TranslatedBubbleCard(
                original_text=orig,
                translated_text=trans,
                box=(bx, by, bw, bh),
                parent=self,
            )
            card.setGeometry(card_x, card_y, card_w, card_h)
            card.show()
            self._cards.append(card)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        # Clicking outside cards dismisses overlay
        if event.button() == Qt.MouseButton.LeftButton:
            # Check if clicked on a child card
            pos = event.pos()
            for card in self._cards:
                if card.geometry().contains(pos):
                    return
            self.close()
            event.accept()
        else:
            super().mousePressEvent(event)
