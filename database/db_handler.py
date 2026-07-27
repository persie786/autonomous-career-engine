import sqlite3
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from utils.logger import setup_logger
import json
import re
import libsql

TURSO_URL = os.getenv("libsql://career-engine-persie786.aws-ap-northeast-1.turso.io")
TURSO_TOKEN = os.getenv(
    "eyJhbGciOiJFZERTQSIsInR5cCI6IkpXVCJ9.eyJpYXQiOjE3ODUxMjg5MzYsImlkIjoiMDE5ZmExZjktMWYwMS03ZmIxLTg3OGYtMzc0ODIwN2I0ODdjIiwia2lkIjoiTGhZdzFSMlpRU0ZQaC1xS0pBT0ozbmFzZzZ4MGVsTTg1Uk1FUGtxRUVUZyIsInJpZCI6IjZmY2Q4MTM1LTRmYWUtNDMyMy05NTdhLTA5ZDAxMTZiYTNiOCJ9.oZ5OyUkEf12t_Uj8lxKzNzG--igMF4D36FiuoC1O8sfgKD5RGN3S0np1jcnFH_luFFRAzoFZB3vIIHtTipXwDA"
)

_turso_connection_cache = None


class _CompatRow(tuple):
    """Makes a plain libsql row behave exactly like sqlite3.Row — every
    existing function in this file does row['status'], dict(row), or
    row[0] somewhere, and libsql only gives you the last one natively."""

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
        pass  # a cached Turso connection stays open for the app's life — see get_connection()


load_dotenv()
logger = setup_logger("db_handler")

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "jobs.db")


def get_connection():
    """
    Local dev (no TURSO_DATABASE_URL set): identical to before — a fresh
    sqlite3 connection to the local jobs.db file, no Turso account needed.
    Deployed (TURSO_DATABASE_URL set): a single cached connection to your
    persistent Turso database, reused across calls rather than reconnecting
    every time — each "connection" is now a real network round-trip, so
    treating it like a free local file handle would be needlessly slow.
    """
    global _turso_connection_cache
    if TURSO_URL:
        if _turso_connection_cache is None:
            raw = libsql.connect(database=TURSO_URL, auth_token=TURSO_TOKEN)
            _turso_connection_cache = _CompatConnection(raw)
        return _turso_connection_cache
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
        "job_source": "TEXT",  # which site JobSpy found this on
        "search_profile": "TEXT",  # which profile ran the search that found it
        "docx_path": "TEXT",
        "ats_keywords_total": "INTEGER",
        "ats_keywords_matched": "INTEGER",
        "ats_missing_keywords": "TEXT",
        "referral_contact": "TEXT",
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

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period_start TEXT NOT NULL,
            period_end TEXT NOT NULL,
            generated_at TEXT NOT NULL DEFAULT (datetime('now')),
            stats_json TEXT NOT NULL,
            summary_text TEXT
        )
    """)

    cursor.execute(
        "INSERT OR IGNORE INTO scrape_stats (id, total_scraped) VALUES (1, 0)"
    )

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
    cursor.execute(
        "UPDATE scrape_stats SET total_scraped = total_scraped + ? WHERE id = 1",
        (amount,),
    )
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
        INSERT INTO jobs (company, role, job_url, job_description, match_score, persona_used,
                          evaluator_reason, job_source, search_profile)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
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


def update_job_notes(job_id: int, notes: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE jobs SET notes = ? WHERE id = ?", (notes, job_id))
    conn.commit()
    conn.close()


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
        cursor.execute(
            "SELECT * FROM jobs WHERE status = ? ORDER BY date_added DESC", (status,)
        )
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
        cursor.execute(
            "SELECT * FROM activity_log WHERE module = ? ORDER BY timestamp DESC LIMIT ?",
            (module, limit),
        )
    else:
        cursor.execute(
            "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?", (limit,)
        )
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
    cutoff = (datetime.now() - timedelta(days=days_threshold)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    cursor.execute(
        """
        UPDATE jobs
        SET status = 'Ghosted'
        WHERE status = 'Applied' AND date_applied IS NOT NULL AND date_applied < ?
    """,
        (cutoff,),
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
        "UPDATE jobs SET persona_used = ?, generated_cv = ?, generated_cover_letter = ?, docx_path = ? WHERE id = ?",
        (persona_name, cv_text, cover_letter_text, docx_path, job_id),
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
        "UPDATE jobs SET cv_approved_at = datetime('now') WHERE id = ?", (job_id,)
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} CV/cover letter approved.")


def get_company_history(company: str, exclude_job_id: int = None) -> list[dict]:
    """All prior jobs at this company — what the Studio shows you before
    you generate, so the Guardrail's decision is visible, not just enforced."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if exclude_job_id:
        cursor.execute(
            "SELECT * FROM jobs WHERE LOWER(company) = LOWER(?) AND id != ? ORDER BY date_added ASC",
            (company, exclude_job_id),
        )
    else:
        cursor.execute(
            "SELECT * FROM jobs WHERE LOWER(company) = LOWER(?) ORDER BY date_added ASC",
            (company,),
        )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_jobs_in_period(date_field: str, start: str, end: str) -> list[dict]:
    if date_field not in (
        "date_added",
        "date_applied",
    ):  # whitelisted — never interpolate raw column names
        raise ValueError("Invalid date_field")
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        f"SELECT * FROM jobs WHERE {date_field} BETWEEN ? AND ?", (start, end)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_status_counts() -> dict:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, COUNT(*) FROM jobs GROUP BY status")
    counts = dict(cursor.fetchall())
    conn.close()
    return counts


