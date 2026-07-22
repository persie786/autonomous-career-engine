import os
import re
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from database.db_handler import get_connection, update_job_status, log_activity
from modules.persona_builder import get_persona
from utils.field_memory import load_field_memory
from utils.logger import setup_logger

logger = setup_logger("browser_agent")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_DATA_DIR = os.path.join(PROJECT_ROOT, "data", "browser_profile")

MAX_RETRIES = 3

# (persona field, label/placeholder/name/id keywords to try, in order)
FIELD_MAP = [
    ("full_name", ["full name", "your name", "applicant name"]),
    ("email", ["email"]),
    ("phone", ["phone", "mobile", "contact number"]),
]


def _try_fill_text_field(page, keywords: list[str], value: str) -> bool:
    if not value:
        return False
    for keyword in keywords:
        try:
            locator = page.get_by_label(re.compile(keyword, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.fill(value)
                return True
        except Exception:
            pass
        for attr in ("placeholder", "name", "id"):
            try:
                locator = page.locator(f'input[{attr}*="{keyword}" i], textarea[{attr}*="{keyword}" i]')
                if locator.count() > 0:
                    locator.first.fill(value)
                    return True
            except Exception:
                pass
    return False


def _try_fill_cover_letter(page, text: str) -> bool:
    if not text:
        return False
    for keyword in ["cover letter", "why do you want", "message to hiring", "additional information"]:
        try:
            locator = page.get_by_label(re.compile(keyword, re.IGNORECASE))
            if locator.count() > 0:
                locator.first.fill(text)
                return True
        except Exception:
            pass
    return False


def _try_upload_resume(page, docx_path: str) -> bool:
    if not docx_path or not os.path.exists(docx_path):
        return False
    try:
        file_input = page.locator('input[type="file"]').first
        if file_input.count() > 0:
            file_input.set_input_files(docx_path)
            return True
    except Exception:
        pass
    return False


def prepare_application(job: dict, headless: bool = False) -> dict:
    """
    Opens the job's application page in a real, visible browser window and
    fills whatever fields it can confidently identify. Never submits — that's
    a deliberate action you take yourself, in the window this leaves open.
    Returns a dict including the live playwright/context/page objects, which
    the caller must eventually pass to close_browser_session().
    """
    persona = get_persona(job.get("persona_used") or "default")
    if persona is None:
        raise ValueError("No persona found for this job — build one in Settings first.")

    field_memory = load_field_memory()
    filled, skipped = [], []

    playwright = sync_playwright().start()
    context = playwright.chromium.launch_persistent_context(USER_DATA_DIR, headless=headless)
    page = context.new_page()

    try:
        page.goto(job["job_url"], timeout=30000)
        page.wait_for_load_state("networkidle", timeout=15000)
    except PlaywrightTimeoutError:
        logger.warning(f"Page load timed out for job id={job['id']} — filling whatever loaded so far.")

    for persona_key, keywords in FIELD_MAP:
        value = persona.get(persona_key, "")
        (filled if _try_fill_text_field(page, keywords, value) else skipped).append(persona_key)

    (filled if _try_fill_cover_letter(page, job.get("generated_cover_letter", "")) else skipped).append("cover_letter")
    (filled if _try_upload_resume(page, job.get("docx_path")) else skipped).append("resume_upload")

    # Field Memory Cache: reuse answers to custom questions seen on prior
    # applications. Anything new is left for you to answer by hand below.
    for question, cached_answer in field_memory.items():
        try:
            locator = page.get_by_label(re.compile(re.escape(question), re.IGNORECASE))
            if locator.count() > 0:
                locator.first.fill(cached_answer)
                filled.append(f"custom: {question}")
        except Exception:
            pass

    log_activity(
        "browser_agent",
        f"Prepared job id={job['id']}: filled [{', '.join(filled) or 'none'}], "
        f"needs your attention [{', '.join(skipped) or 'none'}].",
    )

    return {"filled": filled, "skipped": skipped, "playwright": playwright, "context": context, "page": page}


def close_browser_session(session: dict):
    """Call once you're done reviewing/submitting in the window. Safe to
    call even if the window was already closed manually."""
    try:
        session["context"].close()
    except Exception:
        pass
    try:
        session["playwright"].stop()
    except Exception:
        pass


def mark_prep_failed(job_id: int, retry_count: int):
    """Called when prepare_application() itself raises (persona missing,
    Playwright fails to launch) — not for ordinary 'some fields weren't
    found', which is expected and surfaced via the skipped list instead."""
    conn = get_connection()
    cursor = conn.cursor()
    new_count = retry_count + 1

    if new_count >= MAX_RETRIES:
        cursor.execute("UPDATE jobs SET status = 'Needs Consultation', retry_count = ? WHERE id = ?", (new_count, job_id))
        log_activity("browser_agent", f"Job id={job_id} failed prep {new_count}x — routed to Needs Consultation.")
    else:
        cursor.execute("UPDATE jobs SET status = 'Failed - Retry', retry_count = ? WHERE id = ?", (new_count, job_id))
        log_activity("browser_agent", f"Job id={job_id} prep failed (attempt {new_count}/{MAX_RETRIES}).")

    conn.commit()
    conn.close()


def confirm_submitted(job_id: int):
    """The only path that moves a job to 'Applied' — call this after you've
    personally reviewed and clicked Submit yourself."""
    update_job_status(job_id, "Applied")