import sqlite3
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from utils.logger import setup_logger

load_dotenv()
logger = setup_logger("db_handler")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            job_url TEXT UNIQUE,
            job_description TEXT,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN (
                'Pending', 'Manual Review', 'Not Interested', 'Needs Consultation',
                'Applied', 'Rejected', 'Interview', 'Ghosted', 'Failed - Retry', 'Dead'
            )),
            match_score REAL,
            persona_used TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL DEFAULT (datetime('now')),
            date_applied TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            job_url TEXT UNIQUE,
            job_description TEXT,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN (
                'Pending', 'Manual Review', 'Not Interested', 'Needs Consultation',
                'Applied', 'Rejected', 'Interview', 'Ghosted', 'Failed - Retry', 'Dead'
            )),
            match_score REAL,
            persona_used TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL DEFAULT (datetime('now')),
            date_applied TEXT
        )
    """)
    cursor.execute("PRAGMA table_info(jobs)")
    existing_columns = {row[1] for row in cursor.fetchall()}
    new_columns = {
        "evaluator_reason": "TEXT",
        "generated_cv": "TEXT",
        "generated_cover_letter": "TEXT",
        "cv_approved_at": "TEXT",
        "docx_path": "TEXT",
    }
    for col_name, col_type in new_columns.items():
        if col_name not in existing_columns:
            cursor.execute(f"ALTER TABLE jobs ADD COLUMN {col_name} {col_type}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            module TEXT NOT NULL,
            action_description TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scrape_stats (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            total_scraped INTEGER NOT NULL DEFAULT 0
        )
    """)
    cursor.execute("INSERT OR IGNORE INTO scrape_stats (id, total_scraped) VALUES (1, 0)")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company)")

    conn.commit()
    conn.close()
    logger.info("Database initialized (jobs, activity_log tables ready).")


def increment_scraped_count(amount: int):
    if amount <= 0:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE scrape_stats SET total_scraped = total_scraped + ? WHERE id = 1", (amount,))
    conn.commit()
    conn.close()


def get_scraped_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT total_scraped FROM scrape_stats WHERE id = 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0

def log_activity(module: str, action_description: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO activity_log (module, action_description) VALUES (?, ?)",
        (module, action_description),
    )
    conn.commit()
    conn.close()
    logger.info(f"[{module}] {action_description}")


def job_exists(job_url: str) -> bool:
    """Checks for an existing row by job_url — call this before add_job() from
    scraper.py so re-scraping the same listing doesn't create duplicate rows."""
    if not job_url:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM jobs WHERE job_url = ? LIMIT 1", (job_url,))
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def add_job(company, role, job_url, job_description, match_score=None, persona_used=None, evaluator_reason=None) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs (company, role, job_url, job_description, match_score, persona_used, evaluator_reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (company, role, job_url, job_description, match_score, persona_used, evaluator_reason))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    log_activity("db_handler", f"Added job: {role} at {company} (id={job_id})")
    return job_id


def update_job_status(job_id: int, new_status: str):
    conn = get_connection()
    cursor = conn.cursor()
    if new_status == "Applied":
        cursor.execute(
            "UPDATE jobs SET status = ?, date_applied = datetime('now') WHERE id = ?",
            (new_status, job_id),
        )
    else:
        cursor.execute("UPDATE jobs SET status = ? WHERE id = ?", (new_status, job_id))
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} status changed to '{new_status}'")


def get_jobs(status: str = None) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if status:
        cursor.execute("SELECT * FROM jobs WHERE status = ? ORDER BY date_added DESC", (status,))
    else:
        cursor.execute("SELECT * FROM jobs ORDER BY date_added DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

    


def get_recent_activity(limit: int = 30, module: str = None) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if module:
        cursor.execute("SELECT * FROM activity_log WHERE module = ? ORDER BY timestamp DESC LIMIT ?", (module, limit))
    else:
        cursor.execute("SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_activity_modules() -> list[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT module FROM activity_log ORDER BY module ASC")
    modules = [row[0] for row in cursor.fetchall()]
    conn.close()
    return modules


def _get_cipher() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY not set — check your .env file.")
    return Fernet(key.encode())


def encrypt_data(plaintext: str) -> str:
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt_data(ciphertext: str) -> str:
    return _get_cipher().decrypt(ciphertext.encode()).decode()


def apply_ghosting_webhook(days_threshold: int = 21) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days_threshold)).strftime("%Y-%m-%d %H:%M:%S")

    cursor.execute("""
        UPDATE jobs
        SET status = 'Ghosted'
        WHERE status = 'Applied' AND date_applied IS NOT NULL AND date_applied < ?
    """, (cutoff,))

    updated_count = cursor.rowcount
    conn.commit()
    conn.close()

    if updated_count > 0:
        log_activity("db_handler", f"Ghosting webhook: {updated_count} job(s) auto-marked as Ghosted.")

    return updated_count


if __name__ == "__main__":
    init_db()
    apply_ghosting_webhook()
    print("Schema OK. Existing jobs:", get_jobs())

def get_persona_for_company(company: str) -> str | None:
    """Looks up whether this company already has a job tied to a specific
    persona — this is what the Consistency Guardrail checks in cv_generator.py."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT persona_used FROM jobs WHERE LOWER(company) = LOWER(?) "
        "AND persona_used IS NOT NULL ORDER BY date_added ASC LIMIT 1",
        (company,),
    )
    row = cursor.fetchone()
    conn.close()
    return row["persona_used"] if row else None


def save_generated_assets(job_id: int, persona_name: str, cv_text: str, cover_letter_text: str, docx_path: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET persona_used = ?, generated_cv = ?, generated_cover_letter = ?, docx_path = ? WHERE id = ?",
        (persona_name, cv_text, cover_letter_text, docx_path, job_id),
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Generated CV/cover letter saved for job id={job_id} (persona: {persona_name})")

def approve_assets(job_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET cv_approved_at = datetime('now') WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} CV/cover letter approved.")