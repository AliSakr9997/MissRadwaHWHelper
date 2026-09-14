"""Google OAuth for a local-only Desktop client.

- Secrets live in config/client_secret.json (shared by all teacher profiles,
  gitignored, never commit/share). The client identifies the APP, not you.
- Each teacher profile gets its OWN token: data/<profile>/token.json
  (gitignored, auto-refreshed). Run `classroom auth` once per profile.
- Browser opens once on first run; everything stays on this machine.
"""
from __future__ import annotations

import threading
from pathlib import Path
import re

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


def setup_status() -> dict:
    """Return safe setup diagnostics without exposing OAuth credentials."""
    status = {
        "clientSecret": False,
        "clientSecretValid": False,
        "clientType": "",
        "scopes": len(SCOPES),
    }
    if not CLIENT_SECRET_FILE.exists():
        return status
    status["clientSecret"] = True
    try:
        import json as _json
        payload = _json.loads(CLIENT_SECRET_FILE.read_text(encoding="utf-8"))
        client_type = next((key for key in ("installed", "web") if key in payload), "")
        client = payload.get(client_type) if client_type else None
        status["clientType"] = client_type
        status["clientSecretValid"] = bool(
            isinstance(client, dict)
            and client.get("client_id")
            and client.get("client_secret")
            and client.get("auth_uri")
            and client.get("token_uri")
        )
    except (OSError, ValueError, TypeError):
        pass
    return status


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


_auth_state = {"url": None, "error": None, "done": False, "lock": threading.Lock()}


def start_auth_background(timeout: int = 180) -> str:
    """Start the OAuth loopback in a background thread, return the auth URL.

    The thread waits for Google's callback, exchanges the code, and saves
    the token. Call check_auth_status() to poll for completion.
    """
    import urllib.parse as _up
    import wsgiref.simple_server as _wsgi
    import os
    from google_auth_oauthlib.flow import InstalledAppFlow

    with _auth_state["lock"]:
        _auth_state["url"] = None
        _auth_state["error"] = None
        _auth_state["done"] = False

    captured: dict = {}

    def _app(environ, start_response):
        captured["query"] = environ.get("QUERY_STRING", "")
        app_url = os.environ.get("HW_APP_URL", "http://127.0.0.1:8000")
        body = (f"""<!doctype html><html><head><meta charset="utf-8">
<title>Classroom HW Helper</title>
<style>
:root{{--paper:#f5f0e8;--ink:#121212;--red:#d02020;--blue:#1040c0;--yellow:#f2c230}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
background:var(--blue);color:#fff;font-family:Arial,sans-serif;text-align:center;padding:24px}}
.card{{width:min(620px,100%);background:var(--paper);color:var(--ink);border:4px solid var(--ink);
box-shadow:10px 10px 0 var(--ink);padding:42px 30px}}
h1{{margin:0 0 18px;font-size:clamp(2.2rem,8vw,4.8rem);line-height:.86;text-transform:uppercase;
letter-spacing:-.08em}}p{{font-size:1.05rem;line-height:1.5}}
</style></head><body>
<div class="card"><h1>Classroom<br>HW Helper</h1>
<p><b>Google sign-in completed.</b><br>Saving credentials, then returning to the app...</p></div>
<script>setTimeout(function(){{window.location.replace('{app_url}');}},2000);</script>
</body></html>""").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html"),
                                  ("Content-Length", str(len(body)))])
        return [body]

    server = _wsgi.make_server("127.0.0.1", _auth_port(), _app)
    port = server.server_port
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET_FILE), SCOPES)
    flow.redirect_uri = f"http://127.0.0.1:{port}/"
    auth_url, _ = flow.authorization_url(
        prompt="select_account consent", access_type="offline")

    with _auth_state["lock"]:
        _auth_state["url"] = auth_url

    def _run():
        try:
            server.timeout = timeout
            import time as _time
            deadline = _time.time() + timeout
            while True:
                remaining = deadline - _time.time()
                if remaining <= 0:
                    with _auth_state["lock"]:
                        _auth_state["error"] = "Timed out waiting for Google callback"
                        _auth_state["done"] = True
                    break
                server.timeout = remaining
                try:
                    server.handle_request()
                except Exception:
                    continue
                if captured.get("query"):
                    break
            q = _up.parse_qs(captured.get("query", ""))
            if q.get("error"):
                with _auth_state["lock"]:
                    _auth_state["error"] = "Google refused access: %s" % q["error"][0]
                    _auth_state["done"] = True
            elif not (q.get("code") or [None])[0]:
                with _auth_state["lock"]:
                    _auth_state["error"] = "No authorization code received"
                    _auth_state["done"] = True
            else:
                code = q["code"][0]
                granted = (q.get("scope") or [""])[0].split() or list(SCOPES)
                flow.oauth2session.scope = granted
                flow.fetch_token(code=code)
                creds = flow.credentials
                token_file = token_path()
                token_file.write_text(creds.to_json(), encoding="utf-8")
                scopes_path().write_text(
                    __import__("json").dumps(granted), encoding="utf-8")
                _assign_google_profile(creds, token_file)
                with _auth_state["lock"]:
                    _auth_state["done"] = True
        except Exception as e:
            with _auth_state["lock"]:
                _auth_state["error"] = str(e)
                _auth_state["done"] = True
        finally:
            server.server_close()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return auth_url


