import pandas as pd
from jobspy import scrape_jobs

from database.db_handler import job_exists, log_activity, increment_scraped_count
from utils.settings import load_settings
from utils.logger import setup_logger

logger = setup_logger("scraper")


def _clean(value) -> str:
    """Normalizes a JobSpy DataFrame cell to a plain string — handles NaN safely,
    which a bare `value or ''` does not (NaN is truthy in Python)."""
    if pd.isna(value):
        return ""
    return str(value).strip()


def _matches_red_flag(title: str, description: str, red_flags: list[str]) -> str | None:
    """Returns the specific red flag that matched, or None — returning which one
    matched (not just True/False) is what makes the activity log actually useful."""
    haystack = f"{title} {description}".lower()
    for flag in red_flags:
        if flag.lower() in haystack:
            return flag
    return None


def source_jobs(profile: dict) -> list[dict]:
    """
    Scrapes fresh listings for one search profile, drops red flags and
    duplicates, and returns clean candidate dicts ready for ai_evaluator.py.
    """
    settings = load_settings()
    red_flags = settings.get("red_flags", [])

    logger.info(
        f"[{profile['name']}] Scraping '{profile['search_term']}' in '{profile['location']}' "
        f"across {profile['site_names']} (last {profile['hours_old']}h)"
    )

    try:
        raw = scrape_jobs(
            site_name=profile["site_names"],
            search_term=profile["search_term"],
            location=profile["location"],
            country_indeed=profile.get("country_indeed", ""),
            results_wanted=profile["results_wanted"],
            hours_old=profile["hours_old"],
            description_format="markdown",
        )
    except Exception:
        logger.exception(f"[{profile['name']}] JobSpy scrape failed")
        log_activity(
            "scraper",
            f"[{profile['name']}] Scrape run failed — see app.log for details.",
        )
        return []

    total_scraped = len(raw)
    increment_scraped_count(total_scraped)
    if total_scraped == 0:
        log_activity("scraper", f"[{profile['name']}] Scrape run found 0 listings.")
        return []

    candidates = []
    dropped_red_flag = dropped_duplicate = dropped_incomplete = 0

    for _, row in raw.iterrows():
        title = _clean(row.get("title"))
        company = _clean(row.get("company")) or "Unknown"
        job_url = _clean(row.get("job_url")) or _clean(row.get("job_url_direct"))
        description = _clean(row.get("description"))
        source_site = _clean(row.get("site")) or "unknown"

        if not title or not job_url:
            dropped_incomplete += 1
            continue
        if job_exists(job_url):
            dropped_duplicate += 1
            continue

        matched_flag = _matches_red_flag(title, description, red_flags)
        if matched_flag:
            dropped_red_flag += 1
            logger.info(
                f"[{profile['name']}] Dropped '{title}' at {company} — matched red flag '{matched_flag}'"
            )
            continue

        candidates.append(
            {
                "company": company,
                "role": title,
                "job_url": job_url,
                "job_description": description,
                "job_source": source_site,
                "search_profile": profile["name"],
            }
        )

    log_activity(
        "scraper",
        f"[{profile['name']}] Scraped {total_scraped}, dropped {dropped_red_flag} (red flag), "
        f"{dropped_duplicate} (duplicate), {dropped_incomplete} (incomplete) — "
        f"{len(candidates)} candidate(s) ready for evaluation.",
    )
    return candidates


if __name__ == "__main__":
    from database.db_handler import init_db
    from utils.search_profiles import get_active_profiles

    init_db()
    for profile in get_active_profiles():
        results = source_jobs(profile)
        print(f"\n[{profile['name']}] {len(results)} candidate(s):")
        for job in results:
            print(
                f"- {job['role']} @ {job['company']} ({job['job_source']}) — {job['job_url']}"
            )


if __name__ == "__main__":
    from database.db_handler import init_db

    init_db()
    results = source_jobs()
    print(f"\n{len(results)} candidate job(s) ready for evaluation:\n")
    for job in results:
        print(f"- {job['role']} @ {job['company']} — {job['job_url']}")
