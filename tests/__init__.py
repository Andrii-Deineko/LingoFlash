"""Automated test suite for LingoFlash."""

import os
import tempfile

# Prevent tests from ever polluting or overwriting the user's ~/.config/lingoflash or ~/.local/share
_tmp = tempfile.gettempdir()
os.environ.setdefault("LINGOFLASH_CONFIG_PATH", os.path.join(_tmp, "lingoflash_test_config.json"))
os.environ.setdefault("LINGOFLASH_HISTORY_PATH", os.path.join(_tmp, "lingoflash_test_history.json"))
