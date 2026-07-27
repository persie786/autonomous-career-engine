import os
import json
from utils.blob_store import save_blob, load_blob

USE_REMOTE_STORAGE = bool(os.getenv("TURSO_DATABASE_URL"))


def read_json(local_path: str, blob_key: str, default: dict) -> dict:
    if USE_REMOTE_STORAGE:
        raw = load_blob(blob_key)
        if raw is None:
            write_json(local_path, blob_key, default)
            return default.copy()
        return json.loads(raw.decode())
    if not os.path.exists(local_path):
        write_json(local_path, blob_key, default)
        return default.copy()
    with open(local_path, "r") as f:
        return json.load(f)


def write_json(local_path: str, blob_key: str, data):
    if USE_REMOTE_STORAGE:
        save_blob(blob_key, json.dumps(data).encode())
        return
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "w") as f:
        json.dump(data, f, indent=2)


def read_binary(local_path: str, blob_key: str) -> bytes | None:
    if USE_REMOTE_STORAGE:
        return load_blob(blob_key)
    if not os.path.exists(local_path):
        return None
    with open(local_path, "rb") as f:
        return f.read()


def write_binary(local_path: str, blob_key: str, content: bytes):
    if USE_REMOTE_STORAGE:
        save_blob(blob_key, content)
        return
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(content)


def binary_exists(local_path: str, blob_key: str) -> bool:
    if USE_REMOTE_STORAGE:
        return load_blob(blob_key) is not None
    return os.path.exists(local_path)
