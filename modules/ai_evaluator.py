import json
import os
import time
from openai import OpenAI
from modules.cv_generator import generate_for_job
from database.db_handler import get_job_by_id

from database.db_handler import add_job, update_job_status, log_activity
from utils.settings import load_settings
from utils.logger import setup_logger

logger = setup_logger("ai_evaluator")

from utils.ai_router import generate_json

MAX_DESCRIPTION_CHARS = (
    3000  # keeps token usage predictable against Groq's free-tier limits
)

SYSTEM_PROMPT = """You are a job-fit evaluator for an entry-level software engineering \
candidate's automated job search. Given one job posting, decide whether it's worth pursuing.

Respond GO if it's a legitimate software engineering role reasonably open to an \
entry-level candidate. Respond NO-GO if any of these apply: it requires significantly \
more experience than "entry-level" despite how it's titled, it's unpaid/commission-only/ \
equity-only, it isn't actually a software engineering role, or the listing shows other \
clear signs of being low-quality or untrustworthy.

Respond with ONLY valid JSON, no other text, in exactly this shape:
{"decision": "GO" or "NO-GO", "match_score": a number from 0.0 to 1.0 rating overall fit, "reason": "one concise sentence"}"""


def evaluate_job(job: dict) -> dict | None:
    """
    Sends one candidate to Groq for a GO/NO-GO decision and match score.
    Returns None on any failure — callers should treat None as 'needs a
    human look', never as an implicit NO-GO.
    """
    description = job["job_description"][:MAX_DESCRIPTION_CHARS]
    user_message = f"Job title: {job['role']}\nCompany: {job['company']}\nDescription:\n{description}"

    try:
        raw_text, model_used = generate_json(
            SYSTEM_PROMPT, user_message, temperature=0.2
        )
        data = json.loads(raw_text)
    except Exception:
        logger.exception(f"Evaluation failed for '{job['role']}' at {job['company']}")
        return None

    decision = data.get("decision")
    score = data.get("match_score")
    reason = data.get("reason", "")

    if decision not in ("GO", "NO-GO") or not isinstance(score, (int, float)):
        logger.warning(f"Evaluator returned unexpected shape: {data}")
        return None

    return {
        "decision": decision,
        "match_score": max(0.0, min(1.0, float(score))),
        "reason": reason,
        "model_used": model_used,
    }


def run_sourcing_pipeline(candidates: list[dict]) -> dict:
    """
    Evaluates each candidate and saves the GO ones, routed by the confidence
    threshold. Returns counts for the UI. This is what the Sourcing Queue
    tab's "Trigger JobSpy" button calls.
    """
    settings = load_settings()
    threshold = settings.get("confidence_threshold", 0.75)

    counts = {
        "auto_approved": 0,
        "manual_review": 0,
        "rejected": 0,
        "needs_consultation": 0,
    }

    for job in candidates:
        result = evaluate_job(job)

        if result is None:
            job_id = add_job(
                company=job["company"],
                role=job["role"],
                job_url=job["job_url"],
                job_description=job["job_description"],
                job_source=job.get("job_source"),
                search_profile=job.get("search_profile"),
            )
            update_job_status(job_id, "Needs Consultation")

        elif result["decision"] == "NO-GO":
            log_activity(
                "ai_evaluator",
                f"NO-GO: {job['role']} at {job['company']} — {result['reason']}",
            )
            counts["rejected"] += 1

        else:
            job_id = add_job(
                company=job["company"],
                role=job["role"],
                job_url=job["job_url"],
                job_description=job["job_description"],
                match_score=result["match_score"],
                evaluator_reason=result["reason"],
                job_source=job.get("job_source"),
                search_profile=job.get("search_profile"),
            )

            if result["match_score"] >= threshold:
                counts["auto_approved"] += 1  # stays 'Pending', the schema default
                try:
                    generate_for_job(get_job_by_id(job_id))
                except Exception:
                    logger.exception(
                        f"Auto-generation failed for job id={job_id} — left for manual generation in the Studio."
                    )
            else:
                update_job_status(job_id, "Manual Review")
                counts["manual_review"] += 1

            log_activity(
                "ai_evaluator",
                f"GO ({result['match_score']:.2f} via {result['model_used']}): "
                f"{job['role']} at {job['company']} — {result['reason']}",
            )

        time.sleep(2)  # stays comfortably under Groq's free-tier 30 req/min

    return counts


if __name__ == "__main__":
    from database.db_handler import init_db
    from modules.scraper import source_jobs

    init_db()
    candidates = source_jobs()
    print(f"Evaluating {len(candidates)} candidate(s)...")
    print(run_sourcing_pipeline(candidates))
