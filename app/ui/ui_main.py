"""Main Application Window for LingoFlash."""

from __future__ import annotations

import logging
from typing import Optional, Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QEvent, QSize
from PyQt6.QtGui import (
    QIcon,
    QCloseEvent,
    QTextCursor,
    QKeySequence,
    QShortcut,
    QResizeEvent,
    QShowEvent,
)
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QSizePolicy,
    QLabel,
    QTextEdit,
    QPushButton,
    QComboBox,
    QCompleter,
    QListWidget,
    QListWidgetItem,
    QStatusBar,
    QDialog,
    QLineEdit,
    QFormLayout,
    QDialogButtonBox,
    QMessageBox,
    QFrame,
    QTabWidget,
    QKeySequenceEdit,
    QApplication,
    QSpinBox,
    QStyle,
)

from app.clipboard.clipboard_manager import ClipboardManager
from app.audio.voice_input import VoiceInputWorker
from app.audio.tts_engine import TTSManager
from app.utils.shortcuts import DEFAULT_SHORTCUTS, apply_gnome_shortcuts
from app.utils.theme import get_available_themes, get_stylesheet, create_svg_icon, get_theme_palette, get_app_icon
from app.utils.i18n import SUPPORTED_LOCALES, tr, set_current_locale, get_current_locale
from app.translation.translator import (
    TranslationManager,
    TranslationHistory,
    detect_language,
)

logger = logging.getLogger(__name__)

# Comprehensive language list (code, display name)
LANGUAGES = [
    ("auto", "Auto Detect"),
    ("af", "Afrikaans"),
    ("sq", "Albanian"),
    ("am", "Amharic"),
    ("ar", "Arabic"),
    ("hy", "Armenian"),
    ("az", "Azerbaijani"),
    ("eu", "Basque"),
    ("be", "Belarusian"),
    ("bn", "Bengali"),
    ("bs", "Bosnian"),
    ("bg", "Bulgarian"),
    ("ca", "Catalan"),
    ("zh-CN", "Chinese (Simplified)"),
    ("zh-TW", "Chinese (Traditional)"),
    ("hr", "Croatian"),
    ("cs", "Czech"),
    ("da", "Danish"),
    ("nl", "Dutch"),
    ("en", "English"),
    ("eo", "Esperanto"),
    ("et", "Estonian"),
    ("fi", "Finnish"),
    ("fr", "French"),
    ("gl", "Galician"),
    ("ka", "Georgian"),
    ("de", "German"),
    ("el", "Greek"),
    ("gu", "Gujarati"),
    ("ht", "Haitian Creole"),
    ("he", "Hebrew"),
    ("hi", "Hindi"),
    ("hu", "Hungarian"),
    ("is", "Icelandic"),
    ("id", "Indonesian"),
    ("ga", "Irish"),
    ("it", "Italian"),
    ("ja", "Japanese"),
    ("kn", "Kannada"),
    ("kk", "Kazakh"),
    ("ko", "Korean"),
    ("la", "Latin"),
    ("lv", "Latvian"),
    ("lt", "Lithuanian"),
    ("mk", "Macedonian"),
    ("ms", "Malay"),
    ("ml", "Malayalam"),
    ("mr", "Marathi"),
    ("mn", "Mongolian"),
    ("no", "Norwegian"),
    ("fa", "Persian"),
    ("pl", "Polish"),
    ("pt", "Portuguese"),
    ("pa", "Punjabi"),
    ("ro", "Romanian"),
    ("ru", "Russian"),
    ("sr", "Serbian"),
    ("sk", "Slovak"),
    ("sl", "Slovenian"),
    ("es", "Spanish"),
    ("sw", "Swahili"),
    ("sv", "Swedish"),
    ("tl", "Tagalog"),
    ("ta", "Tamil"),
    ("te", "Telugu"),
    ("th", "Thai"),
    ("tr", "Turkish"),
    ("uk", "Ukrainian"),
    ("ur", "Urdu"),
    ("vi", "Vietnamese"),
    ("cy", "Welsh"),
    ("yi", "Yiddish"),
]

LANGUAGE_NAME_MAP: dict[str, str] = {code: name for code, name in LANGUAGES}


