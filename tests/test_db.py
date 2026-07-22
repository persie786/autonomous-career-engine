import sqlite3
from datetime import datetime, timedelta

from cryptography.fernet import Fernet
import pytest


def test_init_db_creates_tables(temp_db):
    conn = temp_db.get_connection()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert {"jobs", "activity_log", "scrape_stats"}.issubset(tables)


def test_add_job_and_get_jobs(temp_db):
    job_id = temp_db.add_job(
        "Acme", "Software Engineer", "https://example.com/1", "A description"
    )
    jobs = temp_db.get_jobs()
    assert len(jobs) == 1
    assert jobs[0]["id"] == job_id
    assert jobs[0]["status"] == "Pending"  # schema default


def test_duplicate_job_url_rejected(temp_db):
    temp_db.add_job("Acme", "Engineer", "https://example.com/dup", "desc")
    with pytest.raises(sqlite3.IntegrityError):
        temp_db.add_job("Other Co", "Different Role", "https://example.com/dup", "desc")


def test_invalid_status_rejected(temp_db):
    conn = temp_db.get_connection()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO jobs (company, role, job_url, status) VALUES (?, ?, ?, ?)",
            (
                "Acme",
                "Engineer",
                "https://example.com/bad",
                "Aplied",
            ),  # typo — not a real status
        )
    conn.close()


def test_job_exists(temp_db):
    temp_db.add_job("Acme", "Engineer", "https://example.com/2", "desc")
    assert temp_db.job_exists("https://example.com/2") is True
    assert temp_db.job_exists("https://example.com/nonexistent") is False


def test_update_job_status_sets_date_applied(temp_db):
    job_id = temp_db.add_job("Acme", "Engineer", "https://example.com/3", "desc")
    temp_db.update_job_status(job_id, "Applied")
    job = temp_db.get_jobs()[0]
    assert job["status"] == "Applied"
    assert job["date_applied"] is not None


def test_encrypt_decrypt_roundtrip(temp_db):
    ciphertext = temp_db.encrypt_data("sensitive value")
    assert ciphertext != "sensitive value"
    assert temp_db.decrypt_data(ciphertext) == "sensitive value"


def test_decrypt_with_wrong_key_fails(temp_db, monkeypatch):
    ciphertext = temp_db.encrypt_data("sensitive value")
    monkeypatch.setenv(
        "ENCRYPTION_KEY", Fernet.generate_key().decode()
    )  # simulate a lost/changed key
    with pytest.raises(Exception):
        temp_db.decrypt_data(ciphertext)


def test_ghosting_webhook_marks_stale_applied_jobs(temp_db):
    job_id = temp_db.add_job("Acme", "Engineer", "https://example.com/4", "desc")
    temp_db.update_job_status(job_id, "Applied")

    # update_job_status always stamps "now" — backdate directly to simulate a real 25-day-old application.
    stale_date = (datetime.now() - timedelta(days=25)).strftime("%Y-%m-%d %H:%M:%S")
    conn = temp_db.get_connection()
    conn.execute("UPDATE jobs SET date_applied = ? WHERE id = ?", (stale_date, job_id))
    conn.commit()
    conn.close()

    assert temp_db.apply_ghosting_webhook(days_threshold=21) == 1
    assert temp_db.get_jobs()[0]["status"] == "Ghosted"


def test_ghosting_webhook_ignores_recent_applied_jobs(temp_db):
    job_id = temp_db.add_job("Acme", "Engineer", "https://example.com/5", "desc")
    temp_db.update_job_status(job_id, "Applied")  # date_applied = right now

    assert temp_db.apply_ghosting_webhook(days_threshold=21) == 0
    assert temp_db.get_jobs()[0]["status"] == "Applied"


def test_scrape_stats_increment(temp_db):
    assert temp_db.get_scraped_count() == 0
    temp_db.increment_scraped_count(15)
    temp_db.increment_scraped_count(10)
    assert temp_db.get_scraped_count() == 25


def test_consistency_guardrail_no_prior_persona(temp_db):
    assert temp_db.get_persona_for_company("Never Applied Here Inc") is None


def test_consistency_guardrail_reuses_prior_persona(temp_db):
    job_id = temp_db.add_job("Acme", "Engineer", "https://example.com/6", "desc")
    temp_db.save_generated_assets(
        job_id, "backend-focused", "cv text", "cover letter text"
    )
    assert (
        temp_db.get_persona_for_company("acme") == "backend-focused"
    )  # case-insensitive match
