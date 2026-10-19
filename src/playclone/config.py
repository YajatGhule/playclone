from __future__ import annotations

import json
import os
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("PLAYCLONE_HOME") or Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "playclone"
    path = Path(base)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    try:
        return json.loads(_settings_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_settings(**values) -> None:
    settings = load_settings() | values
    path = _settings_path()
    path.write_text(json.dumps(settings, indent=2))
    path.chmod(0o600)
