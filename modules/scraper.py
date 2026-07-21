import pandas as pd
from jobspy import scrape_jobs

from database.db_handler import job_exists, log_activity
from utils.settings import load_settings
from utils.logger import setup_logger

logger = setup_logger("scraper")

# --- Edit these to match your actual job search ---
SEARCH_TERM = "software engineer"
LOCATION = "Lahore, Pakistan"       # or "Remote" — try both, compare result counts
COUNTRY_INDEED = "Pakistan"         # required by Indeed/Glassdoor even if unused elsewhere;
                                     # check JobSpy's supported-country list if this site returns 0 results
SITE_NAMES = ["indeed", "linkedin", "zip_recruiter"]
RESULTS_WANTED = 20
HOURS_OLD = 48
# ---------------------------------------------------


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


def source_jobs() -> list[dict]:
    """
    Scrapes fresh listings, drops anything matching a global red flag, drops
    anything already in the database, and returns clean candidate dicts —
    ready for ai_evaluator.py. Does not write to the jobs table itself.
    """
    settings = load_settings()
    red_flags = settings.get("red_flags", [])

    logger.info(f"Scraping '{SEARCH_TERM}' in '{LOCATION}' across {SITE_NAMES} (last {HOURS_OLD}h)")

    try:
        raw = scrape_jobs(
            site_name=SITE_NAMES,
            search_term=SEARCH_TERM,
            location=LOCATION,
            country_indeed=COUNTRY_INDEED,
            results_wanted=RESULTS_WANTED,
            hours_old=HOURS_OLD,
            description_format="markdown",
        )
    except Exception:
        logger.exception("JobSpy scrape failed")
        log_activity("scraper", "Scrape run failed — see app.log for details.")
        return []

    total_scraped = len(raw)
    if total_scraped == 0:
        log_activity("scraper", "Scrape run found 0 listings.")
        return []

    candidates = []
    dropped_red_flag = 0
    dropped_duplicate = 0
    dropped_incomplete = 0

    for _, row in raw.iterrows():
        title = _clean(row.get("title"))
        company = _clean(row.get("company")) or "Unknown"
        job_url = _clean(row.get("job_url")) or _clean(row.get("job_url_direct"))
        description = _clean(row.get("description"))

        if not title or not job_url:
            dropped_incomplete += 1
            continue

        if job_exists(job_url):
            dropped_duplicate += 1
            continue

        matched_flag = _matches_red_flag(title, description, red_flags)
        if matched_flag:
            dropped_red_flag += 1
            logger.info(f"Dropped '{title}' at {company} — matched red flag '{matched_flag}'")
            continue

        candidates.append({
            "company": company,
            "role": title,
            "job_url": job_url,
            "job_description": description,
        })

    log_activity(
        "scraper",
        f"Scraped {total_scraped}, dropped {dropped_red_flag} (red flag), "
        f"{dropped_duplicate} (duplicate), {dropped_incomplete} (incomplete) — "
        f"{len(candidates)} candidate(s) ready for evaluation.",
    )
    logger.info(f"Scrape complete: {len(candidates)} new candidate(s) out of {total_scraped} scraped.")

    return candidates


if __name__ == "__main__":
    from database.db_handler import init_db
    init_db()
    results = source_jobs()
    print(f"\n{len(results)} candidate job(s) ready for evaluation:\n")
    for job in results:
        print(f"- {job['role']} @ {job['company']} — {job['job_url']}")