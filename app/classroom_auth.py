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


def scopes_path() -> Path:
    profiles.ensure_profile()
    return profiles.data_dir() / "granted_scopes.json"


def _load_granted_scopes() -> list[str]:
    p = scopes_path()
    if p.exists():
        try:
            import json as _json
            scopes = _json.loads(p.read_text(encoding="utf-8"))
            if isinstance(scopes, list) and scopes:
                return scopes
        except Exception:
            pass
    return SCOPES


def _auth_port() -> int:
    """Loopback port, 0 = ephemeral. Override with HW_AUTH_PORT if needed."""
    import os as _os
    try:
        return int(_os.environ.get("HW_AUTH_PORT", "0") or 0)
    except ValueError:
        return 0


def _loopback_authorize(client_secret: str, scopes: list[str],
                        open_browser: bool, timeout: int = 180) -> tuple[str, list[str]]:
    """Run a loopback OAuth flow tolerant to Google substituting scopes.

    Returns (authorization code, granted scopes, redirect uri).
    Raises AuthError on denial/timeout.
    """
    import urllib.parse as _up
    import webbrowser as _wb
    import wsgiref.simple_server as _wsgi
    from google_auth_oauthlib.flow import InstalledAppFlow

    captured: dict = {}

    def _app(environ, start_response):
        captured["query"] = environ.get("QUERY_STRING", "")
        body = ("<html><body style='font-family:sans-serif'>"
                "<h3>The authentication flow has completed. You may close this window.</h3>"
                "</body></html>").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html"),
                                  ("Content-Length", str(len(body)))])
        return [body]

    server = _wsgi.make_server("127.0.0.1", _auth_port(), _app)
    port = server.server_port
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, scopes)
    flow.redirect_uri = f"http://127.0.0.1:{port}/"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print("Visit this URL to authorize (teacher Google account, ALL boxes ticked):")
    print(auth_url)
    if open_browser:
        try:
            _wb.open(auth_url)
        except Exception:
            pass
    server.timeout = timeout
    import time as _time
    deadline = _time.time() + timeout
    try:
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                raise AuthError(
                    "Timed out waiting for the browser callback "
                    f"({timeout}s). Re-run auth and complete the Allow step.")
            server.timeout = remaining
            try:
                server.handle_request()
            except Exception:
                raise AuthError(
                    "Timed out waiting for the browser callback "
                    f"({timeout}s). Re-run auth and complete the Allow step.")
            # Ignore stray probes with no query (AV scanners, favicon, ...).
            if captured.get("query"):
                break
    finally:
        server.server_close()
    q = _up.parse_qs(captured.get("query", ""))
    if q.get("error"):
        raise AuthError("Google refused access: %s. Re-run auth and Allow." % q["error"][0])
    code = (q.get("code") or [None])[0]
    if not code:
        raise AuthError("No authorization code arrived. Re-run auth and complete Allow.")
    granted = (q.get("scope") or [""])[0].split() or list(scopes)
    return code, granted, f"http://127.0.0.1:{port}/"


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
        creds = Credentials.from_authorized_user_file(str(token_file), _load_granted_scopes())
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                code, granted, redirect_uri = _loopback_authorize(
                    str(CLIENT_SECRET_FILE), SCOPES, open_browser)
                # Exchange with the GRANTED set: Google may substitute an
                # equivalent scope server-side (observed: coursework.students
                # -> student-submissions.students). Exchanging with exactly
                # what was granted always validates.
                exchange = InstalledAppFlow.from_client_secrets_file(
                    str(CLIENT_SECRET_FILE), granted)
                exchange.redirect_uri = redirect_uri
                creds = exchange.fetch_token(code=code)
                import json as _json
                scopes_path().write_text(_json.dumps(granted), encoding="utf-8")
        except AuthError:
            raise
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
