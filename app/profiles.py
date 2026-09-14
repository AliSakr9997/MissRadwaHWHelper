"""Teacher profiles: isolate each teacher's data on a shared machine.

Usage:
  HW_PROFILE=sara python -m app.server        # server / UI
  python -m app.main --profile sara batch ... # CLI (or HW_PROFILE=sara)

Profile "default" = previous behavior paths... no: every profile, including
"default", lives under data/<profile>/. Shared (same for all teachers):
config/report_rules.json, config/preferences.json, config/client_secret.json
(the OAuth client identifies the APP; each teacher consents with THEIR
Google account and gets their OWN token.json inside their profile).
"""
from __future__ import annotations

import os
import re
import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
GLOBAL_CONFIG = BASE_DIR / "config"
DATA_ROOT = BASE_DIR / "data"
EXAMPLE_ROSTER = GLOBAL_CONFIG / "students.example.json"


def current() -> str:
    name = (os.environ.get("HW_PROFILE", "") or "default").strip() or "default"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise ValueError("bad profile name (use letters/numbers/_/-): %r" % name)
    return name


def set_current(name: str) -> str:
    os.environ["HW_PROFILE"] = current_name(name)
    return os.environ["HW_PROFILE"]


def current_name(name: str) -> str:
    name = (name or "default").strip() or "default"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", name):
        raise ValueError("bad profile name (use letters/numbers/_/-): %r" % name)
    return name


def data_dir(profile: str | None = None) -> Path:
    d = DATA_ROOT / (profile or current())
    return d


def student_file(profile: str | None = None) -> Path:
    return data_dir(profile) / "students.json"


def token_file(profile: str | None = None) -> Path:
    return data_dir(profile) / "token.json"


def homework_dir(profile: str | None = None) -> Path:
    configured = os.environ.get("HW_HOMEWORK_DIR", "").strip()
    if configured and profile in (None, current()):
        return Path(configured).expanduser()
    pref = data_dir(profile) / "preferences.json"
    if pref.exists():
        try:
            value = json.loads(pref.read_text(encoding="utf-8")).get("download_path", "")
            if value:
                return Path(value).expanduser()
        except (OSError, ValueError, TypeError):
            pass
    return data_dir(profile) / "homework"


def ensure_profile(profile: str | None = None) -> Path:
    """Create profile dirs + seed roster from the example template if missing."""
    d = data_dir(profile)
    (d / "homework").mkdir(parents=True, exist_ok=True)
    sf = d / "students.json"
    if not sf.exists():
        if EXAMPLE_ROSTER.exists():
            sf.write_text(EXAMPLE_ROSTER.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            sf.write_text("[]", encoding="utf-8")
    return d
