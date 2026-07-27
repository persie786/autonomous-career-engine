import json
import os
from utils.storage import read_json, write_json

PROFILES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "search_profiles.json",
)

DEFAULT_PROFILE = {
    "name": "Default",
    "search_term": "software engineer",
    "location": "Lahore, Pakistan",
    "country_indeed": "Pakistan",
    "site_names": ["indeed", "linkedin", "zip_recruiter"],
    "results_wanted": 20,
    "hours_old": 48,
    "active": True,
}


def load_profiles() -> list[dict]:
    result = read_json(
        PROFILES_PATH, "search_profiles", {"profiles": [DEFAULT_PROFILE]}
    )
    return result["profiles"] if isinstance(result, dict) else result


def save_profiles(profiles: list[dict]):
    write_json(PROFILES_PATH, "search_profiles", {"profiles": profiles})


def get_active_profiles() -> list[dict]:
    return [p for p in load_profiles() if p.get("active", True)]


def add_profile(profile: dict):
    profiles = load_profiles()
    if any(p["name"].lower() == profile["name"].lower() for p in profiles):
        raise ValueError(f"A profile named '{profile['name']}' already exists.")
    profiles.append(profile)
    save_profiles(profiles)


def update_profile(name: str, updated: dict):
    profiles = load_profiles()
    for i, p in enumerate(profiles):
        if p["name"].lower() == name.lower():
            profiles[i] = updated
            save_profiles(profiles)
            return
    raise ValueError(f"No profile named '{name}' found.")


def delete_profile(name: str):
    profiles = [p for p in load_profiles() if p["name"].lower() != name.lower()]
    save_profiles(profiles)
