import os
import json
import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv

from database.db_handler import get_jobs, update_job_status, log_activity
from utils.ai_router import generate_json
from utils.logger import setup_logger

load_dotenv()
logger = setup_logger("email_monitor")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(PROJECT_ROOT, "data", "email_state.json")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
IMAP_EMAIL = os.getenv("IMAP_EMAIL")
IMAP_PASS = os.getenv("IMAP_PASS")

CLASSIFICATION_PROMPT = """You are classifying one email that may relate to a job application \
for the role of "{role}" at "{company}". Decide which category applies:

- REJECTION: clearly states the candidate was not selected, the role was filled, or the application was unsuccessful.
- INTERVIEW: clearly invites the candidate to an interview, phone screen, or next stage.
- OTHER: anything else — an automated receipt confirmation, unrelated content, or genuinely unclear intent.

Respond with ONLY valid JSON: {{"category": "REJECTION" or "INTERVIEW" or "OTHER", "reason": "one concise sentence"}}

Email subject: {subject}
Email body (may be truncated):
{body}"""


def _load_state() -> dict:
    if not os.path.exists(STATE_PATH):
        return {"last_uid": 0}
    with open(STATE_PATH, "r") as f:
        return json.load(f)


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


def _decode_subject(raw_subject) -> str:
    parts = decode_header(raw_subject or "")
    return "".join(
        text.decode(encoding or "utf-8", errors="replace") if isinstance(text, bytes) else text
        for text, encoding in parts
    )


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                try:
                    return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", errors="replace")
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(msg.get_content_charset() or "utf-8", errors="replace")
    except Exception:
        return ""


def _match_job(subject: str, sender: str, applied_jobs: list[dict]) -> dict | None:
    """Finds the single Applied job this email most plausibly relates to, by
    checking whether the company name appears in the sender or subject.
    Deliberately conservative — returns None rather than guess."""
    haystack = f"{sender} {subject}".lower()
    for job in applied_jobs:
        if job["company"].lower() in haystack:
            return job
    return None


def check_inbox() -> dict:
    """
    Scans for emails newer than the last run, matches each to an Applied job
    by company name, classifies matched ones, and updates job status. This
    is what the Dashboard's 'Check Inbox' button calls.
    """
    if not IMAP_EMAIL or not IMAP_PASS:
        raise ValueError("IMAP_EMAIL and IMAP_PASS must be set in .env.")

    counts = {"scanned": 0, "matched": 0, "rejections": 0, "interviews": 0, "flagged": 0}

    applied_jobs = get_jobs(status="Applied")
    if not applied_jobs:
        return counts

    state = _load_state()
    conn = imaplib.IMAP4_SSL(IMAP_SERVER)

    try:
        conn.login(IMAP_EMAIL, IMAP_PASS)
        conn.select("INBOX")

        result, data = conn.uid("search", None, f'UID {state["last_uid"] + 1}:*')
        if result != "OK" or not data[0]:
            return counts

        # IMAP's UID range search can include the boundary UID itself — filter defensively.
        uids = sorted(u for u in (int(x) for x in data[0].split()) if u > state["last_uid"])

        for uid in uids:
            result, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if result != "OK" or not msg_data or msg_data[0] is None:
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode_subject(msg.get("Subject"))
            sender = msg.get("From", "")
            body = _extract_body(msg)[:2000]
            counts["scanned"] += 1

            job = _match_job(subject, sender, applied_jobs)
            if job is None:
                continue
            counts["matched"] += 1

            try:
                raw_text, _ = generate_json(
                    "", CLASSIFICATION_PROMPT.format(role=job["role"], company=job["company"], subject=subject, body=body)
                )
                parsed = json.loads(raw_text)
                category, reason = parsed.get("category"), parsed.get("reason", "")
            except Exception:
                logger.exception(f"Classification failed for email UID {uid}")
                category, reason = None, ""

            if category == "REJECTION":
                update_job_status(job["id"], "Rejected")
                log_activity("email_monitor", f"Rejection detected: {job['role']} at {job['company']} — {reason}")
                counts["rejections"] += 1
            elif category == "INTERVIEW":
                update_job_status(job["id"], "Interview")
                log_activity("email_monitor", f"Interview invite detected: {job['role']} at {job['company']} — {reason}")
                counts["interviews"] += 1
            else:
                update_job_status(job["id"], "Needs Consultation")
                log_activity("email_monitor", f"Ambiguous email for {job['role']} at {job['company']} — flagged for review.")
                counts["flagged"] += 1

        if uids:
            state["last_uid"] = max(uids)
            _save_state(state)

    finally:
        try:
            conn.logout()
        except Exception:
            pass

    return counts


if __name__ == "__main__":
    print(check_inbox())