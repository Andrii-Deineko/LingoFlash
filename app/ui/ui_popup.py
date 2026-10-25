"""Quick Translation Popup widget for LingoFlash."""

from __future__ import annotations

import logging
from typing import Optional, Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QPoint
from PyQt6.QtGui import QCursor, QGuiApplication, QKeyEvent, QMouseEvent, QColor
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QTextEdit,
    QPushButton,
    QFrame,
    QGraphicsDropShadowEffect,
)

from app.clipboard.clipboard_manager import ClipboardManager
from app.ui.ui_main import LANGUAGES, SearchableComboBox
from app.utils.i18n import tr
from app.utils.theme import get_app_icon

logger = logging.getLogger(__name__)

LANGUAGE_NAME_MAP: dict[str, str] = {code: name for code, name in LANGUAGES}


class TranslationPopup(QWidget):
    """Floating, frameless translation widget displayed near cursor."""

    open_main_requested = pyqtSignal(str)
    retranslate_requested = pyqtSignal(str, str, str)  # (text, source_lang, target_lang)
    cancel_worker_requested = pyqtSignal()
    closed = pyqtSignal()
    copy_to_clipboard_requested = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None, font_size: int = 14) -> None:
        super().__init__(parent)
        self._clipboard = ClipboardManager()
        self._drag_position = QPoint()
        self._source_text = ""
        self._translated_text = ""
        self._font_size = font_size
        self._linked_worker: Optional[Any] = None

        # Debounce timer for interactive live typing
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._trigger_retranslation)

        self._setup_window()
        self._setup_ui()
        self._setup_animation_timer()
        self.apply_font_size(self._font_size)

    def set_active_worker(self, worker: Any) -> None:
        """Track active background worker associated with this popup."""
        self._linked_worker = worker

    def dismiss(self) -> None:
        """Safely dismiss and hide the popup without destroying it.

        Cancels any active TranslationWorker, stops debounce/loading timers,
        and hides the popup without triggering process termination or widget destruction.
        """
        self._debounce_timer.stop()
        self._loading_timer.stop()
        self._lbl_loading.hide()

        if self._linked_worker is not None:
            try:
                self._linked_worker.cancel()
            except RuntimeError:
                pass
            self._linked_worker = None

        self.cancel_worker_requested.emit()
        self.closed.emit()
        self.hide()

    def _setup_window(self) -> None:
        """Configure frameless and top-level window flags."""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        icon = get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        self.setFixedWidth(440)

    def _setup_ui(self) -> None:
        """Create popup layout and widgets."""
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        # Outer frame for rounded styling and drop shadow
        self._frame = QFrame(self)
        self._frame.setObjectName("popupFrame")
        self._frame.setFrameShape(QFrame.Shape.NoFrame)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(28)
        shadow.setColor(QColor(0, 0, 0, 100))
        shadow.setOffset(0, 8)
        self._frame.setGraphicsEffect(shadow)

        frame_layout = QVBoxLayout(self._frame)
        frame_layout.setContentsMargins(16, 14, 16, 16)
        frame_layout.setSpacing(10)

        # 1. Header (drag area + title + close button)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        self._lbl_logo = QLabel(self._frame)
        icon = get_app_icon()
        if icon is not None and not icon.isNull():
            self._lbl_logo.setPixmap(icon.pixmap(18, 18))

        self._lbl_title = QLabel("LingoFlash Quick Translation", self._frame)
        self._lbl_title.setObjectName("popupTitle")

        self._btn_close = QPushButton("✕", self._frame)
        self._btn_close.setObjectName("closeBtn")
        self._btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_close.clicked.connect(self.dismiss)

        header_layout.addWidget(self._lbl_logo)
        header_layout.addWidget(self._lbl_title)
        header_layout.addStretch()
        header_layout.addWidget(self._btn_close)
        frame_layout.addLayout(header_layout)

        # 2. Source section with searchable "From:" selector and detected badge
        source_header = QHBoxLayout()
        source_header.setContentsMargins(0, 0, 0, 0)
        source_header.setSpacing(8)

        self._lbl_source_lang = QLabel("From:", self._frame)
        self._lbl_source_lang.setObjectName("popupSubLabel")

        self._cmb_source_lang = SearchableComboBox(self._frame)
        self._cmb_source_lang.setObjectName("popupLangCombo")
        self._cmb_source_lang.setMinimumWidth(140)
        for code, name in LANGUAGES:
            self._cmb_source_lang.addItem(name, code)
        self._cmb_source_lang.currentIndexChanged.connect(self._on_source_lang_changed)

        self._lbl_detected = QLabel("", self._frame)
        self._lbl_detected.setObjectName("detectedBadge")

        source_header.addWidget(self._lbl_source_lang)
        source_header.addWidget(self._cmb_source_lang)
        source_header.addWidget(self._lbl_detected)
        source_header.addStretch()
        frame_layout.addLayout(source_header)

        # Editable source text area
        self._txt_source = QTextEdit(self._frame)
        self._txt_source.setReadOnly(False)
        self._txt_source.setPlaceholderText("Enter or paste text to translate…")
        self._txt_source.setFrameShape(QFrame.Shape.NoFrame)
        self._txt_source.setMinimumHeight(65)
        self._txt_source.setMaximumHeight(115)
        self._txt_source.textChanged.connect(self._on_source_text_changed)
        frame_layout.addWidget(self._txt_source)

        # 3. Loading indicator
        self._lbl_loading = QLabel("Translating…", self._frame)
        self._lbl_loading.setObjectName("loadingLabel")
        self._lbl_loading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        frame_layout.addWidget(self._lbl_loading)

        # 4. Result section with searchable "To:" selector
        target_header = QHBoxLayout()
        target_header.setContentsMargins(0, 0, 0, 0)
        target_header.setSpacing(8)

        self._lbl_target_lang = QLabel("To:", self._frame)
        self._lbl_target_lang.setObjectName("popupSubLabel")

        self._cmb_target_lang = SearchableComboBox(self._frame)
        self._cmb_target_lang.setObjectName("popupLangCombo")
        self._cmb_target_lang.setMinimumWidth(140)
        for code, name in LANGUAGES:
            if code != "auto":
                self._cmb_target_lang.addItem(name, code)
        self._cmb_target_lang.currentIndexChanged.connect(self._on_target_lang_changed)

        target_header.addWidget(self._lbl_target_lang)
        target_header.addWidget(self._cmb_target_lang)
        target_header.addStretch()
        frame_layout.addLayout(target_header)

        self._txt_result = QTextEdit(self._frame)
        self._txt_result.setReadOnly(True)
        self._txt_result.setPlaceholderText("Translation will appear here…")
        self._txt_result.setFrameShape(QFrame.Shape.NoFrame)
        self._txt_result.setMinimumHeight(80)
        self._txt_result.setMaximumHeight(150)
        frame_layout.addWidget(self._txt_result)

        # 5. Action Buttons (perfectly aligned ghost & primary buttons)
        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 4, 0, 0)
        btn_layout.setSpacing(10)
        btn_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)

        self._btn_copy = QPushButton("📋 Copy", self._frame)
        self._btn_copy.setObjectName("popupCopyBtn")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.clicked.connect(self._copy_translation)

        self._btn_open_main = QPushButton("↗ Open in App", self._frame)
        self._btn_open_main.setObjectName("popupPrimaryBtn")
        self._btn_open_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_open_main.clicked.connect(self._on_open_main_clicked)

        btn_layout.addWidget(self._btn_copy, 0, Qt.AlignmentFlag.AlignVCenter)
        btn_layout.addStretch()
        btn_layout.addWidget(self._btn_open_main, 0, Qt.AlignmentFlag.AlignVCenter)
        frame_layout.addLayout(btn_layout)

        root_layout.addWidget(self._frame)

    def _setup_animation_timer(self) -> None:
        """Timer to animate the loading dots."""
        self._dots_count = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(400)
        self._loading_timer.timeout.connect(self._tick_loading)

    def _tick_loading(self) -> None:
        self._dots_count = (self._dots_count + 1) % 4
        self._lbl_loading.setText("Translating" + "." * self._dots_count)

    def show_with_text(
        self,
        source_text: str,
        source_lang: str = "auto",
        target_lang: str = "uk",
    ) -> None:
        """Display the popup near cursor and initialize with source text."""
        self._debounce_timer.stop()
        self._source_text = source_text
        self._translated_text = ""
        self._lbl_detected.setText("")

        self._txt_source.blockSignals(True)
        self._txt_source.setPlainText(source_text)
        self._txt_source.blockSignals(False)

        self._txt_result.clear()

        # Update source combobox
        self._cmb_source_lang.blockSignals(True)
        idx_src = self._cmb_source_lang.findData(source_lang)
        if idx_src >= 0:
            self._cmb_source_lang.setCurrentIndex(idx_src)
            self._cmb_source_lang.setEditText(self._cmb_source_lang.itemText(idx_src))
        self._cmb_source_lang.blockSignals(False)

        # Update target combobox
        self._cmb_target_lang.blockSignals(True)
        idx_tgt = self._cmb_target_lang.findData(target_lang)
        if idx_tgt >= 0:
            self._cmb_target_lang.setCurrentIndex(idx_tgt)
            self._cmb_target_lang.setEditText(self._cmb_target_lang.itemText(idx_tgt))
        self._cmb_target_lang.blockSignals(False)

        # Show loading indicator
        self._lbl_loading.show()
        self._loading_timer.start()

        self._position_near_cursor()
        self.show()
        self.raise_()
        self.activateWindow()

    def _on_source_text_changed(self) -> None:
        """Handle user typing in popup source text edit."""
        new_text = self._txt_source.toPlainText()
        if new_text == self._source_text:
            return
        self._debounce_timer.start()

    def _on_source_lang_changed(self) -> None:
        """Handle user changing source language inside the quick popup."""
        src = self._cmb_source_lang.currentData()
        if src != "auto":
            self._lbl_detected.setText("")
        self._trigger_retranslation()

    def _on_target_lang_changed(self) -> None:
        """Handle user changing target language inside the quick popup."""
        self._trigger_retranslation()

    def _trigger_retranslation(self) -> None:
        """Debounced trigger for retranslating after user edits text or language."""
        text = self._txt_source.toPlainText().strip()
        if not text:
            self._debounce_timer.stop()
            self._source_text = ""
            self._translated_text = ""
            self._txt_result.clear()
            self._lbl_loading.hide()
            self._loading_timer.stop()
            self._lbl_detected.setText("")
            return

        self._source_text = text
        source_code = self._cmb_source_lang.currentData() or "auto"
        target_code = self._cmb_target_lang.currentData() or "en"

        self._lbl_loading.show()
        self._loading_timer.start()
        self.retranslate_requested.emit(text, source_code, target_code)

    def set_error(self, error_message: str) -> None:
        """Display an error message directly inside target text area and stop loading state."""
        self._loading_timer.stop()
        self._lbl_loading.hide()
        self._translated_text = error_message
        self._txt_result.setPlainText(error_message)
        self.adjustSize()

    def set_translation(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str = "",
        target_lang: str = "",
    ) -> None:
        """Update popup contents once background translation finishes."""
        self._loading_timer.stop()
        self._lbl_loading.hide()

        if translated_text.startswith("⚠️") or translated_text.startswith("Error:"):
            self.set_error(translated_text)
            return

        # Update source text only if not currently matching and not actively being typed
        if source_text and self._txt_source.toPlainText() != source_text:
            self._source_text = source_text
            self._txt_source.blockSignals(True)
            self._txt_source.setPlainText(source_text)
            self._txt_source.blockSignals(False)

        self._translated_text = translated_text
        self._txt_result.setPlainText(translated_text)

        # Dynamic Auto Detect feedback
        current_src_code = self._cmb_source_lang.currentData()
        if source_lang and source_lang != "auto":
            lang_name = LANGUAGE_NAME_MAP.get(source_lang, source_lang.upper())
            if current_src_code == "auto":
                self._lbl_detected.setText(tr("detected_badge", lang=lang_name))
            else:
                self._lbl_detected.setText("")
        else:
            self._lbl_detected.setText("")

        if target_lang:
            self._cmb_target_lang.blockSignals(True)
            idx = self._cmb_target_lang.findData(target_lang)
            if idx >= 0:
                self._cmb_target_lang.setCurrentIndex(idx)
                self._cmb_target_lang.setEditText(self._cmb_target_lang.itemText(idx))
            self._cmb_target_lang.blockSignals(False)

        self.adjustSize()

    def apply_locale(self, locale: str | None = None) -> None:
        """Update popup UI text to reflect active interface language."""
        self._lbl_title.setText(tr("quick_popup_title", locale))
        self._lbl_source_lang.setText(tr("from", locale))
        self._lbl_target_lang.setText(tr("to", locale))
        self._txt_source.setPlaceholderText(tr("source_placeholder", locale))
        self._txt_result.setPlaceholderText(tr("target_placeholder", locale))
        self._btn_copy.setText(f"📋 {tr('copy', locale)}")
        self._btn_open_main.setText(f"↗ {tr('open_main', locale)}")

        # Update Auto Detect item text in source combo
        self._cmb_source_lang.update_item_display_text("auto", tr("auto_detect", locale))

    def _position_near_cursor(self) -> None:
        """Position the popup close to the mouse cursor without leaving screen."""
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos) or QGuiApplication.primaryScreen()
        if not screen:
            self.move(cursor_pos)
            return

        geo = screen.availableGeometry()
        x = cursor_pos.x() + 15
        y = cursor_pos.y() + 15

        popup_width = self.width()
        popup_height = 340

        if x + popup_width > geo.right():
            x = cursor_pos.x() - popup_width - 10
        if y + popup_height > geo.bottom():
            y = cursor_pos.y() - popup_height - 10

        x = max(geo.left() + 10, x)
        y = max(geo.top() + 10, y)

        self.move(x, y)

    def _copy_translation(self) -> None:
        """Copy current translated text to clipboard."""
        if self._translated_text:
            self.copy_to_clipboard_requested.emit(self._translated_text)
            prev_text = self._btn_copy.text()
            self._btn_copy.setText(tr("copied"))
            QTimer.singleShot(1500, lambda: self._btn_copy.setText(prev_text))

    def apply_font_size(self, font_size: int) -> None:
        """Dynamically update font size for source and result text areas."""
        self._font_size = max(10, min(36, int(font_size)))
        font = self._txt_source.font()
        font.setPointSize(self._font_size)
        self._txt_source.setFont(font)
        self._txt_result.setFont(font)

    def _on_open_main_clicked(self) -> None:
        """Emit open_main_requested and dismiss popup safely."""
        text_to_send = self._source_text or self._translated_text
        self.open_main_requested.emit(text_to_send)
        self.dismiss()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        """Dismiss on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Enable dragging the popup by clicking anywhere on header."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Move popup when dragging."""
        if event.buttons() == Qt.MouseButton.LeftButton and not self._drag_position.isNull():
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        """Reset drag position on mouse release."""
        self._drag_position = QPoint()
        super().mouseReleaseEvent(event)

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        """Ensure closing safely hides the popup and never destroys the widget."""
        self.dismiss()
        event.ignore()

    def hideEvent(self, event) -> None:  # noqa: ANN001, N802
        """Cancel background operations when hidden."""
        self._debounce_timer.stop()
        self._loading_timer.stop()
        if self._linked_worker is not None:
            try:
                self._linked_worker.cancel()
            except RuntimeError:
                pass
            self._linked_worker = None
        super().hideEvent(event)
