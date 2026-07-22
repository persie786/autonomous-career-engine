import json
from datetime import datetime, timedelta

from database.db_handler import (
    get_jobs_in_period,
    get_status_counts,
    save_weekly_report,
)
from utils.ai_router import generate_json
from utils.logger import setup_logger

logger = setup_logger("report_generator")

SUMMARY_PROMPT = """Write a brief, honest 2-3 sentence weekly recap for a job seeker's \
automated job search pipeline, based on these stats for the past {days} days:

New jobs added to pipeline: {new_jobs}
Applications submitted: {applications}
Current status breakdown (all-time totals, as of today): {status_counts}
Top companies applied to this period: {top_companies}

Do not invent any numbers not given above — this is a status update, not a pep talk.

Respond with ONLY valid JSON: {{"summary": "your 2-3 sentence recap"}}"""


def generate_weekly_report(days: int = 7) -> dict:
    period_end = datetime.now()
    period_start = period_end - timedelta(days=days)
    start_str = period_start.strftime("%Y-%m-%d %H:%M:%S")
    end_str = period_end.strftime("%Y-%m-%d %H:%M:%S")

    new_jobs = get_jobs_in_period("date_added", start_str, end_str)
    applied_jobs = get_jobs_in_period("date_applied", start_str, end_str)
    status_counts = get_status_counts()

    company_counts = {}
    for job in applied_jobs:
        company_counts[job["company"]] = company_counts.get(job["company"], 0) + 1
    top_companies = sorted(company_counts.items(), key=lambda x: -x[1])[:5]

    stats = {
        "period_days": days,
        "new_jobs_added": len(new_jobs),
        "applications_submitted": len(applied_jobs),
        "status_counts": status_counts,
        "top_companies": top_companies,
    }

    try:
        raw_text, _ = generate_json(
            "",
            SUMMARY_PROMPT.format(
                days=days,
                new_jobs=len(new_jobs),
                applications=len(applied_jobs),
                status_counts=status_counts,
                top_companies=top_companies,
            ),
        )
        summary_text = json.loads(raw_text).get("summary", "")
    except Exception:
        logger.exception(
            "AI summary generation failed — saving report without narrative."
        )
        summary_text = (
            "Summary unavailable this week (AI call failed) — see the numbers above."
        )

    report_id = save_weekly_report(start_str, end_str, stats, summary_text)
    return {
        **stats,
        "id": report_id,
        "summary_text": summary_text,
        "period_start": start_str,
        "period_end": end_str,
    }
