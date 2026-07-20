import json
import os

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "settings.json"
)

DEFAULT_SETTINGS = {
    "red_flags": ["unpaid", "web3", "commission-only"],
    "confidence_threshold": 0.75,
}


def load_settings() -> dict:
    """Returns current settings, creating the file with sane defaults on first run."""
    if not os.path.exists(SETTINGS_PATH):
        save_settings(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()

    with open(SETTINGS_PATH, "r") as f:
        return json.load(f)


def save_settings(settings: dict):
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w") as f:
        json.dump(settings, f, indent=2)