def save_weekly_report(
    period_start: str, period_end: str, stats: dict, summary_text: str
) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO weekly_reports (period_start, period_end, stats_json, summary_text) VALUES (?, ?, ?, ?)",
        (period_start, period_end, json.dumps(stats), summary_text),
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
        "SELECT * FROM weekly_reports ORDER BY generated_at DESC LIMIT ?", (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def update_ats_score(
    job_id: int, total: int, matched: int, missing_keywords: list[str]
):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET ats_keywords_total = ?, ats_keywords_matched = ?, ats_missing_keywords = ? WHERE id = ?",
        (total, matched, json.dumps(missing_keywords), job_id),
    )
    conn.commit()
    conn.close()


def update_referral_contact(job_id: int, contact: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET referral_contact = ? WHERE id = ?", (contact, job_id)
    )
    conn.commit()
    conn.close()


def get_referral_contact_for_company(company: str) -> str | None:
    """Mirrors get_persona_for_company's lookup pattern — if you've already
    recorded a contact at this company, new jobs there can surface it."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT referral_contact FROM jobs WHERE LOWER(company) = LOWER(?) "
        "AND referral_contact IS NOT NULL AND referral_contact != '' LIMIT 1",
        (company,),
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
    """
    Token-overlap similarity against every prior job at the same company —
    deliberately not an AI call. This needs to be explainable (which words
    overlapped) and fast enough to run on every manual add and every Studio
    card, and a plain Jaccard score does that without another API round-trip.
    """
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


def get_skill_gap_summary(top_n: int = 15) -> list[tuple]:
    """Tallies every keyword ever flagged missing across every generated CV —
    this is what turns one job's ATS score into a pattern worth acting on."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT ats_missing_keywords FROM jobs WHERE ats_missing_keywords IS NOT NULL"
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
    cursor.execute("""
        SELECT company, COUNT(*) as total_jobs,
               SUM(CASE WHEN date_applied IS NOT NULL THEN 1 ELSE 0 END) as applied_count,
               SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) as interview_count,
               MAX(persona_used) as persona_used, MAX(referral_contact) as referral_contact,
               MAX(date_added) as latest_activity
        FROM jobs GROUP BY LOWER(company) ORDER BY latest_activity DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_profile_performance_summary() -> list[dict]:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT COALESCE(search_profile, 'Manual / Unknown') as search_profile,
               COUNT(*) as total_jobs,
               SUM(CASE WHEN date_applied IS NOT NULL THEN 1 ELSE 0 END) as applied_count,
               SUM(CASE WHEN status = 'Interview' THEN 1 ELSE 0 END) as interview_count,
               ROUND(AVG(match_score), 2) as avg_match_score
        FROM jobs GROUP BY search_profile ORDER BY total_jobs DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def delete_job(job_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Job id={job_id} deleted.")


def clear_generated_assets(job_id: int):
    """Resets a job back to 'ready to generate' — used by the CV library's delete action."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE jobs SET generated_cv = NULL, generated_cover_letter = NULL, docx_path = NULL, cv_approved_at = NULL WHERE id = ?",
        (job_id,),
    )
    conn.commit()
    conn.close()
    log_activity("db_handler", f"Cleared generated assets for job id={job_id}.")


def get_all_generated_assets() -> list[dict]:
    """Every job with a generated CV, regardless of status — unlike the Studio's
    three working sections (which only ever query status='Pending'), this needs
    to include jobs that have since moved to Applied/Interview/etc., or an
    applied job's CV becomes invisible the moment it leaves Pending."""
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM jobs WHERE generated_cv IS NOT NULL ORDER BY date_added DESC"
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_job_by_id(job_id: int) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None
