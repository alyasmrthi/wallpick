"""Configuration and on-disk locations for wallpick."""

from __future__ import annotations

import json
import os
from pathlib import Path

APP_ID = "io.github.alyasmrthi.wallpick"
APP_NAME = "wallpick"


def _xdg(var: str, fallback: str) -> Path:
    value = os.environ.get(var, "").strip()
    return Path(value) if value else Path.home() / fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME
CACHE_DIR = _xdg("XDG_CACHE_HOME", ".cache") / APP_NAME
CONFIG_FILE = CONFIG_DIR / "config.json"
THUMB_DIR = CACHE_DIR / "thumbs"
FULL_DIR = CACHE_DIR / "full"
STATE_FILE = CACHE_DIR / "current.json"
CURRENT_LINK = CACHE_DIR / "current"

DEFAULTS = {
    # Set via the first-run dialog or export WALLHAVEN_API_KEY.
    "api_key": "",
    "download_dir": str(Path.home() / "Pictures" / "Wallpapers"),
    # Optional override for how the wallpaper gets applied.
    # "{path}" is replaced with the image path, e.g.
    #   "awww img {path} --transition-type fade --transition-fps 30"
    "setter_command": "",
    "categories": "111",   # general / anime / people
    "sketchy": False,
    "nsfw": False,
    "sorting": "date_added",
    "order": "desc",
    "top_range": "1M",
    "atleast": "",         # e.g. "1920x1080"
    "ratios": "",          # e.g. "16x10,16x9"
    "thumb_width": 240,
    "concurrent_thumbs": 4,
    "dark_theme": True,
    "first_run": True,
}


class Config:
    """Thin dict wrapper that persists to ~/.config/wallpick/config.json."""

    def __init__(self) -> None:
        self.data = dict(DEFAULTS)
        self._key_from_env = False
        self.load()

    # --- persistence -----------------------------------------------------

    def load(self) -> None:
        if CONFIG_FILE.exists():
            try:
                stored = json.loads(CONFIG_FILE.read_text())
                if isinstance(stored, dict):
                    self.data.update({k: v for k, v in stored.items() if k in DEFAULTS})
            except (OSError, ValueError):
                pass

        env_key = os.environ.get("WALLHAVEN_API_KEY", "").strip()
        if env_key:
            self.data["api_key"] = env_key
            self._key_from_env = True

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = dict(self.data)
        if self._key_from_env:
            # Never write an environment-supplied key back to disk.
            payload["api_key"] = ""
        tmp = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
        try:
            tmp.write_text(json.dumps(payload, indent=2) + "\n")
            tmp.replace(CONFIG_FILE)
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass

    # --- mapping-ish access ----------------------------------------------

    def __getitem__(self, key: str):
        return self.data[key]

    def __setitem__(self, key: str, value) -> None:
        self.data[key] = value

    def get(self, key: str, default=None):
        return self.data.get(key, default)

    @property
    def download_dir(self) -> Path:
        return Path(self.data["download_dir"]).expanduser()


def ensure_dirs() -> None:
    for directory in (CONFIG_DIR, CACHE_DIR, THUMB_DIR, FULL_DIR):
        directory.mkdir(parents=True, exist_ok=True)
