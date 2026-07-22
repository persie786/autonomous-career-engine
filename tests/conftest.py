import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Points db_handler at a throwaway SQLite file and a disposable
    encryption key for the duration of one test. Yields the db_handler
    module itself so tests can call its functions directly."""
    import database.db_handler as db_handler

    monkeypatch.setattr(db_handler, "DB_PATH", str(tmp_path / "test_jobs.db"))
    monkeypatch.setenv("ENCRYPTION_KEY", Fernet.generate_key().decode())

    db_handler.init_db()
    yield db_handler
