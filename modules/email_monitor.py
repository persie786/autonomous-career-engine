import json
from utils.ai_router import generate_json
from database.db_handler import get_jobs, update_job_status, log_activity
from modules.gmail_api import (
    list_emails as _gmail_list,
    send_email as _gmail_send,
    set_read_status as _gmail_set_read,
)
from utils.logger import setup_logger

logger = setup_logger("email_monitor")

CLASSIFICATION_PROMPT = """You are classifying one email that may relate to a job application \
for the role of "{role}" at "{company}". Decide which category applies:

- REJECTION: clearly states the candidate was not selected, the role was filled, or the application was unsuccessful.
- INTERVIEW: clearly invites the candidate to an interview, phone screen, or next stage.
- OTHER: anything else — an automated receipt confirmation, unrelated content, or genuinely unclear intent.

Respond with ONLY valid JSON: {{"category": "REJECTION" or "INTERVIEW" or "OTHER", "reason": "one concise sentence"}}

Email subject: {subject}
Email body (may be truncated):
{body}"""


def match_job_for_email(subject: str, sender: str, applied_jobs: list) -> dict | None:
    haystack = f"{sender} {subject}".lower()
    for job in applied_jobs:
        if job["company"].lower() in haystack:
            return job
    return None


def list_emails(
    limit: int = 10,
    page_token: str = None,
    unread_only: bool = False,
    search_term: str = None,
) -> tuple:
    return _gmail_list(
        limit=limit,
        page_token=page_token,
        unread_only=unread_only,
        search_term=search_term,
    )


def set_read_status(message_id: str, seen: bool):
    _gmail_set_read(message_id, seen)


def send_email(
    to_address: str,
    subject: str,
    body: str,
    in_reply_to: str = None,
    references: str = None,
    thread_id: str = None,
):
    _gmail_send(
        to_address,
        subject,
        body,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=thread_id,
    )


def check_inbox() -> dict:
    applied_jobs = get_jobs(status="Applied")
    counts = {
        "scanned": 0,
        "matched": 0,
        "rejections": 0,
        "interviews": 0,
        "flagged": 0,
    }
    if not applied_jobs:
        return counts

    emails, _ = _gmail_list(limit=20, unread_only=True)
    for mail in emails:
        counts["scanned"] += 1
        job = match_job_for_email(mail["subject"], mail["sender"], applied_jobs)
        if job is None:
            continue
        counts["matched"] += 1

        try:
            raw_text, _ = generate_json(
                "",
                CLASSIFICATION_PROMPT.format(
                    role=job["role"],
                    company=job["company"],
                    subject=mail["subject"],
                    body=mail["body"][:2000],
                ),
            )
            parsed = json.loads(raw_text)
            category, reason = parsed.get("category"), parsed.get("reason", "")
        except Exception:
            logger.exception(f"Classification failed for message {mail['uid']}")
            category, reason = None, ""

        if category == "REJECTION":
            update_job_status(job["id"], "Rejected")
            log_activity(
                "email_monitor",
                f"Rejection detected: {job['role']} at {job['company']} — {reason}",
            )
            counts["rejections"] += 1
        elif category == "INTERVIEW":
            update_job_status(job["id"], "Interview")
            log_activity(
                "email_monitor",
                f"Interview invite detected: {job['role']} at {job['company']} — {reason}",
            )
            counts["interviews"] += 1
        else:
            update_job_status(job["id"], "Needs Consultation")
            log_activity(
                "email_monitor",
                f"Ambiguous email for {job['role']} at {job['company']} — flagged for review.",
            )
            counts["flagged"] += 1

    return counts


REPLY_DRAFT_PROMPT = """Draft a brief, professional reply to this email as the job \
candidate. Match tone to context — gracious and concise if it's a rejection, enthusiastic \
and available if it's an interview invite, neutral and helpful otherwise. Don't invent \
availability, dates, or facts not given.

From: {sender}
Subject: {subject}
Body:
{body}

{job_context}

Return ONLY valid JSON: {{"reply": "the drafted reply text, ready to copy and send"}}"""


def draft_reply(sender: str, subject: str, body: str, job: dict = None) -> str:
    job_context = (
        f"Context: relates to the candidate's application for {job['role']} at {job['company']}."
        if job
        else ""
    )
    raw_text, _ = generate_json(
        "",
        REPLY_DRAFT_PROMPT.format(
            sender=sender, subject=subject, body=body[:2000], job_context=job_context
        ),
    )
    return json.loads(raw_text).get("reply", "")
