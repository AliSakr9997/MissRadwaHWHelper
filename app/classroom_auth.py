"""Google OAuth for a local-only Desktop client.

- Secrets live in config/client_secret.json (shared by all teacher profiles,
  gitignored, never commit/share). The client identifies the APP, not you.
- Each teacher profile gets its OWN token: data/<profile>/token.json
  (gitignored, auto-refreshed). Run `classroom auth` once per profile.
- Browser opens once on first run; everything stays on this machine.
"""
from __future__ import annotations

from pathlib import Path

from . import profiles

BASE_DIR = Path(__file__).resolve().parent.parent
CLIENT_SECRET_FILE = BASE_DIR / "config" / "client_secret.json"

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.rosters.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.students.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


class AuthError(Exception):
    """Actionable auth failure (shown verbatim to the teacher)."""


def is_configured() -> bool:
    return CLIENT_SECRET_FILE.exists()


def token_path() -> Path:
    profiles.ensure_profile()
    return profiles.token_file()


def get_credentials(open_browser: bool = True):
    """Return valid Credentials, running the localhost OAuth flow if needed.

    Raises AuthError with fix instructions instead of raw oauthlib tracebacks.
    With open_browser=False the URL is printed for manual use (right browser
    profile) instead of auto-opening the default browser.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not is_configured():
        raise AuthError(
            "config/client_secret.json not found — create a Desktop OAuth client in "
            "Google Cloud Console and save the JSON there (see README).")
    token_file = token_path()
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
                creds = flow.run_local_server(port=0, open_browser=open_browser)
        except Exception as e:
            msg = str(e)
            if "scope" in msg.lower():
                raise AuthError(
                    "Google granted different permissions than requested (stale grant "
                    "or an unticked checkbox on the consent screen).\n"
                    "Fix: myaccount.google.com → Security → Third-party access → "
                    "remove this app → run `classroom logout` → `classroom auth` again "
                    "and keep ALL permission checkboxes ticked.") from e
            raise AuthError(f"Google sign-in failed: {msg}") from e
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def logout() -> bool:
    """Delete the local token (forces re-auth next run)."""
    token_file = token_path()
    if token_file.exists():
        token_file.unlink()
        return True
    return False
