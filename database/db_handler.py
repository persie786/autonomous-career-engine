import sqlite3
import os
import json
import re
from datetime import datetime, timedelta
from dotenv import load_dotenv
from cryptography.fernet import Fernet
import libsql
from utils.logger import setup_logger
from utils.user_context import get_current_user

load_dotenv()
logger = setup_logger("db_handler")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")
TURSO_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_TOKEN = os.getenv("TURSO_AUTH_TOKEN")
_turso_connection_cache = None


class _CompatRow(tuple):
    def __new__(cls, columns, values):
        obj = super().__new__(cls, values)
        obj._columns = columns
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._columns.index(key))
        return tuple.__getitem__(self, key)

    def keys(self):
        return self._columns


class _CompatCursor:
    def __init__(self, raw_cursor):
        self._cursor = raw_cursor

    def execute(self, *args, **kwargs):
        self._cursor.execute(*args, **kwargs)
        return self

    def fetchone(self):
        row = self._cursor.fetchone()
        return self._wrap(row) if row is not None else None

    def fetchall(self):
        return [self._wrap(row) for row in self._cursor.fetchall()]

    def _wrap(self, row):
        columns = [d[0] for d in self._cursor.description]
        return _CompatRow(columns, row)

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def rowcount(self):
        return self._cursor.rowcount


class _CompatConnection:
    def __init__(self, raw_conn):
        self._conn = raw_conn

    def cursor(self):
        return _CompatCursor(self._conn.cursor())

    def commit(self):
        self._conn.commit()

    def close(self):
        pass


def get_connection():
    global _turso_connection_cache
    if TURSO_URL:
        if _turso_connection_cache is None:
            raw = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            _turso_connection_cache = _CompatConnection(raw)
        return _turso_connection_cache
    return sqlite3.connect(DB_PATH)


_db_initialized = False


