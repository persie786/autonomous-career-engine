import json
import os
from utils.storage import read_json, write_json

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
    return read_json(PROFILE_PATH, "user_profile", DEFAULT_PROFILE)


def save_user_profile(profile: dict):
    write_json(PROFILE_PATH, "user_profile", profile)
