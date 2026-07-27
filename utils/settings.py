import json
import os

from utils.storage import write_json, read_json

SETTINGS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "settings.json"
)

DEFAULT_SETTINGS = {
    "red_flags": ["unpaid", "web3", "commission-only"],
    "confidence_threshold": 0.75,
}


def load_settings() -> dict:
    return read_json(SETTINGS_PATH, "settings", DEFAULT_SETTINGS)


def save_settings(settings: dict):
    write_json(SETTINGS_PATH, "settings", settings)