def init_db():
    global _db_initialized
    if _db_initialized:
        return
    conn = get_connection()
    cursor = conn.cursor()
    if not TURSO_URL:
        cursor.execute(
            "PRAGMA journal_mode=WAL"
        )  # local-file concept only — Turso's Hrana protocol rejects this

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            imap_email TEXT,
            imap_server TEXT DEFAULT 'imap.gmail.com',
            imap_password_encrypted TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            company TEXT NOT NULL,
            role TEXT NOT NULL,
            job_url TEXT,
            job_description TEXT,
            status TEXT NOT NULL DEFAULT 'Pending' CHECK (status IN (
                'Pending', 'Manual Review', 'Not Interested', 'Needs Consultation',
                'Applied', 'Rejected', 'Interview', 'Ghosted', 'Failed - Retry', 'Dead'
            )),
            match_score REAL,
            persona_used TEXT,
            retry_count INTEGER NOT NULL DEFAULT 0,
            date_added TEXT NOT NULL DEFAULT (datetime('now')),
            date_applied TEXT,
            evaluator_reason TEXT,
            generated_cv TEXT,
            generated_cover_letter TEXT,
            cv_approved_at TEXT,
            docx_path TEXT,
            job_source TEXT,
            search_profile TEXT,
            notes TEXT,
            ats_keywords_total INTEGER,
            ats_keywords_matched INTEGER,
            ats_missing_keywords TEXT,
            referral_contact TEXT
        )
    """)
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_user_url ON jobs(user_id, job_url)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(user_id, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(user_id, company)"
    )

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL DEFAULT (datetime('now')),
            module TEXT NOT NULL,
            action_description TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scrape_stats (
            user_id INTEGER PRIMARY KEY,
            total_scraped INTEGER NOT NULL DEFAULT 0
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            stats_json TEXT NOT NULL,
            summary_text TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS blobs (
            key TEXT PRIMARY KEY,
            content BLOB,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    _db_initialized = True
    logger.info("Database initialized.")


def log_activity(module: str, action_description: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO activity_log (user_id, module, action_description) VALUES (?, ?, ?)",
        (get_current_user(), module, action_description),
    )
    conn.commit()
    conn.close()
    logger.info(f"[user={get_current_user()}] [{module}] {action_description}")


def job_exists(job_url: str) -> bool:
    if not job_url:
        return False
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT 1 FROM jobs WHERE user_id = ? AND job_url = ? LIMIT 1",
        (get_current_user(), job_url),
    )
    exists = cursor.fetchone() is not None
    conn.close()
    return exists


def add_job(
    company,
    role,
    job_url,
    job_description,
    match_score=None,
    persona_used=None,
    evaluator_reason=None,
    job_source=None,
    search_profile=None,
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO jobs (user_id, company, role, job_url, job_description, match_score, persona_used,
                          evaluator_reason, job_source, search_profile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            get_current_user(),
            company,
            role,
            job_url,
            job_description,
            match_score,
            persona_used,
            evaluator_reason,
            job_source,
            search_profile,
        ),
    )
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
            "UPDATE jobs SET status = ?, date_applied = datetime('now') WHERE id = ? AND user_id = ?",
            (new_status, job_id, get_current_user()),
        )
    else:
        cursor.execute(
            "UPDATE jobs SET status = ? WHERE id = ? AND user_id = ?",
            (new_status, job_id, get_current_user()),
        )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} status changed to '{new_status}'")


def update_job_notes(job_id: int, notes: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET notes = ? WHERE id = ? AND user_id = ?",
        (notes, job_id, get_current_user()),
    )
    conn.commit()
    conn.close()


def get_jobs(status: str = None) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if status:
        cursor.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND status = ? ORDER BY date_added DESC",
            (get_current_user(), status),
        )
    else:
        cursor.execute(
            "SELECT * FROM jobs WHERE user_id = ? ORDER BY date_added DESC",
            (get_current_user(),),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_job_by_id(job_id: int) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, get_current_user())
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def delete_job(job_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, get_current_user())
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} deleted.")


def get_recent_activity(limit: int = 30, module: str = None) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if module:
        cursor.execute(
            "SELECT * FROM activity_log WHERE user_id = ? AND module = ? ORDER BY timestamp DESC LIMIT ?",
            (get_current_user(), module, limit),
        )
    else:
        cursor.execute(
            "SELECT * FROM activity_log WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?",
            (get_current_user(), limit),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_activity_modules() -> list[str]:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT DISTINCT module FROM activity_log WHERE user_id = ? ORDER BY module ASC",
        (get_current_user(),),
    )
    modules = [row[0] for row in cursor.fetchall()]
    conn.close()
    return modules


def _get_cipher() -> Fernet:
    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise ValueError("ENCRYPTION_KEY not set — check your secrets/.env.")
    return Fernet(key.encode())


def encrypt_data(plaintext: str) -> str:
    return _get_cipher().encrypt(plaintext.encode()).decode()


def decrypt_data(ciphertext: str) -> str:
    return _get_cipher().decrypt(ciphertext.encode()).decode()


def update_user_email_credentials(
    user_id: int, imap_email: str, imap_server: str, imap_password_plain: str = None
):
    conn = get_connection()
    cursor = conn.cursor()
    if imap_password_plain:
        encrypted = encrypt_data(imap_password_plain)
        cursor.execute(
            "UPDATE users SET imap_email = ?, imap_server = ?, imap_password_encrypted = ? WHERE id = ?",
            (imap_email, imap_server, encrypted, user_id),
        )
    else:
        cursor.execute(
            "UPDATE users SET imap_email = ?, imap_server = ? WHERE id = ?",
            (imap_email, imap_server, user_id),
        )
    conn.commit()
    conn.close()


def get_user_email_credentials(user_id: int) -> tuple:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT imap_email, imap_server, imap_password_encrypted FROM users WHERE id = ?",
        (user_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row or not row["imap_password_encrypted"]:
        return None, None, None
    return (
        row["imap_email"],
        row["imap_server"] or "imap.gmail.com",
        decrypt_data(row["imap_password_encrypted"]),
    )


def apply_ghosting_webhook(days_threshold: int = 21) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cutoff = (datetime.now() - timedelta(days=days_threshold)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    cursor.execute(
        """
        UPDATE jobs SET status = 'Ghosted'
        WHERE user_id = ? AND status = 'Applied' AND date_applied IS NOT NULL AND date_applied < ?
    """,
        (get_current_user(), cutoff),
    )
    updated_count = cursor.rowcount
    conn.commit()
    conn.close()
    if updated_count > 0:
        log_activity(
            "db_handler",
            f"Ghosting webhook: {updated_count} job(s) auto-marked as Ghosted.",
        )
    return updated_count


def get_persona_for_company(company: str) -> str | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT persona_used FROM jobs WHERE user_id = ? AND LOWER(company) = LOWER(?) "
        "AND persona_used IS NOT NULL ORDER BY date_added ASC LIMIT 1",
        (get_current_user(), company),
    )
    row = cursor.fetchone()
    conn.close()
    return row["persona_used"] if row else None


def save_generated_assets(
    job_id: int,
    persona_name: str,
    cv_text: str,
    cover_letter_text: str,
    docx_path: str = None,
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET persona_used = ?, generated_cv = ?, generated_cover_letter = ?, docx_path = ? WHERE id = ? AND user_id = ?",
        (
            persona_name,
            cv_text,
            cover_letter_text,
            docx_path,
            job_id,
            get_current_user(),
        ),
    )
    conn.commit()
    conn.close()
    log_activity(
        "db_handler",
        f"Generated CV/cover letter saved for job id={job_id} (persona: {persona_name})",
    )


def approve_assets(job_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET cv_approved_at = datetime('now') WHERE id = ? AND user_id = ?",
        (job_id, get_current_user()),
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} CV/cover letter approved.")


def clear_generated_assets(job_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET generated_cv = NULL, generated_cover_letter = NULL, docx_path = NULL, cv_approved_at = NULL WHERE id = ? AND user_id = ?",
        (job_id, get_current_user()),
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Cleared generated assets for job id={job_id}.")


def get_all_generated_assets() -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM jobs WHERE user_id = ? AND generated_cv IS NOT NULL ORDER BY date_added DESC",
        (get_current_user(),),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_company_history(company: str, exclude_job_id: int = None) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if exclude_job_id:
        cursor.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND LOWER(company) = LOWER(?) AND id != ? ORDER BY date_added ASC",
            (get_current_user(), company, exclude_job_id),
        )
    else:
        cursor.execute(
            "SELECT * FROM jobs WHERE user_id = ? AND LOWER(company) = LOWER(?) ORDER BY date_added ASC",
            (get_current_user(), company),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_referral_contact(job_id: int, contact: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET referral_contact = ? WHERE id = ? AND user_id = ?",
        (contact, job_id, get_current_user()),
    )
    conn.commit()
    conn.close()


def get_referral_contact_for_company(company: str) -> str | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT referral_contact FROM jobs WHERE user_id = ? AND LOWER(company) = LOWER(?) "
        "AND referral_contact IS NOT NULL AND referral_contact != '' LIMIT 1",
        (get_current_user(), company),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None


_TITLE_STOPWORDS = {
    "the",
    "a",
    "an",
    "of",
    "and",
    "or",
    "for",
    "to",
    "in",
    "on",
    "at",
    "is",
    "i",
    "ii",
    "iii",
    "sr",
    "sr.",
    "jr",
    "jr.",
    "senior",
    "junior",
    "remote",
    "hybrid",
    "onsite",
}


def _normalize_title_tokens(title: str) -> set:
    cleaned = re.sub(r"[^\w\s]", " ", title.lower())
    return {t for t in cleaned.split() if t and t not in _TITLE_STOPWORDS}


def find_similar_jobs(
    company: str, role: str, exclude_job_id: int = None, threshold: float = 0.55
) -> list[dict]:
    candidate_tokens = _normalize_title_tokens(role)
    if not candidate_tokens:
        return []
    history = get_company_history(company, exclude_job_id=exclude_job_id)
    matches = []
    for h in history:
        h_tokens = _normalize_title_tokens(h["role"])
        if not h_tokens:
            continue
        union = candidate_tokens | h_tokens
        similarity = len(candidate_tokens & h_tokens) / len(union) if union else 0
        if similarity >= threshold:
            matches.append({**h, "similarity": similarity})
    return sorted(matches, key=lambda x: -x["similarity"])


def update_ats_score(job_id: int, total: int, matched: int, missing_keywords: list):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET ats_keywords_total = ?, ats_keywords_matched = ?, ats_missing_keywords = ? WHERE id = ? AND user_id = ?",
        (total, matched, json.dumps(missing_keywords), job_id, get_current_user()),
    )
    conn.commit()
    conn.close()


def get_skill_gap_summary(top_n: int = 15) -> list:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ats_missing_keywords FROM jobs WHERE user_id = ? AND ats_missing_keywords IS NOT NULL",
        (get_current_user(),),
    )
    rows = cursor.fetchall()
    conn.close()
    counts = {}
    for (raw,) in rows:
        try:
            keywords = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for kw in keywords:
            key = kw.strip().lower()
            if key:
                counts[key] = counts.get(key, 0) + 1
    return sorted(counts.items(), key=lambda x: -x[1])[:top_n]


def get_company_summary() -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT company, COUNT(*) as total_jobs,
               SUM(CASE WHEN date_applied IS NOT NULL THEN 1 ELSE 0 END) as applied_count,
               SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) as interview_count,
               MAX(persona_used) as persona_used, MAX(referral_contact) as referral_contact,
               MAX(date_added) as latest_activity
        FROM jobs WHERE user_id = ? GROUP BY LOWER(company) ORDER BY latest_activity DESC
    """,
        (get_current_user(),),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_profile_performance_summary() -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COALESCE(search_profile, 'Manual / Unknown') as search_profile,
               COUNT(*) as total_jobs,
               SUM(CASE WHEN date_applied IS NOT NULL THEN 1 ELSE 0 END) as applied_count,
               SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) as interview_count,
               ROUND(AVG(match_score), 2) as avg_match_score
        FROM jobs WHERE user_id = ? GROUP BY search_profile ORDER BY total_jobs DESC
    """,
        (get_current_user(),),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_scraped_count() -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT total_scraped FROM scrape_stats WHERE user_id = ?",
        (get_current_user(),),
    )
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else 0


def increment_scraped_count(amount: int):
    if amount <= 0:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO scrape_stats (user_id, total_scraped) VALUES (?, ?) "
        "ON CONFLICT(user_id) DO UPDATE SET total_scraped = total_scraped + excluded.total_scraped",
        (get_current_user(), amount),
    )
    conn.commit()
    conn.close()


def get_jobs_in_period(date_field: str, start: str, end: str) -> list[dict]:
    if date_field not in ("date_added", "date_applied"):
        raise ValueError("Invalid date_field")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM jobs WHERE user_id = ? AND {date_field} BETWEEN ? AND ?",
        (get_current_user(), start, end),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_status_counts() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT status, COUNT(*) FROM jobs WHERE user_id = ? GROUP BY status",
        (get_current_user(),),
    )
    counts = dict(cursor.fetchall())
    conn.close()
    return counts


def save_weekly_report(
    period_start: str, period_end: str, stats: dict, summary_text: str
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO weekly_reports (user_id, period_start, period_end, stats_json, summary_text) VALUES (?, ?, ?, ?, ?)",
        (get_current_user(), period_start, period_end, json.dumps(stats), summary_text),
    )
    conn.commit()
    report_id = cursor.lastrowid
    conn.close()
    log_activity(
        "report_generator",
        f"Weekly report generated for {period_start} to {period_end}",
    )
    return report_id


def get_weekly_reports(limit: int = 10) -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM weekly_reports WHERE user_id = ? ORDER BY generated_at DESC LIMIT ?",
        (get_current_user(), limit),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]
