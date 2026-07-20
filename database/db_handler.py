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
    """Opens a fresh connection to the SQLite database."""
    return sqlite3.connect(DB_PATH)


def init_db():
    """Creates the jobs and activity_log tables if they don't already exist."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            job_url TEXT,
            job_description TEXT,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN (
                'Pending', 'Manual Review', 'Needs Consultation', 'Applied',
                'Rejected', 'Interview', 'Ghosted', 'Failed - Retry', 'Dead'
            )),
            match_score REAL,
            persona_used TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL DEFAULT (datetime('now')),
            date_applied TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            module TEXT NOT NULL,
            action_description TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()
    logger.info("Database initialized (jobs, activity_log tables ready).")


def log_activity(module: str, action_description: str):
    """Writes one row to activity_log — this feeds the Dashboard's live audit trail."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO activity_log (module, action_description) VALUES (?, ?)",
        (module, action_description),
    )
    conn.commit()
    conn.close()
    logger.info(f"[{module}] {action_description}")


def add_job(company, role, job_url, job_description, match_score=None, persona_used=None) -> int:
    """Inserts a new job with status 'Pending'. Returns the new row's id."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO jobs (company, role, job_url, job_description, match_score, persona_used)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (company, role, job_url, job_description, match_score, persona_used))
    conn.commit()
    job_id = cursor.lastrowid
    conn.close()
    log_activity("db_handler", f"Added job: {role} at {company} (id={job_id})")
    return job_id


def update_job_status(job_id: int, new_status: str):
    """Updates a job's status. Moving to 'Applied' also stamps date_applied."""
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
    """Returns all jobs, or jobs filtered by status if provided."""
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


def get_recent_activity(limit: int = 20) -> list[dict]:
    """Returns the most recent activity_log entries, newest first."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def _get_cipher() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY not set — check your .env file.")
    return Fernet(key.encode())


def encrypt_data(plaintext: str) -> str:
    """Encrypts a string for storage. Returns ciphertext as a plain string."""
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt_data(ciphertext: str) -> str:
    """Decrypts a ciphertext string back to plaintext."""
    return _get_cipher().decrypt(ciphertext.encode()).decode()


def apply_ghosting_webhook(days_threshold: int = 21) -> int:
    """
    Call once at startup. Any job still 'Applied' more than `days_threshold`
    days after date_applied is auto-updated to 'Ghosted'. Returns count updated.
    """
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

    test_id = add_job(
        company="Test Co",
        role="Software Engineer",
        job_url="https://example.com/job/123",
        job_description="A sample job for testing.",
        match_score=0.87,
        persona_used="default",
    )
    print(f"Inserted test job with id={test_id}")
    print("Current jobs:", get_jobs())

    encrypted = encrypt_data("this is a secret")
    print("Encrypted:", encrypted)
    print("Decrypted:", decrypt_data(encrypted))

    print("Recent activity:", get_recent_activity())