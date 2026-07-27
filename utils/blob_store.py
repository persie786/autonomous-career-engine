from database.db_handler import get_connection
from utils.user_context import get_current_user


def init_blob_store():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("CREATE TABLE IF NOT EXISTS blobs (key TEXT PRIMARY KEY, content BLOB, updated_at TEXT DEFAULT (datetime('now')))")
    conn.commit()
    conn.close()


def _scoped(key: str) -> str:
    return f"user_{get_current_user()}::{key}"


def save_blob(key: str, content: bytes):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO blobs (key, content, updated_at) VALUES (?, ?, datetime('now')) "
        "ON CONFLICT(key) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (_scoped(key), content),
    )
    conn.commit()
    conn.close()


def load_blob(key: str) -> bytes | None:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT content FROM blobs WHERE key = ?", (_scoped(key),))
    row = cursor.fetchone()
    conn.close()
    return row[0] if row else None