def check_auth_status() -> dict:
    """Return {done: bool, error: str|None, url: str|None}."""
    with _auth_state["lock"]:
        return {"done": _auth_state["done"],
                "error": _auth_state["error"],
                "url": _auth_state["url"]}


def _loopback_authorize(client_secret: str, scopes: list[str],
                        open_browser: bool, timeout: int = 180):
    """Run a loopback OAuth flow tolerant to Google substituting scopes.

    Returns (flow, authorization code, granted scopes).
    Raises AuthError on denial/timeout.
    """
    import urllib.parse as _up
    import webbrowser as _wb
    import wsgiref.simple_server as _wsgi
    import os
    from google_auth_oauthlib.flow import InstalledAppFlow

    captured: dict = {}

    def _app(environ, start_response):
        captured["query"] = environ.get("QUERY_STRING", "")
        app_url = os.environ.get("HW_APP_URL", "http://127.0.0.1:8000")
        body = (f"""<!doctype html><html><head><meta charset="utf-8">
<title>Classroom HW Helper</title>
<style>
:root{{--paper:#f5f0e8;--ink:#121212;--red:#d02020;--blue:#1040c0;--yellow:#f2c230}}
*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;display:grid;place-items:center;
background:var(--blue);color:#fff;font-family:Arial,sans-serif;text-align:center;padding:24px}}
.card{{width:min(620px,100%);background:var(--paper);color:var(--ink);border:4px solid var(--ink);
box-shadow:10px 10px 0 var(--ink);padding:42px 30px}}
h1{{margin:0 0 18px;font-size:clamp(2.2rem,8vw,4.8rem);line-height:.86;text-transform:uppercase;
letter-spacing:-.08em}}p{{font-size:1.05rem;line-height:1.5}}
</style></head><body>
<div class="card"><h1>Classroom<br>HW Helper</h1>
<p><b>Google sign-in completed.</b><br>Redirecting to the app...</p></div>
<script>window.location.replace({app_url!r});</script>
</body></html>""").encode("utf-8")
        start_response("200 OK", [("Content-Type", "text/html"),
                                  ("Content-Length", str(len(body)))])
        return [body]

    server = _wsgi.make_server("127.0.0.1", _auth_port(), _app)
    port = server.server_port
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, scopes)
    flow.redirect_uri = f"http://127.0.0.1:{port}/"
    auth_url, _ = flow.authorization_url(
        prompt="select_account consent", access_type="offline")
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
    return flow, code, granted


