import os
import json
import imaplib
import email
from email.header import decode_header
from dotenv import load_dotenv
from utils.user_context import get_current_user
from database.db_handler import get_user_email_credentials
from database.db_handler import get_jobs, update_job_status, log_activity
from utils.ai_router import generate_json
from utils.logger import setup_logger

load_dotenv()
logger = setup_logger("email_monitor")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(PROJECT_ROOT, "data", "email_state.json")

IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")


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
        (
            text.decode(encoding or "utf-8", errors="replace")
            if isinstance(text, bytes)
            else text
        )
        for text, encoding in parts
    )


def _extract_body(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain" and not part.get(
                "Content-Disposition"
            ):
                try:
                    return part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                except Exception:
                    continue
        return ""
    try:
        return msg.get_payload(decode=True).decode(
            msg.get_content_charset() or "utf-8", errors="replace"
        )
    except Exception:
        return ""


def match_job_for_email(
    subject: str, sender: str, applied_jobs: list[dict]
) -> dict | None:
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
    imap_email, imap_server, imap_pass = get_user_email_credentials(get_current_user())
    if not imap_email or not imap_pass:
        raise ValueError("No email credentials saved yet — add them in Settings first.")

    counts = {
        "scanned": 0,
        "matched": 0,
        "rejections": 0,
        "interviews": 0,
        "flagged": 0,
    }

    applied_jobs = get_jobs(status="Applied")
    if not applied_jobs:
        return counts

    state = _load_state()
    conn = imaplib.IMAP4_SSL(imap_server)

    try:
        conn.login(imap_email, imap_pass)
        conn.select("INBOX")

        result, data = conn.uid("search", None, f'UID {state["last_uid"] + 1}:*')
        if result != "OK" or not data[0]:
            return counts

        # IMAP's UID range search can include the boundary UID itself — filter defensively.
        uids = sorted(
            u for u in (int(x) for x in data[0].split()) if u > state["last_uid"]
        )

        for uid in uids:
            result, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if result != "OK" or not msg_data or msg_data[0] is None:
                continue

            msg = email.message_from_bytes(msg_data[0][1])
            subject = _decode_subject(msg.get("Subject"))
            sender = msg.get("From", "")
            body = _extract_body(msg)[:2000]
            counts["scanned"] += 1

            job = match_job_for_email(subject, sender, applied_jobs)
            if job is None:
                continue
            counts["matched"] += 1

            try:
                raw_text, _ = generate_json(
                    "",
                    CLASSIFICATION_PROMPT.format(
                        role=job["role"],
                        company=job["company"],
                        subject=subject,
                        body=body,
                    ),
                )
                parsed = json.loads(raw_text)
                category, reason = parsed.get("category"), parsed.get("reason", "")
            except Exception:
                logger.exception(f"Classification failed for email UID {uid}")
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


def list_recent_emails(limit: int = 20) -> list[dict]:
    """Fetches the most recent N messages regardless of processed-state —
    for browsing/reading, distinct from check_inbox()'s incremental,
    UID-tracked scan used for automated classification."""
    imap_email, imap_server, imap_pass = get_user_email_credentials(get_current_user())
    if not imap_email or not imap_pass:
        raise ValueError("No email credentials saved yet — add them in Settings first.")

    conn = imaplib.IMAP4_SSL(imap_server)
    emails = []
    try:
        conn.login(imap_email, imap_pass)
        conn.select("INBOX")
        result, data = conn.uid("search", None, "ALL")
        if result != "OK" or not data[0]:
            return []

        uids = sorted((int(u) for u in data[0].split()), reverse=True)[:limit]
        for uid in uids:
            result, msg_data = conn.uid("fetch", str(uid), "(RFC822)")
            if result != "OK" or not msg_data or msg_data[0] is None:
                continue
            msg = email.message_from_bytes(msg_data[0][1])
            emails.append(
                {
                    "uid": uid,
                    "subject": _decode_subject(msg.get("Subject")),
                    "sender": msg.get("From", ""),
                    "date": msg.get("Date", ""),
                    "body": _extract_body(msg),
                }
            )
    finally:
        try:
            conn.logout()
        except Exception:
            pass
    return emails


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


def test_connection(imap_email: str, imap_server: str, imap_password: str) -> tuple:
    """
    Attempts a live IMAP login and returns (success, message) — the message
    is a specific, human-readable diagnosis, not a raw exception. A wrong
    password, a wrong server address, and a genuine network problem all look
    different, so whoever's testing this knows exactly what to fix.
    """
    try:
        conn = imaplib.IMAP4_SSL(imap_server, timeout=10)
    except Exception as e:
        return (
            False,
            f"Couldn't reach '{imap_server}' — check the server address. ({e})",
        )

    try:
        conn.login(imap_email, imap_password)
    except imaplib.IMAP4.error as e:
        error_text = str(e)
        try:
            conn.logout()
        except Exception:
            pass
        if (
            "AUTHENTICATIONFAILED" in error_text.upper()
            or "invalid credentials" in error_text.lower()
        ):
            return (
                False,
                "Login rejected — this usually means the password isn't a valid App Password, or 2-Step Verification isn't turned on for this account yet.",
            )
        return False, f"Login failed: {error_text}"
    except Exception as e:
        try:
            conn.logout()
        except Exception:
            pass
        return False, f"Unexpected error while connecting: {e}"

    try:
        conn.select("INBOX")
    except Exception as e:
        conn.logout()
        return False, f"Logged in, but couldn't open the inbox: {e}"

    conn.logout()
    return True, "Connected successfully — inbox is reachable."
