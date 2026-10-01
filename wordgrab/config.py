from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "WordGrab"
APP_DIR = Path(__file__).resolve().parent.parent
def _data_dir() -> Path:
    """Resolved on every call so WORDGRAB_DATA can be set after import."""
    return Path(os.environ.get("WORDGRAB_DATA") or (APP_DIR / "data"))


DATA_DIR = _data_dir()
DB_PATH = DATA_DIR / "words.db"
LOG_PATH = DATA_DIR / "wordgrab.log"
CONFIG_PATH = DATA_DIR / "config.json"

HOST = "127.0.0.1"
PORT = 8731


def log_path_safe() -> Path:
    return _data_dir() / "wordgrab.log"


def db_path_safe() -> Path:
    return _data_dir() / "words.db"


def config_path_safe() -> Path:
    return _data_dir() / "config.json"

DEFAULTS = {
    "capture_enabled": True,
    "capture_browser": False,
    "auto_lookup": True,
    "double_click_interval_ms": 400,
    "min_word_len": 1,
    "max_word_len": 40,
    "ignore_apps": [
        "Windows Input Experience",
        "TextInputHost",
        "SearchHost",
    ],
    "notify_on_add": True,
    "play_sound_on_add": False,
    "tray": True,
    "hotkey_panel": "ctrl+alt+w",
    "hotkey_review": "ctrl+alt+r",
    "hotkey_quick_add": "ctrl+alt+d",
}

_config: dict | None = None


def load_config() -> dict:
    global _config
    cfg = dict(DEFAULTS)
    try:
        path = config_path_safe()
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                cfg.update(loaded)
    except Exception:
        pass
    _config = cfg
    return cfg


def save_config(patch: dict | None = None) -> dict:
    cfg = load_config()
    if patch:
        cfg.update(patch)
        path = config_path_safe()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg


def data_ready() -> None:
    _data_dir().mkdir(parents=True, exist_ok=True)