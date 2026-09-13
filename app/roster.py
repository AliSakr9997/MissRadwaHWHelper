"""Roster helpers: per-teacher students.json wins. Simple load/save/resolve."""
from __future__ import annotations

import json
from pathlib import Path

from . import profiles


def load(path: Path | None = None) -> list[dict]:
    if path is None:
        profiles.ensure_profile()
        path = profiles.student_file()
    if not path.exists() and profiles.EXAMPLE_ROSTER.exists():
        # First run: start from the (fictional) template. Real names are
        # added by the teacher via the Roster UI and never committed.
        path.write_text(profiles.EXAMPLE_ROSTER.read_text(encoding="utf-8"),
                        encoding="utf-8")
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def save(students: list[dict], path: Path | None = None) -> None:
    if path is None:
        profiles.ensure_profile()
        path = profiles.student_file()
    path.write_text(json.dumps(students, ensure_ascii=False, indent=2), encoding="utf-8")


def active_only(students: list[dict]) -> list[dict]:
    return [s for s in students if s.get("active", True)]


def upsert(students: list[dict], entry: dict) -> list[dict]:
    """Insert or update by id. Returns the same list (mutated)."""
    for i, s in enumerate(students):
        if s.get("id") == entry.get("id"):
            students[i] = entry
            return students
    students.append(entry)
    return students
