"""Google OAuth for a local-only Desktop client.

- Secrets live in config/client_secret.json (gitignored, never commit/share).
- User token lives in config/token.json (gitignored, auto-refreshed).
- Browser opens once on first run; everything stays on this machine.
"""
from __future__ import annotations

from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRET_FILE = BASE_DIR / "config" / "client_secret.json"
TOKEN_FILE = BASE_DIR / "config" / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def is_configured() -> bool:
    return CLIENT_SECRET_FILE.exists()


def get_credentials():
    """Return valid Credentials, running the localhost OAuth flow if needed."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not is_configured():
        raise FileNotFoundError(
            "config/client_secret.json not found — create a Desktop OAuth client in "
            "Google Cloud Console and save the JSON there (see README).")
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def logout() -> bool:
    """Delete the local token (forces re-auth next run)."""
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
        return True
    return False
