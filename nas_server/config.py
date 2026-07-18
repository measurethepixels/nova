import os
import json
import sys

SETTINGS_PATH = os.environ.get(
    "SEESTAR_SETTINGS",
    os.path.join(os.path.expanduser("~"), "seestar_database", "settings.json"),
)

DEFAULTS = {
    "seestar_incoming_path": "/mnt/seestar/incoming",
    "seestar_library_path": "/mnt/seestar/library",
    "db_path": os.path.join(os.path.expanduser("~"), "seestar_database", "astro_data.db"),
    "observer_lat": 0.0,
    "observer_lon": 0.0,
    "observer_elevation_m": 0,
    "siril_path": "/usr/bin/siril",
    "stability_wait_seconds": 60,
    "api_host": "0.0.0.0",
    "api_port": 8000,
    "anthropic_api_key": "",
    "ollama_url": "http://localhost:11434",
    "ollama_model": "qwen2.5-coder:7b",
    "ollama_vision_model": "",  # recommended: "moondream:latest" — pull via: ollama pull moondream
    "auto_assess": True,
    "stretch_auto_optimize": True,
    "subframe_claude_threshold": 0.10,
    "cosmic_clarity_enabled": False,
    "cosmic_clarity_gpu": True,
    "auto_process_enabled": False,
    "auto_process_workflow": "seestar_broadband",
    "manual_review_enabled": False,
    "server_host": "http://localhost:8000",
}


def load_settings() -> dict:
    if not os.path.exists(SETTINGS_PATH):
        print(f"[config] No settings file found at {SETTINGS_PATH}.")
        print("[config] Copy nas_server/settings.example.json and edit it.")
        sys.exit(1)
    with open(SETTINGS_PATH) as f:
        saved = json.load(f)
    return {**DEFAULTS, **saved}


def save_setting(key: str, value) -> None:
    """Persist a single key to settings.json and update the in-memory dict."""
    with open(SETTINGS_PATH) as f:
        data = json.load(f)
    data[key] = value
    with open(SETTINGS_PATH, "w") as f:
        json.dump(data, f, indent=2)
    settings[key] = value


settings = load_settings()
