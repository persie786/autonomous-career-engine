import sqlite3
import bcrypt
from database.db_handler import get_connection


def create_user(email: str, password: str) -> int:
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
    if cursor.fetchone():
        conn.close()
        raise ValueError("An account with this email already exists.")

    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
    cursor.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)", (email, password_hash))
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return user_id


def verify_login(email: str, password: str) -> dict | None:
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if row is None:
        return None
    row = dict(row)
    if not bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
        return None
    return row