def get_credentials(open_browser: bool = True, force_reauth: bool = False):
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

    # If a background auth flow is already running, wait for it instead of
    # starting a second flow (which would open a duplicate browser tab).
    with _auth_state["lock"]:
        auth_in_progress = _auth_state["url"] and not _auth_state["done"]
    if auth_in_progress:
        import time as _time
        deadline = _time.time() + 15
        while _time.time() < deadline:
            with _auth_state["lock"]:
                if _auth_state["done"]:
                    break
            _time.sleep(0.3)
        # Re-read token after waiting — the background thread should have saved it.
        token_file = token_path()
        if token_file.exists():
            creds = Credentials.from_authorized_user_file(
                str(token_file), _load_granted_scopes())
            if creds and creds.valid:
                _assign_google_profile(creds, token_file)
                return creds

    token_file = token_path()
    if force_reauth:
        # A valid cached token otherwise bypasses Google completely. The
        # explicit sign-in action must allow the teacher to switch accounts.
        token_file.unlink(missing_ok=True)
        scopes_path().unlink(missing_ok=True)
    creds = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), _load_granted_scopes())
    if not creds or not creds.valid:
        try:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow, code, granted = _loopback_authorize(
                    str(CLIENT_SECRET_FILE), SCOPES, open_browser)
                # Exchange on the SAME flow (it holds the PKCE verifier) but
                # validate against the GRANTED set: Google may substitute an
                # equivalent scope server-side (observed: coursework.students
                # -> student-submissions.students).
                flow.oauth2session.scope = granted
                flow.fetch_token(code=code)
                creds = flow.credentials
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
    _assign_google_profile(creds, token_file)
    return creds


def _assign_google_profile(creds, token_file: Path) -> None:
    """Name the local profile after the authenticated Google account."""
    import base64 as _base64
    import json as _json
    import shutil as _shutil
    import urllib.parse as _urlparse
    import urllib.request as _urlrequest
    email = ""
    raw = getattr(creds, "id_token", None)
    if raw and isinstance(raw, str) and raw.count(".") == 2:
        try:
            body = raw.split(".")[1] + "==="
            claims = _json.loads(_base64.urlsafe_b64decode(body))
            email = str(claims.get("email", ""))
        except (ValueError, TypeError, _json.JSONDecodeError):
            pass
    if not email and getattr(creds, "token", None):
        try:
            query = _urlparse.urlencode({"access_token": creds.token})
            with _urlrequest.urlopen(
                    "https://oauth2.googleapis.com/tokeninfo?" + query,
                    timeout=10) as response:
                claims = _json.loads(response.read().decode("utf-8"))
            email = str(claims.get("email", ""))
        except (OSError, ValueError, TypeError, _json.JSONDecodeError):
            pass
    if not email:
        try:
            from googleapiclient.discovery import build
            classroom = build("classroom", "v1", credentials=creds,
                              cache_discovery=False)
            email = str(classroom.userProfiles().get(userId="me").execute()
                        .get("emailAddress", ""))
        except Exception:
            pass
    if not email:
        return
    local = email.split("@", 1)[0].lower()
    local = re.sub(r"[^a-z0-9_-]+", "-", local).strip("-")[:40] or "teacher"
    if local == profiles.current():
        (profiles.data_dir() / "account_email.txt").write_text(email, encoding="utf-8")
        return
    old = profiles.data_dir()
    new = profiles.data_dir(local)
    profiles.ensure_profile(local)
    _shutil.copy2(token_file, profiles.token_file(local))
    scopes = old / "granted_scopes.json"
    if scopes.exists():
        _shutil.copy2(scopes, new / "granted_scopes.json")
    (new / "account_email.txt").write_text(email, encoding="utf-8")
    import os as _os
    _os.environ["HW_PROFILE"] = local


def logout() -> bool:
    """Delete the local token (forces re-auth next run)."""
    token_file = token_path()
    if token_file.exists():
        token_file.unlink()
        return True
    return False