class SearchableComboBox(QComboBox):
    """Editable dropdown with live substring filter, forgiving QCompleter, and validation.

    Strictly separates:
    - DisplayRole: The localized human-readable language string (e.g., 'Auto Detect', 'English').
    - UserRole: The internal language code (e.g., 'auto', 'en', 'sv').
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMinimumWidth(160)

        # Configure QCompleter for forgiving substring matching
        comp = self.completer()
        if comp is not None:
            comp.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
            comp.setFilterMode(Qt.MatchFlag.MatchContains)
            comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
            comp.activated[str].connect(self._on_completer_activated)

        self._last_valid_index = 0
        self.currentIndexChanged.connect(self._on_index_changed)

        line_edit = self.lineEdit()
        if line_edit is not None:
            line_edit.setFrame(False)
            line_edit.installEventFilter(self)
            line_edit.editingFinished.connect(self._validate_and_reconcile)
            line_edit.returnPressed.connect(self._validate_and_reconcile)

    def add_language_item(self, display_name: str, code: str) -> None:
        """Add a language item ensuring DisplayRole and UserRole are distinct."""
        idx = self.count()
        self.addItem(display_name, code)
        self.setItemData(idx, display_name, Qt.ItemDataRole.DisplayRole)
        self.setItemData(idx, code, Qt.ItemDataRole.UserRole)

    def get_selected_code(self) -> str:
        """Strictly retrieve the internal standard language code (UserRole), never the display text."""
        # 1. Check if line edit text differs from currently selected item text (user typed custom search)
        line_edit = self.lineEdit()
        if line_edit is not None:
            text = line_edit.text().strip().lower()
            current_disp = self.itemText(self.currentIndex()).strip().lower() if self.currentIndex() >= 0 else ""
            if text and text != current_disp:
                # User typed something: check UserRole code match first
                for i in range(self.count()):
                    code_i = str(self.itemData(i, Qt.ItemDataRole.UserRole) or "").lower()
                    if code_i == text:
                        return code_i
                # Check DisplayRole match
                for i in range(self.count()):
                    disp_i = str(self.itemText(i) or "").strip().lower()
                    if disp_i == text or (text in disp_i and len(text) > 2):
                        return str(self.itemData(i, Qt.ItemDataRole.UserRole) or "auto")

        # 2. Direct UserRole data from current index
        idx = self.currentIndex()
        if idx >= 0:
            val = self.itemData(idx, Qt.ItemDataRole.UserRole)
            if val:
                return str(val)

        data = super().currentData(Qt.ItemDataRole.UserRole)
        if data:
            return str(data)

        # Fallback to index 0 (typically 'auto' or default)
        if self.count() > 0:
            return str(self.itemData(0, Qt.ItemDataRole.UserRole) or "auto")
        return "auto"

    def set_selected_code(self, code: str) -> bool:
        """Select an item strictly by its internal language code (UserRole)."""
        idx = self.findData(code, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self.setCurrentIndex(idx)
            self.setEditText(self.itemText(idx))
            self._last_valid_index = idx
            return True
        return False

    def update_item_display_text(self, code: str, new_display_text: str) -> None:
        """Update DisplayRole text without changing the underlying UserRole code."""
        idx = self.findData(code, Qt.ItemDataRole.UserRole)
        if idx >= 0:
            self.setItemData(idx, new_display_text, Qt.ItemDataRole.DisplayRole)
            self.setItemText(idx, new_display_text)
            if self.currentIndex() == idx:
                self.setEditText(new_display_text)

    def currentData(self, role: int = Qt.ItemDataRole.UserRole) -> Any:  # noqa: N802
        """Override currentData to guarantee standard code is returned for UserRole."""
        val = super().currentData(role)
        if val is not None and val != "":
            return val
        if role == Qt.ItemDataRole.UserRole:
            return self.get_selected_code()
        return val

    def eventFilter(self, watched, event) -> bool:  # noqa: ANN001
        """Auto-select all text on click for instant search replacement."""
        if watched == self.lineEdit() and event.type() == QEvent.Type.MouseButtonPress:
            QTimer.singleShot(0, self.lineEdit().selectAll)
        return super().eventFilter(watched, event)

    def focusInEvent(self, event) -> None:  # noqa: ANN001
        super().focusInEvent(event)
        if self.lineEdit() is not None:
            QTimer.singleShot(0, self.lineEdit().selectAll)

    def _on_index_changed(self, index: int) -> None:
        if index >= 0:
            self._last_valid_index = index

    def _on_completer_activated(self, text: str) -> None:
        idx = self.findText(text, Qt.MatchFlag.MatchExactly)
        if idx >= 0:
            self.setCurrentIndex(idx)
            self.setEditText(self.itemText(idx))

    def _validate_and_reconcile(self) -> None:
        """Validate input text against supported languages. Revert to last valid selection if invalid."""
        text = self.currentText().strip()
        if not text:
            self.setCurrentIndex(self._last_valid_index)
            self.setEditText(self.itemText(self._last_valid_index))
            return

        # 1. Check if user typed an exact internal code (UserRole)
        for i in range(self.count()):
            if str(self.itemData(i, Qt.ItemDataRole.UserRole)).lower() == text.lower():
                self.setCurrentIndex(i)
                self.setEditText(self.itemText(i))
                return

        # 2. Exact match on DisplayRole
        idx = self.findText(text, Qt.MatchFlag.MatchExactly)
        # 3. Case-insensitive prefix match
        if idx < 0:
            idx = self.findText(text, Qt.MatchFlag.MatchStartsWith)
        # 4. Substring match
        if idx < 0:
            idx = self.findText(text, Qt.MatchFlag.MatchContains)

        if idx >= 0:
            self.setCurrentIndex(idx)
            self.setEditText(self.itemText(idx))
        else:
            # Revert to last valid selection
            self.setCurrentIndex(self._last_valid_index)
            self.setEditText(self.itemText(self._last_valid_index))


class SettingsDialog(QDialog):
    """Dialog for configuring translation backends, target languages, themes, font sizes, interface localization, and hotkeys."""

    font_size_changed = pyqtSignal(int)
    locale_changed = pyqtSignal(str)
    quit_requested = pyqtSignal()

    def __init__(
        self,
        manager: TranslationManager,
        parent: Optional[QWidget] = None,
        on_quit: Optional[Callable[[], None]] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._on_quit = on_quit
        self._initial_theme = self._manager.get_theme()
        self._initial_ui_font_size = self._manager.get_ui_font_size()
        self._initial_text_font_size = self._manager.get_text_font_size()
        self._initial_ui_lang = self._manager.get_interface_language()
        self.setWindowTitle(tr("settings_title", self._initial_ui_lang))
        icon = get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        self.setMinimumWidth(520)
        self._setup_ui()
        self.apply_locale(self._initial_ui_lang)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(20, 20, 20, 20)

        # Tabs container
        self._tabs = QTabWidget(self)

        # === Tab 1: Translation Settings ===
        tab_translation = QWidget(self)
        self._form_trans = QFormLayout(tab_translation)
        self._form_trans.setSpacing(12)
        self._form_trans.setContentsMargins(12, 16, 12, 16)

        # 0. Interface Language selector
        self._lbl_ui_lang = QLabel(tr("interface_language"), tab_translation)
        self._cmb_ui_lang = QComboBox(tab_translation)
        for loc_code, loc_name in SUPPORTED_LOCALES.items():
            self._cmb_ui_lang.addItem(loc_name, loc_code)
        idx_loc = self._cmb_ui_lang.findData(self._initial_ui_lang)
        if idx_loc >= 0:
            self._cmb_ui_lang.setCurrentIndex(idx_loc)
        self._cmb_ui_lang.currentIndexChanged.connect(self._on_locale_selected)
        self._form_trans.addRow(self._lbl_ui_lang, self._cmb_ui_lang)

        # Backend selector
        self._lbl_backend = QLabel(tr("active_backend"), tab_translation)
        self._cmb_backend = QComboBox(tab_translation)
        backend_labels = {
            "google": "Google Translate",
            "deepl": "DeepL",
            "libretranslate": "LibreTranslate",
            "nvidia_nim": "NVIDIA NIM (LLM)",
        }
        for backend_id in self._manager.get_backends():
            label = backend_labels.get(backend_id, backend_id.capitalize())
            self._cmb_backend.addItem(label, backend_id)

        # Current backend selection
        current_backend = self._manager._config.get("backend", "google")
        idx = self._cmb_backend.findData(current_backend)
        if idx >= 0:
            self._cmb_backend.setCurrentIndex(idx)
        self._cmb_backend.currentIndexChanged.connect(self._update_backend_visibility)
        self._form_trans.addRow(self._lbl_backend, self._cmb_backend)

        # Backend config dict
        backends_cfg = self._manager._config.get("backends", {})

        # NVIDIA NIM configuration fields
        nim_cfg = backends_cfg.get("nvidia_nim", {})
        self._lbl_nvidia_key = QLabel(tr("nvidia_key"), tab_translation)
        self._edt_nvidia_key = QLineEdit(tab_translation)
        self._edt_nvidia_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._edt_nvidia_key.setText(nim_cfg.get("api_key", ""))
        self._edt_nvidia_key.setPlaceholderText("nvapi-...")

        self._lbl_nvidia_url = QLabel(tr("nvidia_url"), tab_translation)
        self._edt_nvidia_url = QLineEdit(tab_translation)
        self._edt_nvidia_url.setText(nim_cfg.get("base_url", "https://integrate.api.nvidia.com/v1"))
        self._edt_nvidia_url.setPlaceholderText("https://integrate.api.nvidia.com/v1")

        self._lbl_nvidia_model = QLabel(tr("nvidia_model"), tab_translation)
        self._edt_nvidia_model = QLineEdit(tab_translation)
        self._edt_nvidia_model.setText(nim_cfg.get("model", "meta/llama-3.1-8b-instruct"))
        self._edt_nvidia_model.setPlaceholderText("meta/llama-3.1-8b-instruct")

        self._form_trans.addRow(self._lbl_nvidia_key, self._edt_nvidia_key)
        self._form_trans.addRow(self._lbl_nvidia_url, self._edt_nvidia_url)
        self._form_trans.addRow(self._lbl_nvidia_model, self._edt_nvidia_model)

        # DeepL API Key
        deepl_cfg = backends_cfg.get("deepl", {})
        self._lbl_deepl_key = QLabel(tr("deepl_key"), tab_translation)
        self._edt_deepl_key = QLineEdit(tab_translation)
        self._edt_deepl_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._edt_deepl_key.setText(deepl_cfg.get("api_key", ""))
        self._edt_deepl_key.setPlaceholderText("Enter DeepL Auth Key")
        self._form_trans.addRow(self._lbl_deepl_key, self._edt_deepl_key)

        # LibreTranslate URL
        libre_cfg = backends_cfg.get("libretranslate", {})
        self._lbl_libre_url = QLabel(tr("libre_url"), tab_translation)
        self._edt_libre_url = QLineEdit(tab_translation)
        self._edt_libre_url.setText(libre_cfg.get("base_url", "https://libretranslate.com"))
        self._edt_libre_url.setPlaceholderText("https://libretranslate.com")
        self._form_trans.addRow(self._lbl_libre_url, self._edt_libre_url)

        # Default Target Language
        self._lbl_default_target = QLabel(tr("default_target"), tab_translation)
        self._cmb_default_target = QComboBox(tab_translation)
        for code, name in LANGUAGES:
            if code != "auto":
                self._cmb_default_target.addItem(name, code)
        idx_tgt = self._cmb_default_target.findData(self._manager.get_target_language())
        if idx_tgt >= 0:
            self._cmb_default_target.setCurrentIndex(idx_tgt)
        self._form_trans.addRow(self._lbl_default_target, self._cmb_default_target)

        self._tabs.addTab(tab_translation, tr("tab_translation"))
        self._update_backend_visibility()

        # === Tab 2: Appearance & Theming Settings ===
        tab_appearance = QWidget(self)
        vbox_appearance = QVBoxLayout(tab_appearance)
        vbox_appearance.setSpacing(14)
        vbox_appearance.setContentsMargins(12, 16, 12, 16)

        self._lbl_app_desc = QLabel(
            tr("appearance_desc"),
            tab_appearance,
        )
        self._lbl_app_desc.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        vbox_appearance.addWidget(self._lbl_app_desc)

        form_appearance = QFormLayout()
        form_appearance.setSpacing(12)

        # Theme selector
        self._lbl_theme = QLabel(tr("theme"), tab_appearance)
        self._cmb_theme = QComboBox(tab_appearance)
        for theme_id, display_name in get_available_themes():
            self._cmb_theme.addItem(display_name, theme_id)

        idx_theme = self._cmb_theme.findData(self._initial_theme)
        if idx_theme >= 0:
            self._cmb_theme.setCurrentIndex(idx_theme)
        self._cmb_theme.currentIndexChanged.connect(self._on_theme_preview)
        form_appearance.addRow(self._lbl_theme, self._cmb_theme)

        # 1. UI Font Size setting (QSpinBox, 10 to 36)
        self._lbl_ui_font = QLabel(tr("ui_font_size"), tab_appearance)
        self._spn_ui_font = QSpinBox(tab_appearance)
        self._spn_ui_font.setRange(10, 36)
        self._spn_ui_font.setSuffix(" pt")
        self._spn_ui_font.setValue(self._initial_ui_font_size)
        self._spn_ui_font.valueChanged.connect(self._on_font_size_preview)
        form_appearance.addRow(self._lbl_ui_font, self._spn_ui_font)

        # 2. Text Font Size setting (QSpinBox, 10 to 36)
        self._lbl_text_font = QLabel(tr("text_font_size"), tab_appearance)
        self._spn_text_font = QSpinBox(tab_appearance)
        self._spn_text_font.setRange(10, 36)
        self._spn_text_font.setSuffix(" pt")
        self._spn_text_font.setValue(self._initial_text_font_size)
        self._spn_text_font.valueChanged.connect(self._on_font_size_preview)
        form_appearance.addRow(self._lbl_text_font, self._spn_text_font)

        vbox_appearance.addLayout(form_appearance)

        lbl_theme_info = QLabel(
            "🎨 Includes 11 handcrafted themes: System Auto, Midnight Charcoal (Dark), Clean Modern (Light), "
            "Nord, Dracula, AMOLED Black, Tokyo Night, Catppuccin, Gruvbox, Solarized Dark/Light, and High Contrast.",
            tab_appearance,
        )
        lbl_theme_info.setStyleSheet("color: #71717a; font-size: 11px;")
        lbl_theme_info.setWordWrap(True)
        vbox_appearance.addWidget(lbl_theme_info)
        vbox_appearance.addStretch()

        self._tabs.addTab(tab_appearance, tr("tab_appearance"))

        # === Tab 3: Hotkeys Customization ===
        tab_hotkeys = QWidget(self)
        vbox_hotkeys = QVBoxLayout(tab_hotkeys)
        vbox_hotkeys.setSpacing(14)
        vbox_hotkeys.setContentsMargins(12, 16, 12, 16)

        self._lbl_hk_desc = QLabel(
            tr("hotkeys_desc"),
            tab_hotkeys,
        )
        self._lbl_hk_desc.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        vbox_hotkeys.addWidget(self._lbl_hk_desc)

        form_hotkeys = QFormLayout()
        form_hotkeys.setSpacing(12)

        current_shortcuts = self._manager.get_shortcuts()

        # 1. Translate Selection (Global)
        self._lbl_hk_translate = QLabel(tr("hk_translate"), tab_hotkeys)
        self._kse_translate = QKeySequenceEdit(
            QKeySequence(current_shortcuts.get("translate_selection", DEFAULT_SHORTCUTS["translate_selection"])),
            tab_hotkeys,
        )
        self._kse_translate.setClearButtonEnabled(True)
        form_hotkeys.addRow(self._lbl_hk_translate, self._kse_translate)

        # 2. Screen OCR Capture (Global)
        self._lbl_hk_ocr = QLabel(tr("hk_ocr"), tab_hotkeys)
        self._kse_ocr = QKeySequenceEdit(
            QKeySequence(current_shortcuts.get("ocr_capture", DEFAULT_SHORTCUTS["ocr_capture"])),
            tab_hotkeys,
        )
        self._kse_ocr.setClearButtonEnabled(True)
        form_hotkeys.addRow(self._lbl_hk_ocr, self._kse_ocr)

        # 3. Comic / Game OCR Overlay (Global)
        self._lbl_hk_overlay = QLabel(tr("hk_overlay"), tab_hotkeys)
        self._kse_overlay = QKeySequenceEdit(
            QKeySequence(current_shortcuts.get("ocr_overlay", DEFAULT_SHORTCUTS["ocr_overlay"])),
            tab_hotkeys,
        )
        self._kse_overlay.setClearButtonEnabled(True)
        form_hotkeys.addRow(self._lbl_hk_overlay, self._kse_overlay)

        # 4. Open/Toggle Main Window (Global)
        self._lbl_hk_show = QLabel(tr("hk_show"), tab_hotkeys)
        self._kse_show = QKeySequenceEdit(
            QKeySequence(current_shortcuts.get("show_main", DEFAULT_SHORTCUTS["show_main"])),
            tab_hotkeys,
        )
        self._kse_show.setClearButtonEnabled(True)
        form_hotkeys.addRow(self._lbl_hk_show, self._kse_show)

        # 5. Swap Languages (Local)
        self._lbl_hk_swap = QLabel(tr("hk_swap"), tab_hotkeys)
        self._kse_swap = QKeySequenceEdit(
            QKeySequence(current_shortcuts.get("swap_languages", DEFAULT_SHORTCUTS["swap_languages"])),
            tab_hotkeys,
        )
        self._kse_swap.setClearButtonEnabled(True)
        form_hotkeys.addRow(self._lbl_hk_swap, self._kse_swap)

        vbox_hotkeys.addLayout(form_hotkeys)

        # Reset button row
        self._btn_reset_hk = QPushButton(tr("reset_defaults"), tab_hotkeys)
        self._btn_reset_hk.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_reset_hk.clicked.connect(self._reset_shortcuts_to_default)
        vbox_hotkeys.addWidget(self._btn_reset_hk, alignment=Qt.AlignmentFlag.AlignLeft)

        # Informational note
        lbl_info = QLabel(
            "ℹ️ Global shortcuts are automatically bound via GNOME / Wayland (gsettings) on Fedora when saved.",
            tab_hotkeys,
        )
        lbl_info.setStyleSheet("color: #71717a; font-size: 11px;")
        lbl_info.setWordWrap(True)
        vbox_hotkeys.addWidget(lbl_info)
        vbox_hotkeys.addStretch()

        self._tabs.addTab(tab_hotkeys, tr("tab_hotkeys"))
        layout.addWidget(self._tabs)

        # Bottom bar: Left has distinct red "Quit LingoFlash", right has Save / Cancel
        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(0, 4, 0, 0)

        self._btn_quit = QPushButton(tr("quit_app"), self)
        self._btn_quit.setObjectName("quitAppBtn")
        self._btn_quit.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_quit.setStyleSheet("""
            QPushButton#quitAppBtn {
                background-color: #dc2626;
                color: #ffffff;
                font-weight: bold;
                padding: 6px 16px;
                border-radius: 6px;
                border: none;
                outline: none;
                font-size: 12px;
            }
            QPushButton#quitAppBtn:hover {
                background-color: #b91c1c;
                color: #ffffff;
            }
            QPushButton#quitAppBtn:pressed {
                background-color: #991b1b;
                color: #ffffff;
            }
        """)
        self._btn_quit.clicked.connect(self._on_quit_clicked)
        bottom_bar.addWidget(self._btn_quit)
        bottom_bar.addStretch()

        # Bottom buttons with dynamic SVG icons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        self._btn_save = buttons.button(QDialogButtonBox.StandardButton.Save)
        self._btn_cancel = buttons.button(QDialogButtonBox.StandardButton.Cancel)

        if self._btn_save is not None:
            self._btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_save.setText(f" {tr('save')}")

        if self._btn_cancel is not None:
            self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
            self._btn_cancel.setText(f" {tr('cancel')}")

        self._update_button_icons(self._initial_theme)

        buttons.accepted.connect(self._save_settings)
        buttons.rejected.connect(self.reject)
        bottom_bar.addWidget(buttons)
        layout.addLayout(bottom_bar)

    def _update_backend_visibility(self) -> None:
        """Dynamically show/hide backend credentials depending on selected backend."""
        backend = self._cmb_backend.currentData()
        is_deepl = (backend == "deepl")
        is_libre = (backend == "libretranslate")
        is_nim = (backend == "nvidia_nim")

        self._lbl_deepl_key.setVisible(is_deepl)
        self._edt_deepl_key.setVisible(is_deepl)
        self._lbl_libre_url.setVisible(is_libre)
        self._edt_libre_url.setVisible(is_libre)
        self._lbl_nvidia_key.setVisible(is_nim)
        self._edt_nvidia_key.setVisible(is_nim)
        self._lbl_nvidia_url.setVisible(is_nim)
        self._edt_nvidia_url.setVisible(is_nim)
        self._lbl_nvidia_model.setVisible(is_nim)
        self._edt_nvidia_model.setVisible(is_nim)

    def _update_button_icons(self, theme_id: str) -> None:
        """Dynamically set SVG fill color to match the theme's primary text color."""
        palette = get_theme_palette(theme_id)
        color = palette.get("text_primary", "#FFFFFF")

        if self._btn_save is not None:
            icon_save = create_svg_icon("save", color=color, size=16)
            if not icon_save.isNull():
                self._btn_save.setIcon(icon_save)
                self._btn_save.setIconSize(QSize(16, 16))

        if self._btn_cancel is not None:
            icon_cancel = create_svg_icon("cancel", color=color, size=16)
            if not icon_cancel.isNull():
                self._btn_cancel.setIcon(icon_cancel)
                self._btn_cancel.setIconSize(QSize(16, 16))

    def _on_locale_selected(self) -> None:
        """Preview selected interface language dynamically."""
        locale = self._cmb_ui_lang.currentData()
        if locale:
            set_current_locale(locale)
            self.apply_locale(locale)
            self.locale_changed.emit(locale)

    def apply_locale(self, locale: str | None = None) -> None:
        """Apply active locale strings to all Settings dialog widgets."""
        loc = locale or get_current_locale()
        self.setWindowTitle(tr("settings_title", loc))
        self._tabs.setTabText(0, tr("tab_translation", loc))
        self._tabs.setTabText(1, tr("tab_appearance", loc))
        self._tabs.setTabText(2, tr("tab_hotkeys", loc))

        self._lbl_ui_lang.setText(tr("interface_language", loc))
        self._lbl_backend.setText(tr("active_backend", loc))
        self._lbl_nvidia_key.setText(tr("nvidia_key", loc))
        self._lbl_nvidia_url.setText(tr("nvidia_url", loc))
        self._lbl_nvidia_model.setText(tr("nvidia_model", loc))
        self._lbl_deepl_key.setText(tr("deepl_key", loc))
        self._lbl_libre_url.setText(tr("libre_url", loc))
        self._lbl_default_target.setText(tr("default_target", loc))

        self._lbl_app_desc.setText(tr("appearance_desc", loc))
        self._lbl_theme.setText(tr("theme", loc))
        self._lbl_ui_font.setText(tr("ui_font_size", loc))
        self._lbl_text_font.setText(tr("text_font_size", loc))

        self._lbl_hk_desc.setText(tr("hotkeys_desc", loc))
        self._lbl_hk_translate.setText(tr("hk_translate", loc))
        self._lbl_hk_ocr.setText(tr("hk_ocr", loc))
        self._lbl_hk_overlay.setText(tr("hk_overlay", loc))
        self._lbl_hk_show.setText(tr("hk_show", loc))
        self._lbl_hk_swap.setText(tr("hk_swap", loc))
        self._btn_reset_hk.setText(tr("reset_defaults", loc))

        self._btn_quit.setText(tr("quit_app", loc))
        if self._btn_save is not None:
            self._btn_save.setText(f" {tr('save', loc)}")
        if self._btn_cancel is not None:
            self._btn_cancel.setText(f" {tr('cancel', loc)}")

    def _on_quit_clicked(self) -> None:
        """Handle Quit LingoFlash click: clean IPC socket, cancel threads, and quit."""
        self.reject()
        self.quit_requested.emit()
        if self._on_quit is not None:
            self._on_quit()
        elif self.parent() and hasattr(self.parent(), "quit_app_requested"):
            self.parent().quit_app_requested.emit()
        else:
            # Fallback direct cleanup
            import os
            for p in ("/tmp/LingoFlash", "/tmp/LingoFlash.sock"):
                if os.path.exists(p):
                    try:
                        os.unlink(p)
                    except OSError:
                        pass
            QApplication.quit()

    def _on_font_size_preview(self, *args: object) -> None:
        """Preview UI and Text font sizes dynamically across open widgets."""
        theme_id = self._cmb_theme.currentData()
        ui_sz = self._spn_ui_font.value()
        txt_sz = self._spn_text_font.value()
        app_inst = QApplication.instance()
        if app_inst and theme_id:
            app_inst.setStyleSheet(
                get_stylesheet(theme_id, ui_font_size=ui_sz, text_font_size=txt_sz)
            )
        if self.parent() and hasattr(self.parent(), "apply_font_size"):
            self.parent().apply_font_size(txt_sz)
        self.font_size_changed.emit(txt_sz)

    def _on_theme_preview(self) -> None:
        """Preview selected theme live across all open windows."""
        theme_id = self._cmb_theme.currentData()
        ui_sz = self._spn_ui_font.value()
        txt_sz = self._spn_text_font.value()
        if theme_id:
            self._update_button_icons(theme_id)
            app_inst = QApplication.instance()
            if app_inst:
                app_inst.setStyleSheet(
                    get_stylesheet(theme_id, ui_font_size=ui_sz, text_font_size=txt_sz)
                )

    def reject(self) -> None:
        """Revert theme, font size, and locale preview if cancelled and close dialog."""
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.setStyleSheet(
                get_stylesheet(
                    self._initial_theme,
                    ui_font_size=self._initial_ui_font_size,
                    text_font_size=self._initial_text_font_size,
                )
            )
        if self.parent() and hasattr(self.parent(), "apply_font_size"):
            self.parent().apply_font_size(self._initial_text_font_size)
        self.font_size_changed.emit(self._initial_text_font_size)

        # Revert locale if changed during preview
        set_current_locale(self._initial_ui_lang)
        self.apply_locale(self._initial_ui_lang)
        self.locale_changed.emit(self._initial_ui_lang)
        super().reject()

    def _reset_shortcuts_to_default(self) -> None:
        """Reset the shortcut fields to application defaults."""
        self._kse_translate.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["translate_selection"]))
        self._kse_ocr.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["ocr_capture"]))
        self._kse_overlay.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["ocr_overlay"]))
        self._kse_show.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["show_main"]))
        self._kse_swap.setKeySequence(QKeySequence(DEFAULT_SHORTCUTS["swap_languages"]))

    def _save_settings(self) -> None:
        chosen_backend = self._cmb_backend.currentData()
        chosen_target = self._cmb_default_target.currentData()
        deepl_key = self._edt_deepl_key.text().strip()
        libre_url = self._edt_libre_url.text().strip()
        nvidia_key = self._edt_nvidia_key.text().strip()
        nvidia_url = self._edt_nvidia_url.text().strip()
        nvidia_model = self._edt_nvidia_model.text().strip()

        kwargs = {}
        if chosen_backend == "deepl":
            if not deepl_key:
                QMessageBox.warning(self, "Missing Key", "DeepL API requires an authentication key.")
                return
            kwargs["api_key"] = deepl_key
        elif chosen_backend == "libretranslate":
            if libre_url:
                kwargs["base_url"] = libre_url
        elif chosen_backend == "nvidia_nim":
            if not nvidia_key:
                QMessageBox.warning(self, "Missing Key", "NVIDIA NIM requires an API key.")
                return
            kwargs["api_key"] = nvidia_key
            if nvidia_url:
                kwargs["base_url"] = nvidia_url
            if nvidia_model:
                kwargs["model"] = nvidia_model

        try:
            self._manager.set_backend(chosen_backend, **kwargs)
            if chosen_target:
                self._manager.set_target_language(chosen_target)

            # Persist and apply chosen UI interface language
            chosen_ui_lang = self._cmb_ui_lang.currentData()
            if chosen_ui_lang:
                self._manager.set_interface_language(chosen_ui_lang)
                self._initial_ui_lang = chosen_ui_lang
                set_current_locale(chosen_ui_lang)
                self.locale_changed.emit(chosen_ui_lang)

            # Persist and apply chosen font sizes
            chosen_ui_font = self._spn_ui_font.value()
            chosen_text_font = self._spn_text_font.value()
            self._manager.set_ui_font_size(chosen_ui_font)
            self._manager.set_text_font_size(chosen_text_font)
            self._initial_ui_font_size = chosen_ui_font
            self._initial_text_font_size = chosen_text_font
            if self.parent() and hasattr(self.parent(), "apply_font_size"):
                self.parent().apply_font_size(chosen_text_font)
            self.font_size_changed.emit(chosen_text_font)

            # Persist and apply chosen theme
            chosen_theme = self._cmb_theme.currentData()
            if chosen_theme:
                self._manager.set_theme(chosen_theme)
                self._initial_theme = chosen_theme
                app_inst = QApplication.instance()
                if app_inst:
                    app_inst.setStyleSheet(
                        get_stylesheet(
                            chosen_theme,
                            ui_font_size=chosen_ui_font,
                            text_font_size=chosen_text_font,
                        )
                    )

            # Collect and persist shortcuts
            new_shortcuts = {
                "translate_selection": self._kse_translate.keySequence().toString() or DEFAULT_SHORTCUTS["translate_selection"],
                "ocr_capture": self._kse_ocr.keySequence().toString() or DEFAULT_SHORTCUTS["ocr_capture"],
                "ocr_overlay": self._kse_overlay.keySequence().toString() or DEFAULT_SHORTCUTS["ocr_overlay"],
                "show_main": self._kse_show.keySequence().toString() or DEFAULT_SHORTCUTS["show_main"],
                "swap_languages": self._kse_swap.keySequence().toString() or DEFAULT_SHORTCUTS["swap_languages"],
            }
            self._manager.set_shortcuts(new_shortcuts)

            # Apply global shortcuts to Fedora/GNOME desktop
            success, msg = apply_gnome_shortcuts(new_shortcuts)
            if not success:
                logger.warning("Could not apply GNOME shortcuts: %s", msg)

            self.accept()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {exc}")


