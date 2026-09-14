"""Missing-HW automation: roster + assignment -> missing list + Arabic messages.

Offline-first: submission status comes from teacher checklists/batch paste
(or Classroom later).

Plan format (WhatsApp bold with **):
  السلام عليكم
  سارة مسلمتش Homework حصة **السبت 5/9** و Homework حصة **الاثنين 7/9**
  **علي Classroom**
Feminine names use مسلمتش. Students with onClassroom=false are excluded
(they get a ⚠️ Not on Classroom report instead).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent

AR_WEEKDAYS = {
    "Monday": "الاثنين", "Tuesday": "الثلاثاء", "Wednesday": "الأربعاء",
    "Thursday": "الخميس", "Friday": "الجمعة", "Saturday": "السبت", "Sunday": "الأحد",
}


def assignment_date(key: str, meta: dict | None = None) -> tuple[int, int, str]:
    """Return (day, month, english_weekday) from meta.json or key prefix YYYY-MM-DD."""
    if meta and meta.get("date"):
        iso = meta["date"]
    else:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", key or "")
        iso = f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else "2026-09-05"
    d = datetime.strptime(iso, "%Y-%m-%d").date()
    dt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=ZoneInfo("Africa/Cairo"))
    return d.day, d.month, dt.strftime("%A")


def assignment_label(key: str, meta: dict | None = None) -> str:
    """E.g. 'Homework حصة **السبت 5/9**' (bold date for WhatsApp)."""
    day, month, en_day = assignment_date(key, meta)
    ar_day = AR_WEEKDAYS.get(en_day, en_day)
    return f"Homework حصة **{ar_day} {day}/{month}**"


def day_title(key: str, meta: dict | None = None) -> str:
    """E.g. 'Saturday 5/9' for section headers."""
    day, month, en_day = assignment_date(key, meta)
    return f"{en_day} {day}/{month}"


def compute_missing(eligible_ids: list[str], submitted_ids: list[str]) -> list[str]:
    submitted = set(submitted_ids or [])
    return [sid for sid in (eligible_ids or []) if sid not in submitted]


def group_by_student(by_assignment: dict[str, list[str]]) -> dict[str, list[str]]:
    """{assignmentKey: [studentIds]} -> {studentId: [assignmentKeys]} (sorted)."""
    grouped: dict[str, list[str]] = {}
    for key in sorted(by_assignment):
        for sid in by_assignment[key]:
            grouped.setdefault(sid, []).append(key)
    return grouped


def arabic_message(ar_first: str, feminine: bool, labels: list[str]) -> str:
    """Build ready-to-send Arabic message."""
    verb = "مسلمتش" if feminine else "مسلمش"
    joined = f" {labels[0]}" if len(labels) == 1 else " " + " و ".join(labels)
    return f"السلام عليكم\n{ar_first} {verb}{joined}\n**علي Classroom**"


def format_missing_list(day_label: str, official_names: list[str]) -> str:
    """E.g. '## Did Not Send HW - Saturday 5/9\\n\\n* Sara Ali\\n...'."""
    lines = [f"## Did Not Send HW - {day_label}", ""]
    lines += [f"* {n}" for n in official_names]
    return "\n".join(lines)


def load_missing_all(homework_dir: Path | None = None) -> dict[str, list[str]]:
    if homework_dir is None:
        from . import profiles
        homework_dir = profiles.homework_dir()
    hw = homework_dir
    out: dict[str, list[str]] = {}
    if hw.exists():
        for base in sorted(p for p in hw.iterdir() if p.is_dir()):
            data_dir = base / "data"
            mf = data_dir / "missing.json" if data_dir.exists() else base / "missing.json"
            if mf.exists():
                try:
                    out[base.name] = json.loads(mf.read_text(encoding="utf-8"))
                except Exception:
                    out[base.name] = []
    return out


def save_missing(assignment_key: str, missing_ids: list[str],
                 homework_dir: Path | None = None) -> Path:
    from . import file_organizer
    dirs = file_organizer.assignment_dirs(assignment_key)
    mf = dirs["data"] / "missing.json"
    mf.write_text(json.dumps(missing_ids, ensure_ascii=False, indent=2), encoding="utf-8")
    return mf


def assignment_meta(assignment_key: str) -> dict:
    # Read-only: must NOT create folders as a side effect.
    from . import profiles
    mf = profiles.homework_dir() / assignment_key / "meta.json"
    if mf.exists():
        try:
            return json.loads(mf.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def build_student_message(student: dict, keys: list[str]) -> str:
    labels = [assignment_label(k, assignment_meta(k)) for k in keys]
    ar_first = student.get("arabicFirst") or (student.get("officialName", "").split()[:1] or ["?"])[0]
    return arabic_message(ar_first, student.get("gender") == "f", labels)
