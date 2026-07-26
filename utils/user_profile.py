import json
import os

PROFILE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "user_profile.json",
)

DEFAULT_PROFILE = {
    "full_name": "",
    "email": "",
    "phone": "",
    "location": "",
    "linkedin_url": "",
    "portfolio_url": "",
    "github_url": "",
    "work_authorization": "",
    "notice_period": "",
    "desired_salary_range": "",
    "career_summary": "",
}


def load_user_profile() -> dict:
    if not os.path.exists(PROFILE_PATH):
        save_user_profile(DEFAULT_PROFILE)
        return DEFAULT_PROFILE.copy()
    with open(PROFILE_PATH, "r") as f:
        saved = json.load(f)
    return {**DEFAULT_PROFILE, **saved}


def save_user_profile(profile: dict):
    os.makedirs(os.path.dirname(PROFILE_PATH), exist_ok=True)
    with open(PROFILE_PATH, "w") as f:
        json.dump(profile, f, indent=2)
