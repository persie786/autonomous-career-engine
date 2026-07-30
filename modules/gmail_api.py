import os
import json
import base64
import sqlite3
from email.mime.text import MIMEText

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from database.db_handler import get_connection, encrypt_data, decrypt_data
from utils.user_context import get_current_user

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")
REDIRECT_URI = os.getenv("APP_BASE_URL")


def _client_config():
    return {
        "web": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


def get_authorization_url() -> tuple:
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    return flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )


def exchange_code_for_token(code: str) -> dict:
    flow = Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=REDIRECT_URI
    )
    flow.fetch_token(code=code)
    creds = flow.credentials
    return {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
    }


def save_user_gmail_token(user_id: int, token_dict: dict):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET gmail_token_json = ? WHERE id = ?",
        (encrypt_data(json.dumps(token_dict)), user_id),
    )
    conn.commit()
    conn.close()


def get_user_gmail_credentials(user_id: int):
    conn = get_connection()
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT gmail_token_json FROM users WHERE id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if not row or not row["gmail_token_json"]:
        return None

    token_dict = json.loads(decrypt_data(row["gmail_token_json"]))
    creds = Credentials(
        token=token_dict["token"],
        refresh_token=token_dict["refresh_token"],
        token_uri=token_dict["token_uri"],
        client_id=token_dict["client_id"],
        client_secret=token_dict["client_secret"],
        scopes=token_dict["scopes"],
    )
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_dict["token"] = (
            creds.token
        )  # persist the refreshed access token, avoid re-refreshing every call
        save_user_gmail_token(user_id, token_dict)
    return creds


def is_connected(user_id: int) -> bool:
    return get_user_gmail_credentials(user_id) is not None


def disconnect(user_id: int):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET gmail_token_json = NULL WHERE id = ?", (user_id,))
    conn.commit()
    conn.close()


def _get_service():
    creds = get_user_gmail_credentials(get_current_user())
    if creds is None:
        raise ValueError("Gmail not connected yet — connect it in Settings first.")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _header(headers: list, name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _extract_plain_body(payload: dict) -> str:
    if payload.get("mimeType") == "text/plain" and "data" in payload.get("body", {}):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )
    for part in payload.get("parts", []) or []:
        result = _extract_plain_body(part)
        if result:
            return result
    return ""


def _extract_attachments(service, message_id, payload) -> list:
    attachments = []
    for part in payload.get("parts", []) or []:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            att = (
                service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=part["body"]["attachmentId"])
                .execute()
            )
            attachments.append(
                {
                    "filename": part["filename"],
                    "content": base64.urlsafe_b64decode(att["data"]),
                }
            )
        attachments.extend(_extract_attachments(service, message_id, part))
    return attachments


def list_emails(
    limit: int = 10,
    page_token: str = None,
    unread_only: bool = False,
    search_term: str = None,
) -> tuple:
    """Returns (emails, next_page_token). search_term is passed straight through
    as Gmail's own search syntax — 'from:company.com', 'subject:interview', plain
    text, all work exactly like typing them into Gmail's own search box."""
    service = _get_service()
    query_parts = []
    if unread_only:
        query_parts.append("is:unread")
    if search_term:
        query_parts.append(search_term)
    query = " ".join(query_parts) if query_parts else None

    result = (
        service.users()
        .messages()
        .list(
            userId="me",
            maxResults=limit,
            pageToken=page_token,
            q=query,
            labelIds=["INBOX"],
        )
        .execute()
    )

    emails = []
    for ref in result.get("messages", []):
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        headers = msg["payload"].get("headers", [])
        emails.append(
            {
                "uid": msg["id"],
                "thread_id": msg["threadId"],
                "subject": _header(headers, "Subject"),
                "sender": _header(headers, "From"),
                "date": _header(headers, "Date"),
                "body": _extract_plain_body(msg["payload"]),
                "message_id": _header(headers, "Message-ID"),
                "seen": "UNREAD" not in msg.get("labelIds", []),
                "attachments": _extract_attachments(service, msg["id"], msg["payload"]),
            }
        )
    return emails, result.get("nextPageToken")


def set_read_status(message_id: str, seen: bool):
    service = _get_service()
    body = {"removeLabelIds": ["UNREAD"]} if seen else {"addLabelIds": ["UNREAD"]}
    service.users().messages().modify(userId="me", id=message_id, body=body).execute()


def send_email(
    to_address: str,
    subject: str,
    body: str,
    in_reply_to: str = None,
    references: str = None,
    thread_id: str = None,
):
    service = _get_service()
    msg = MIMEText(body)
    msg["To"] = to_address
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = references or in_reply_to

    payload = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        payload["threadId"] = thread_id
    service.users().messages().send(userId="me", body=payload).execute()