class MainWindow(QMainWindow):
    """Main two-pane translation window with auto-translation and collapsible history."""

    translate_requested = pyqtSignal(str, str, str)
    locale_changed = pyqtSignal(str)
    quit_app_requested = pyqtSignal()
    copy_to_clipboard_requested = pyqtSignal(str)

    def __init__(
        self,
        translation_manager: TranslationManager,
        history: TranslationHistory,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._manager = translation_manager
        self._history = history
        self._clipboard = ClipboardManager()
        self.copy_to_clipboard_requested.connect(self.set_clipboard_text)
        self._last_requested_key: tuple[str, str, str] = ("", "", "")

        # 1. Aggressive debounce timer for near-real-time translation (300ms)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(300)
        self._debounce_timer.timeout.connect(self._on_debounce_translate)

        # Voice input state
        self._voice_worker: Optional[VoiceInputWorker] = None
        self._is_recording = False

        # Text-to-Speech (TTS) manager
        self._tts_manager = TTSManager(self)
        self._tts_manager.state_changed.connect(self._on_tts_state_changed)
        self._tts_manager.error_occurred.connect(self._on_tts_error)
        self._last_detected_source_lang: str = ""

        # Spinner animation timer
        self._spinner_dots = 0
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(300)
        self._spinner_timer.timeout.connect(self._animate_spinner)

        self._history_collapsed = True
        self._init_window()
        self._setup_ui()
        self.apply_font_size(self._manager.get_font_size())
        self._init_swap_shortcut()
        self._refresh_history_list()
        self.apply_locale(self._manager.get_interface_language())
        self._update_status_bar()

    def apply_font_size(self, font_size: int) -> None:
        """Dynamically update font size for source and target text areas."""
        size = max(10, min(36, int(font_size)))
        font = self._txt_source.font()
        font.setPointSize(size)
        self._txt_source.setFont(font)
        self._txt_target.setFont(font)

    def _init_window(self) -> None:
        self.setWindowTitle("LingoFlash")
        icon = get_app_icon()
        if icon is not None and not icon.isNull():
            self.setWindowIcon(icon)
        self.setMinimumSize(920, 620)
        self.resize(980, 680)

    def _setup_ui(self) -> None:
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        # 1. Top toolbar
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)

        self._lbl_logo = QLabel(self)
        icon = get_app_icon()
        if icon is not None and not icon.isNull():
            self._lbl_logo.setPixmap(icon.pixmap(22, 22))
        lbl_app = QLabel("<b>LingoFlash</b>", self)
        lbl_app.setStyleSheet("font-size: 16px;")

        self._btn_settings = QPushButton("⚙ Settings", self)
        self._btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_settings.clicked.connect(self._open_settings)

        toolbar.addWidget(self._lbl_logo)
        toolbar.addWidget(lbl_app)
        toolbar.addStretch()
        toolbar.addWidget(self._btn_settings)
        main_layout.addLayout(toolbar)

        # 2. Main horizontal splitter (Source pane | Target pane)
        self._splitter = QSplitter(Qt.Orientation.Horizontal, central_widget)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(10)

        # --- Left Pane (Source) ---
        left_widget = QWidget(self)
        left_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 10, 0)
        left_layout.setSpacing(8)

        # Source Header (aligned with Target Header)
        left_header = QHBoxLayout()
        left_header.setContentsMargins(0, 0, 0, 0)
        left_header.setSpacing(8)
        self._lbl_from = QLabel("From:", self)
        self._lbl_from.setStyleSheet("font-weight: 500;")
        self._cmb_source_lang = SearchableComboBox(self)
        for code, name in LANGUAGES:
            self._cmb_source_lang.addItem(name, code)
        self._cmb_source_lang.currentIndexChanged.connect(self._on_source_lang_changed)

        self._lbl_detected = QLabel("", self)
        self._lbl_detected.setObjectName("detectedBadge")

        left_header.addWidget(self._lbl_from)
        left_header.addWidget(self._cmb_source_lang)
        left_header.addWidget(self._lbl_detected)
        left_header.addStretch()
        left_layout.addLayout(left_header)

        # Source Text Area
        self._txt_source = QTextEdit(self)
        self._txt_source.setFrameShape(QFrame.Shape.NoFrame)
        self._txt_source.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._txt_source.setPlaceholderText("Type or paste text to translate...")
        self._txt_source.textChanged.connect(self._on_source_text_changed)
        left_layout.addWidget(self._txt_source)

        # Left Footer (symmetrical with Right Footer)
        left_footer = QHBoxLayout()
        left_footer.setContentsMargins(0, 4, 0, 0)
        left_footer.setSpacing(10)

        self._btn_clear_source = QPushButton("🗑 Clear", self)
        self._btn_clear_source.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear_source.setToolTip("Clear source text")
        self._btn_clear_source.clicked.connect(self._clear_source)

        self._btn_mic = QPushButton("🎙 Voice", self)
        self._btn_mic.setObjectName("micBtn")
        self._btn_mic.setProperty("active", "false")
        self._btn_mic.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_mic.setToolTip("Voice Input (Click to speak)")
        self._btn_mic.clicked.connect(self._toggle_voice_input)

        self._btn_tts_source = QPushButton(self)
        self._btn_tts_source.setObjectName("ttsSourceBtn")
        self._btn_tts_source.setProperty("active", "false")
        self._btn_tts_source.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_tts_source.setIcon(create_svg_icon("speaker", color="#A1A1AA", size=16))
        self._btn_tts_source.setToolTip("Listen (Text-to-Speech)")
        self._btn_tts_source.clicked.connect(self._play_source_tts)

        self._lbl_char_count = QLabel("0 characters", self)
        self._lbl_char_count.setStyleSheet("color: #888888; font-size: 11px;")

        self._lbl_spinner = QLabel("Translating…", self)
        self._lbl_spinner.setObjectName("spinnerLabel")
        self._lbl_spinner.hide()

        left_footer.addWidget(self._btn_clear_source)
        left_footer.addWidget(self._btn_mic)
        left_footer.addWidget(self._btn_tts_source)
        left_footer.addWidget(self._lbl_char_count)
        left_footer.addStretch()
        left_footer.addWidget(self._lbl_spinner)
        left_layout.addLayout(left_footer)

        # --- Right Pane (Target) ---
        right_widget = QWidget(self)
        right_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(8)

        # Target Header (aligned with Source Header)
        right_header = QHBoxLayout()
        right_header.setContentsMargins(14, 0, 0, 0)
        right_header.setSpacing(8)
        self._lbl_to = QLabel("To:", self)
        self._lbl_to.setStyleSheet("font-weight: 500;")
        self._cmb_target_lang = SearchableComboBox(self)
        for code, name in LANGUAGES:
            if code != "auto":
                self._cmb_target_lang.addItem(name, code)
        # Initialize with user's saved target language
        saved_target = self._manager.get_target_language()
        idx_target = self._cmb_target_lang.findData(saved_target)
        if idx_target < 0:
            idx_target = self._cmb_target_lang.findData("uk")
        if idx_target < 0:
            idx_target = 0

        self._cmb_target_lang.setCurrentIndex(idx_target)
        self._cmb_target_lang.setEditText(self._cmb_target_lang.itemText(idx_target))
        self._cmb_target_lang.currentIndexChanged.connect(self._on_target_lang_changed)

        right_header.addWidget(self._lbl_to)
        right_header.addWidget(self._cmb_target_lang)
        right_header.addStretch()
        right_layout.addLayout(right_header)

        # Target Text Area
        self._txt_target = QTextEdit(self)
        self._txt_target.setFrameShape(QFrame.Shape.NoFrame)
        self._txt_target.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self._txt_target.setReadOnly(True)
        self._txt_target.setPlaceholderText("Translation will appear here automatically...")
        right_layout.addWidget(self._txt_target)

        # Right Footer (symmetrical with Left Footer)
        right_footer = QHBoxLayout()
        right_footer.setContentsMargins(0, 4, 0, 0)
        right_footer.setSpacing(10)

        self._btn_copy = QPushButton("📋 Copy", self)
        self._btn_copy.setObjectName("copyBtn")
        self._btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_copy.setToolTip("Copy translated text to clipboard")
        self._btn_copy.clicked.connect(self._copy_result)

        self._btn_tts_target = QPushButton(self)
        self._btn_tts_target.setObjectName("ttsTargetBtn")
        self._btn_tts_target.setProperty("active", "false")
        self._btn_tts_target.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_tts_target.setIcon(create_svg_icon("speaker", color="#A1A1AA", size=16))
        self._btn_tts_target.setToolTip("Listen (Text-to-Speech)")
        self._btn_tts_target.clicked.connect(self._play_target_tts)

        self._lbl_target_char_count = QLabel("", self)
        self._lbl_target_char_count.setStyleSheet("color: #888888; font-size: 11px;")

        right_footer.addWidget(self._btn_copy)
        right_footer.addWidget(self._btn_tts_target)
        right_footer.addWidget(self._lbl_target_char_count)
        right_footer.addStretch()
        right_layout.addLayout(right_footer)

        # Add panes to splitter and configure proportional resizing
        self._splitter.addWidget(left_widget)
        self._splitter.addWidget(right_widget)
        self._splitter.setStretchFactor(0, 1)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([500, 500])
        main_layout.addWidget(self._splitter, stretch=1)

        # Circular Swap button positioned over splitter handle between headers
        self._btn_swap = QPushButton("⇄", central_widget)
        self._btn_swap.setObjectName("swapBtn")
        self._btn_swap.setToolTip("Swap languages and text")
        self._btn_swap.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_swap.setFixedSize(32, 32)
        self._btn_swap.clicked.connect(self._swap_languages)
        self._splitter.splitterMoved.connect(lambda pos, index: self._update_swap_button_position())

        # 3. Collapsible Translation History Panel (Accordion)
        history_header = QHBoxLayout()
        history_header.setContentsMargins(0, 4, 0, 0)

        self._btn_toggle_history = QPushButton("▶ Translation History (0)", self)
        self._btn_toggle_history.setObjectName("historyToggleBtn")
        self._btn_toggle_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_toggle_history.clicked.connect(self._toggle_history)

        self._btn_clear_history = QPushButton("Clear", self)
        self._btn_clear_history.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_clear_history.clicked.connect(self._clear_history)

        history_header.addWidget(self._btn_toggle_history)
        history_header.addStretch()
        history_header.addWidget(self._btn_clear_history)
        main_layout.addLayout(history_header)

        self._lst_history = QListWidget(self)
        self._lst_history.setFrameShape(QFrame.Shape.NoFrame)
        self._lst_history.setMaximumHeight(120)
        self._lst_history.itemDoubleClicked.connect(self._on_history_item_clicked)
        self._lst_history.hide()  # Collapsed by default to maximize text space
        main_layout.addWidget(self._lst_history)

        # Status Bar
        self._status_bar = QStatusBar(self)
        self.setStatusBar(self._status_bar)

    def _update_status_bar(self) -> None:
        try:
            backend_name = self._manager.backend.name
        except Exception:
            backend_name = "Unknown"
        self._status_bar.showMessage(f"Active Backend: {backend_name} | {tr('ready')}")

    def _on_source_text_changed(self) -> None:
        """Triggered on every keystroke in source text box."""
        text = self._txt_source.toPlainText()
        self._lbl_char_count.setText(f"{len(text)} {tr('characters')}")

        if text.strip():
            # Restart the 300ms aggressive debounce timer
            self._debounce_timer.start(300)
        else:
            self._debounce_timer.stop()
            self._txt_target.clear()
            self._lbl_target_char_count.setText("")
            self._lbl_detected.setText("")
            self._last_requested_key = ("", "", "")
            self.set_loading(False)

    def _on_debounce_translate(self) -> None:
        """Fires when user pauses typing for 300ms."""
        self._trigger_translation()

    def _trigger_translation(self, force: bool = False) -> None:
        """Emit translate_requested signal if text and languages changed."""
        text = self._txt_source.toPlainText().strip()
        if not text:
            return

        src_code = self._cmb_source_lang.currentData() or "auto"
        tgt_code = self._cmb_target_lang.currentData() or "en"
        key = (text, src_code, tgt_code)

        if not force and key == self._last_requested_key:
            return

        self._last_requested_key = key
        self.set_loading(True)
        self.translate_requested.emit(text, src_code, tgt_code)

    def _on_source_lang_changed(self) -> None:
        """Handle source language dropdown change."""
        src_code = self._cmb_source_lang.currentData()
        if src_code != "auto":
            self._lbl_detected.setText("")
        self._trigger_translation(force=True)

    def _on_target_lang_changed(self) -> None:
        """Handle target language changes, persist to config and retranslate."""
        tgt_code = self._cmb_target_lang.currentData()
        if tgt_code:
            self._manager.set_target_language(tgt_code)
        self._trigger_translation(force=True)

    def get_target_language(self) -> str:
        """Return the current target language code."""
        return self._cmb_target_lang.currentData() or self._manager.get_target_language()

    def set_loading(self, loading: bool) -> None:
        """Show or hide the subtle loading spinner and update status bar."""
        if loading:
            self._lbl_spinner.show()
            self._spinner_timer.start()
            tgt_code = self._cmb_target_lang.currentData() or "en"
            self._status_bar.showMessage(f"Translating to {tgt_code.upper()}…")
        else:
            self._spinner_timer.stop()
            self._lbl_spinner.hide()

    def _animate_spinner(self) -> None:
        """Cycle subtle animated dots while translation is in progress."""
        self._spinner_dots = (self._spinner_dots + 1) % 4
        self._lbl_spinner.setText("Translating" + "." * self._spinner_dots)

    def set_source_text(self, text: str) -> None:
        """Populate the source text field and trigger immediate translation."""
        self._last_requested_key = ("", "", "")
        if not text.strip():
            self._lbl_detected.setText("")
        self._txt_source.setPlainText(text)
        self._trigger_translation(force=True)

    def set_translation_error(self, error_message: str) -> None:
        """Display an error message directly inside target text area and stop loading state."""
        self.set_loading(False)
        self._txt_target.setPlainText(error_message)
        self._lbl_target_char_count.setText("")
        self._lbl_detected.setText("")
        self._status_bar.showMessage(error_message, 6000)

    def set_translation_result(
        self,
        source_text: str,
        translated_text: str,
        source_lang: str = "",
        target_lang: str = "",
    ) -> None:
        """Update target translation field, character count, and history."""
        self.set_loading(False)
        if translated_text.startswith("⚠️") or translated_text.startswith("Error:"):
            self.set_translation_error(translated_text)
            return

        self._txt_target.setPlainText(translated_text)
        if translated_text:
            self._lbl_target_char_count.setText(f"{len(translated_text)} characters")
        else:
            self._lbl_target_char_count.setText("")

        # Dynamic Auto Detect feedback
        self._last_detected_source_lang = source_lang
        current_src_code = self._cmb_source_lang.get_selected_code()
        if self._txt_source.toPlainText().strip() and source_lang and source_lang != "auto":
            lang_name = LANGUAGE_NAME_MAP.get(source_lang, source_lang.upper())
            if current_src_code == "auto":
                self._lbl_detected.setText(tr("detected_badge", lang=lang_name))
            else:
                self._lbl_detected.setText("")
        else:
            self._lbl_detected.setText("")

        self._status_bar.showMessage(tr("translation_ready"), 3000)
        self._refresh_history_list()

    def _swap_languages(self) -> None:
        """Swap languages and texts between source and target."""
        self._tts_manager.stop()
        self._lbl_detected.setText("")
        src_code = self._cmb_source_lang.currentData()
        tgt_code = self._cmb_target_lang.currentData()

        # Swap texts
        src_text = self._txt_source.toPlainText()
        tgt_text = self._txt_target.toPlainText()

        if src_code != "auto":
            # Swap combos
            idx_src_in_tgt = self._cmb_target_lang.findData(src_code)
            idx_tgt_in_src = self._cmb_source_lang.findData(tgt_code)
            if idx_src_in_tgt >= 0:
                self._cmb_target_lang.setCurrentIndex(idx_src_in_tgt)
                self._cmb_target_lang.setEditText(self._cmb_target_lang.itemText(idx_src_in_tgt))
            if idx_tgt_in_src >= 0:
                self._cmb_source_lang.setCurrentIndex(idx_tgt_in_src)
                self._cmb_source_lang.setEditText(self._cmb_source_lang.itemText(idx_tgt_in_src))

        self._last_requested_key = ("", "", "")
        self._txt_source.setPlainText(tgt_text)
        self._txt_target.setPlainText(src_text)

    def _clear_source(self) -> None:
        """Clear source and target fields."""
        self._tts_manager.stop()
        self._last_detected_source_lang = ""
        self._debounce_timer.stop()
        self._txt_source.clear()
        self._txt_target.clear()
        self._lbl_char_count.setText("0 characters")
        self._lbl_target_char_count.setText("")
        self._lbl_detected.setText("")
        self._last_requested_key = ("", "", "")
        self.set_loading(False)
        self._update_status_bar()

    def set_clipboard_text(self, text: str) -> bool:
        """Slot executed strictly on the main GUI thread to safely set clipboard."""
        if not text:
            return False

        success = False
        try:
            app = QApplication.instance()
            if app is not None:
                cb = app.clipboard()
                if cb is not None:
                    cb.setText(text)
                    if cb.text() == text:
                        success = True
        except Exception as exc:
            logger.debug("Failed setting QClipboard on main thread: %s", exc)

        # If QClipboard failed or compositor denied focus, fallback to native wl-copy/xclip
        if not success:
            return self._clipboard.copy_to_clipboard(text, force_native=True)

        return True

    def _copy_result(self) -> None:
        """Copy translated text to clipboard with quick feedback."""
        text = self._txt_target.toPlainText().strip()
        if text:
            self.copy_to_clipboard_requested.emit(text)
            self._btn_copy.setText(f"✓ {tr('copied')}")
            QTimer.singleShot(1500, lambda: self._btn_copy.setText(f"📋 {tr('copy')}"))
            self._status_bar.showMessage(tr("copied"), 2000)

    def _toggle_history(self) -> None:
        """Toggle accordion visibility for the translation history list."""
        self._history_collapsed = not self._history_collapsed
        self._lst_history.setVisible(not self._history_collapsed)
        arrow = "▶" if self._history_collapsed else "▼"
        count = len(self._history.get_all())
        self._btn_toggle_history.setText(f"{arrow} Translation History ({count})")

    def _refresh_history_list(self) -> None:
        """Populate history list items and update accordion header count."""
        self._lst_history.clear()
        entries = self._history.get_all()
        for item in reversed(entries):
            src = item.get("source_text", "").replace("\n", " ")
            tgt = item.get("translated_text", "").replace("\n", " ")
            slang = item.get("source_lang", "auto").upper()
            tlang = item.get("target_lang", "en").upper()
            display_str = f"[{slang} → {tlang}]  {src[:40]}… ➜ {tgt[:40]}…"
            list_item = QListWidgetItem(display_str)
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self._lst_history.addItem(list_item)

        arrow = "▶" if self._history_collapsed else "▼"
        self._btn_toggle_history.setText(f"{arrow} Translation History ({len(entries)})")

    def _on_history_item_clicked(self, item: QListWidgetItem) -> None:
        """Load clicked history entry into translation panes."""
        data = item.data(Qt.ItemDataRole.UserRole)
        if data:
            self._txt_source.setPlainText(data.get("source_text", ""))
            self._txt_target.setPlainText(data.get("translated_text", ""))

    def _clear_history(self) -> None:
        """Clear all stored history."""
        self._history.clear()
        self._refresh_history_list()

    def _init_swap_shortcut(self) -> None:
        """Initialize or update the in-window language swap shortcut."""
        swap_seq = self._manager.get_shortcut("swap_languages")
        if not hasattr(self, "_swap_shortcut") or self._swap_shortcut is None:
            self._swap_shortcut = QShortcut(QKeySequence(swap_seq), self)
            self._swap_shortcut.activated.connect(self._swap_languages)
        else:
            self._swap_shortcut.setKey(QKeySequence(swap_seq))

        if hasattr(self, "_btn_swap"):
            self._btn_swap.setToolTip(f"Swap languages and text ({swap_seq})")

    def apply_locale(self, locale: str | None = None) -> None:
        """Apply active locale strings to all Main Window widgets."""
        loc = locale or get_current_locale()
        self.setWindowTitle(tr("app_title", loc))
        self._btn_settings.setText(f"⚙ {tr('settings_title', loc).split(' — ')[0]}")
        if hasattr(self, "_lbl_from"):
            self._lbl_from.setText(tr("from", loc))
        if hasattr(self, "_lbl_to"):
            self._lbl_to.setText(tr("to", loc))
        self._txt_source.setPlaceholderText(tr("source_placeholder", loc))
        self._txt_target.setPlaceholderText(tr("target_placeholder", loc))
        self._btn_clear_source.setText(f"🗑 {tr('clear_history', loc).split()[0]}")
        self._btn_mic.setText(f"🎙 {tr('voice', loc)}")
        self._btn_copy.setText(f"📋 {tr('copy', loc)}")
        self._btn_clear_history.setText(tr("clear_history", loc))
        self._btn_swap.setToolTip(tr("swap_languages", loc))

        # Update Auto Detect item text in source combo
        self._cmb_source_lang.update_item_display_text("auto", tr("auto_detect", loc))
        if hasattr(self, "_btn_tts_source"):
            self._btn_tts_source.setToolTip(tr("listen", loc))
        if hasattr(self, "_btn_tts_target"):
            self._btn_tts_target.setToolTip(tr("listen", loc))

        self._update_status_bar()
        self._refresh_history_list()

    def _open_settings(self) -> None:
        """Open settings dialog."""
        dlg = SettingsDialog(self._manager, self, on_quit=self.quit_app_requested.emit)
        dlg.quit_requested.connect(self.quit_app_requested.emit)
        dlg.font_size_changed.connect(self.apply_font_size)
        dlg.locale_changed.connect(self.apply_locale)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            chosen_locale = self._manager.get_interface_language()
            self.apply_locale(chosen_locale)
            self.locale_changed.emit(chosen_locale)
            self.apply_font_size(self._manager.get_text_font_size())
            self._update_status_bar()
            self._init_swap_shortcut()
            self._trigger_translation(force=True)
        else:
            self.apply_locale(self._manager.get_interface_language())

    # --- Voice Input (Speech-to-Text) ---

    def _toggle_voice_input(self) -> None:
        """Toggle microphone recording and transcription."""
        if self._is_recording:
            self._stop_voice_input()
        else:
            self._start_voice_input()

    def _start_voice_input(self) -> None:
        """Start capturing microphone audio in background thread."""
        self._tts_manager.stop()
        src_code = self._cmb_source_lang.get_selected_code()
        logger.info("Starting voice input with standard code: %s", src_code)
        self._is_recording = True
        self._btn_mic.setProperty("active", "true")
        self._btn_mic.setStyle(self._btn_mic.style())
        self._btn_mic.setText("🔴 Listening…")
        self._status_bar.showMessage("Listening… Speak into your microphone")

        self._voice_worker = VoiceInputWorker(language_code=src_code, parent=self)
        self._voice_worker.text_captured.connect(self._on_voice_text_captured)
        self._voice_worker.status_changed.connect(lambda msg: self._status_bar.showMessage(msg))
        self._voice_worker.error.connect(self._on_voice_error)
        self._voice_worker.finished.connect(self._on_voice_finished)
        self._voice_worker.start()

    def _stop_voice_input(self) -> None:
        """Stop active microphone worker."""
        try:
            if self._voice_worker is not None and self._voice_worker.isRunning():
                self._voice_worker.stop()
                self._voice_worker.wait(150)
        except RuntimeError:
            pass
        finally:
            self._voice_worker = None
        self._on_voice_finished()

    # --- Text-to-Speech (TTS) Playback ---

    def _play_source_tts(self) -> None:
        """Play TTS for source text area with accurate pronunciation language."""
        text = self._txt_source.toPlainText().strip()
        if not text:
            return

        src_code = self._cmb_source_lang.get_selected_code()
        if src_code == "auto":
            if self._last_detected_source_lang and self._last_detected_source_lang != "auto":
                lang_code = self._last_detected_source_lang
            else:
                detected = detect_language(text)
                lang_code = detected if detected and detected != "auto" else "en"
        else:
            lang_code = src_code

        logger.info("Source TTS requested (lang=%s, chars=%d)", lang_code, len(text))
        self._tts_manager.play_or_toggle("source", text, lang_code)

    def _play_target_tts(self) -> None:
        """Play TTS for target text area with target pronunciation language."""
        text = self._txt_target.toPlainText().strip()
        if not text:
            return

        tgt_code = self._cmb_target_lang.get_selected_code()
        logger.info("Target TTS requested (lang=%s, chars=%d)", tgt_code, len(text))
        self._tts_manager.play_or_toggle("target", text, tgt_code)

    def _on_tts_state_changed(self, role: str, is_playing: bool) -> None:
        """Update Speaker button icon color and active state during playback."""
        active_color = "#3B82F6"
        inactive_color = "#A1A1AA"
        btn = self._btn_tts_source if role == "source" else self._btn_tts_target

        if is_playing:
            btn.setProperty("active", "true")
            btn.setIcon(create_svg_icon("speaker", color=active_color, size=16))
            btn.setStyle(btn.style())
            self._status_bar.showMessage(f"Playing audio ({role})…")
        else:
            btn.setProperty("active", "false")
            btn.setIcon(create_svg_icon("speaker", color=inactive_color, size=16))
            btn.setStyle(btn.style())

    def _on_tts_error(self, role: str, message: str) -> None:
        """Handle and display TTS playback errors."""
        self._status_bar.showMessage(f"Audio error: {message}", 4000)

    def _on_voice_text_captured(self, text: str) -> None:
        """Append transcribed speech to source text and move cursor."""
        current_text = self._txt_source.toPlainText()
        if current_text.strip():
            combined = current_text.rstrip() + " " + text
        else:
            combined = text

        self._txt_source.setPlainText(combined)
        cursor = self._txt_source.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self._txt_source.setTextCursor(cursor)
        self._status_bar.showMessage("Voice input captured", 3000)

    def _on_voice_error(self, error_msg: str) -> None:
        """Display voice input errors in status bar."""
        self._status_bar.showMessage(f"Voice error: {error_msg}", 5000)

    def _on_voice_finished(self) -> None:
        """Reset microphone button state on completion."""
        self._is_recording = False
        self._btn_mic.setProperty("active", "false")
        self._btn_mic.setStyle(self._btn_mic.style())
        self._btn_mic.setText("🎙 Voice")

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_swap_button_position()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._update_swap_button_position)

    def _update_swap_button_position(self) -> None:
        """Keep the circular swap button centered between the panels at header level."""
        if not hasattr(self, "_btn_swap") or not hasattr(self, "_splitter"):
            return
        handle = self._splitter.handle(1)
        if handle is None:
            return
        cw = self.centralWidget()
        if cw is None:
            return
        pos = handle.mapTo(cw, handle.rect().topLeft())
        bw = self._btn_swap.width() or 32
        bh = self._btn_swap.height() or 32
        bx = pos.x() + (handle.width() - bw) // 2

        # Align vertically with From/To language dropdown headers
        if hasattr(self, "_cmb_source_lang") and self._cmb_source_lang.isVisible():
            combo_pos = self._cmb_source_lang.mapTo(cw, self._cmb_source_lang.rect().topLeft())
            by = combo_pos.y() + (self._cmb_source_lang.height() - bh) // 2
        else:
            by = pos.y() + 6

        self._btn_swap.move(bx, by)
        self._btn_swap.raise_()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        """Hide window instead of closing completely (persists in background)."""
        self._tts_manager.stop()
        self._stop_voice_input()
        event.ignore()
        self.hide()
