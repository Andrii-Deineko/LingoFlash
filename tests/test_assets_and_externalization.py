"""Unit tests for externalized assets: locales (i18n), themes.json, and vector SVG logo."""

import json
from pathlib import Path
import unittest

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon

from app.utils.i18n import (
    SUPPORTED_LOCALES,
    tr,
    set_current_locale,
    get_current_locale,
    reload_locales,
    get_locales_directory,
    load_locale,
)
from app.utils.theme import (
    get_available_themes,
    get_stylesheet,
    get_theme_palette,
    get_app_icon,
    reload_themes,
)

# Ensure offscreen QApplication exists for QIcon/QPixmap operations
app = QApplication.instance() or QApplication([])


class TestAssetsAndExternalization(unittest.TestCase):
    """Test suite covering locales JSON, themes JSON, and logo SVG."""

    def setUp(self) -> None:
        reload_locales()
        reload_themes()
        set_current_locale("en")

    def test_locales_files_exist_and_are_valid_json(self) -> None:
        """Verify all supported locales exist as non-empty JSON files."""
        locales_dir = get_locales_directory()
        self.assertTrue(locales_dir.is_dir(), f"Locales dir missing: {locales_dir}")

        for loc_id, loc_name in SUPPORTED_LOCALES.items():
            file_path = locales_dir / f"{loc_id}.json"
            self.assertTrue(file_path.is_file(), f"Locale file missing: {file_path}")
            data = load_locale(loc_id)
            self.assertIsInstance(data, dict)
            self.assertGreater(len(data), 20, f"Locale {loc_id} has too few entries")

            # Check core keys exist in all locales
            core_keys = [
                "app_title",
                "settings_title",
                "save",
                "cancel",
                "quit_app",
                "tab_translation",
                "tab_hotkeys",
                "tab_appearance",
                "source_placeholder",
                "target_placeholder",
            ]
            for key in core_keys:
                self.assertIn(key, data, f"Key '{key}' missing in locale '{loc_id}'")

    def test_i18n_translation_and_fallback(self) -> None:
        """Test translation lookup, language switching, and format arguments."""
        set_current_locale("en")
        self.assertEqual(tr("save"), "Save")
        self.assertEqual(tr("cancel"), "Cancel")

        set_current_locale("ru")
        self.assertEqual(tr("save"), "Сохранить")
        self.assertEqual(tr("cancel"), "Отмена")

        set_current_locale("uk")
        self.assertEqual(tr("save"), "Зберегти")
        self.assertEqual(tr("cancel"), "Скасувати")

        set_current_locale("sv")
        self.assertEqual(tr("save"), "Spara")
        self.assertEqual(tr("cancel"), "Avbryt")

        # Explicit locale override in tr()
        self.assertEqual(tr("save", locale="sv"), "Spara")

        # Fallback to key itself if not found anywhere
        self.assertEqual(tr("non_existent_key_xyz"), "non_existent_key_xyz")

        # Fallback to English if key missing in a specific language
        # (simulate with unknown language code)
        self.assertEqual(tr("save", locale="esperanto_fake"), "Save")

        # String formatting with tr()
        # "detected_badge": "({lang} detected)" in en
        formatted = tr("detected_badge", lang="French")
        self.assertIn("French", formatted)

    def test_themes_json_structure_and_themes_count(self) -> None:
        """Verify assets/themes.json exists, contains >=11 themes, and valid template."""
        themes = get_available_themes()
        self.assertGreaterEqual(len(themes), 11)
        theme_ids = [t[0] for t in themes]

        expected_themes = [
            "dark",
            "light",
            "nord",
            "dracula",
            "amoled",
            "catppuccin",
            "tokyo_night",
            "solarized_dark",
            "solarized_light",
            "gruvbox",
            "high_contrast",
        ]
        for t in expected_themes:
            self.assertIn(t, theme_ids, f"Expected theme '{t}' not in available themes")
            palette = get_theme_palette(t)
            self.assertIn("bg_app", palette)
            self.assertIn("accent", palette)
            self.assertIn("text_primary", palette)

    def test_theme_stylesheet_compilation(self) -> None:
        """Verify QSS compiles correctly for various themes without leftover placeholders."""
        for t in ["dark", "light", "nord", "dracula", "amoled", "catppuccin"]:
            qss = get_stylesheet(t, ui_font_size=14, text_font_size=16)
            self.assertIsInstance(qss, str)
            self.assertGreater(len(qss), 500)
            # Ensure our token placeholders like {ui_sz}, {bg_main}, etc. are replaced
            self.assertNotIn("{ui_sz}", qss)
            self.assertNotIn("{txt_sz}", qss)
            self.assertNotIn("{bg_main}", qss)
            self.assertNotIn("{accent}", qss)
            self.assertNotIn("{card_border}", qss)

    def test_logo_svg_and_icon_loading(self) -> None:
        """Verify assets/logo.svg exists and get_app_icon() returns valid QIcon."""
        logo_path = Path(__file__).resolve().parent.parent / "assets" / "logo.svg"
        self.assertTrue(logo_path.is_file(), f"Logo file missing: {logo_path}")

        with open(logo_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("<svg", content)
        self.assertIn("</svg>", content)
        self.assertIn("<path", content)

        icon = get_app_icon()
        self.assertIsInstance(icon, QIcon)
        self.assertFalse(icon.isNull())

        # Verify rasterization to pixmaps
        for sz in [16, 24, 32, 48, 64, 128]:
            pix = icon.pixmap(sz, sz)
            self.assertFalse(pix.isNull())
            self.assertEqual(pix.width(), sz)
            self.assertEqual(pix.height(), sz)


if __name__ == "__main__":
    unittest.main()
