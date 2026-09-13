"""Roster helpers: local students.json wins. Simple load/save/resolve."""
from __future__ import annotations

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STUDENTS_PATH = BASE_DIR / "config" / "students.json"
EXAMPLE_PATH = BASE_DIR / "config" / "students.example.json"


def load(path: Path | None = None) -> list[dict]:
    p = path or STUDENTS_PATH
    if not p.exists() and EXAMPLE_PATH.exists():
        # First run: start from the (fictional) template. Real names are
        # added by the teacher via the Roster UI and never committed.
        p.write_text(EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save(students: list[dict], path: Path | None = None) -> None:
    p = path or STUDENTS_PATH
    p.write_text(json.dumps(students, ensure_ascii=False, indent=2), encoding="utf-8")


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
