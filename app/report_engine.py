"""Report engine: teacher summary -> standardized HW report.

Rules live in config/report_rules.json (changeable later).
Roster lives in config/students.json (local file wins).
Engine must NEVER alter numbers: grade/mistakes/skipped pass through verbatim.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = BASE_DIR / "config"

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def load_rules(path: Path | None = None) -> dict:
    p = path or (CONFIG_DIR / "report_rules.json")
    return json.loads(p.read_text(encoding="utf-8"))


def load_students(path: Path | None = None) -> list[dict]:
    if path is None:
        from . import roster
        return roster.load()
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_student(query: str, students: list[dict]) -> tuple[dict | None, bool]:
    """Return (student, needs_review). Local file wins; fuzzy fallback flags review."""
    q = (query or "").strip()
    if not q:
        return None, True
    ql = q.lower()
    for s in students:
        if ql == s.get("officialName", "").lower() or ql == s.get("id", "").lower():
            return s, False
    for s in students:
        if ql in [v.lower() for v in s.get("variants", [])]:
            return s, False
    hits = [s for s in students if ql in s.get("officialName", "").lower()]
    if len(hits) == 1:
        return hits[0], False
    return None, True  # ambiguous or unknown


def eligible_ids(students: list[dict]) -> list[str]:
    """Active AND on Classroom — the only students who can 'miss' HW."""
    return [s["id"] for s in students
            if s.get("active", True) and s.get("onClassroom", True)]


def mistakes_label(n: int) -> str:
    if n <= 0:
        return "Mistakes (none) 🟢"
    return f"Mistakes ({n}) {'🟢' if n <= 4 else '🔴'}"


def skipped_label(n: int) -> str:
    if n <= 0:
        return "Skipped questions (none) 🟢"
    if 1 <= n <= 2:
        return f"Skipped questions ({n})"
    return f"Skipped questions ({n}) 🔴"


def understanding_emoji(understanding: str, grade_num: int, grade_den: int) -> str:
    u = (understanding or "").strip().lower()
    if u.startswith("excellent") and grade_num == grade_den:
        return "⭐"
    if u.startswith("excellent") or u.startswith("very good"):
        return "🟢"
    if u.startswith("poor"):
        return "🔴"
    return ""


def submission_label(ontime: bool) -> str:
    return "HW sent ontime 🟢" if ontime else "HW sent late 🔴"


def weekday_name(iso_date: str, tz: str | None = None) -> str:
    """iso_date 'YYYY-MM-DD' -> 'Saturday'. Raises ValueError on bad input."""
    if tz is None:
        tz = load_rules().get("timezone", "Africa/Cairo")
    d = datetime.strptime(iso_date, "%Y-%m-%d").date()
    dt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=ZoneInfo(tz))
    return dt.strftime("%A")


def validate(grade_num: int, grade_den: int, mistakes: int, skipped: int) -> None:
    if grade_den <= 0:
        raise ValueError("grade_den must be > 0")
    if not (0 <= grade_num <= grade_den):
        raise ValueError("grade_num must satisfy 0 <= num <= den")
    if mistakes < 0 or skipped < 0:
        raise ValueError("mistakes/skipped must be >= 0")


def resolve_understanding(understanding: str | None, grade_num: int, grade_den: int,
                          rules: dict | None = None) -> str | None:
    """Given understanding (or missing) -> display value, or None to omit the line.

    Rule: if teacher gave it, keep it. If missing and grade ratio is failing,
    infer 'poor'. Otherwise omit the line.
    """
    u = (understanding or "").strip()
    if u:
        return u
    rules = rules or load_rules()
    cfg = rules.get("rules", {}).get("understanding_infer", {})
    threshold = float(cfg.get("below_ratio", 0.5))
    if grade_den > 0 and (grade_num / grade_den) < threshold:
        return str(cfg.get("label", "poor"))
    return None


def normalize_note(note: str, rules: dict | None = None) -> str:
    fixed = re.sub(r"\s+", " ", (note or "").strip())
    if not fixed:
        return ""
    # Common teacher shorthand -> standard tokens (deterministic, no invention)
    fixed = re.sub(r"\bhw\b", "HW", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bpdf\b", "PDF", fixed, flags=re.IGNORECASE)
    fixed = re.sub(r"\bclassroom\b", "Classroom", fixed, flags=re.IGNORECASE)
    # Teacher-curated phrase fixes from report_rules.json (extendable)
    rules = rules if rules is not None else load_rules()
    for pair in rules.get("rules", {}).get("note_fixes", []):
        find, repl = pair.get("find", ""), pair.get("replace", "")
        if find:
            fixed = re.sub(re.escape(find), repl, fixed, flags=re.IGNORECASE)
    if not fixed.endswith("."):
        fixed += "."
    return fixed[0].upper() + fixed[1:]


def format_report(
    *,
    official_name: str,
    year: str,
    day: int,
    month: int,
    weekday: str,
    ontime: bool,
    mistakes: int,
    skipped: int,
    understanding: str | None,
    grade_num: int,
    grade_den: int,
    note: str = "",
    rules: dict | None = None,
) -> str:
    """Build the final report text. Numbers pass through untouched."""
    validate(grade_num, grade_den, mistakes, skipped)
    rules = rules or load_rules()
    fmt = rules.get("formatting", {})
    header = fmt.get("header", "📝HW Report")
    note_header = fmt.get("note_header", "‼️NOTE‼️")
    u = resolve_understanding(understanding, grade_num, grade_den, rules)
    lines = [
        header,
        f"{official_name} ({year} {day}/{month} {weekday})",
        submission_label(ontime),
        mistakes_label(mistakes),
        skipped_label(skipped),
    ]
    if u is not None:
        u = u.lower()
        lines.append(f"Understanding ({u}) {understanding_emoji(u, grade_num, grade_den)}".rstrip())
    lines.append(f"Grade: {grade_num}/{grade_den}")
    fixed_note = normalize_note(note, rules)
    if fixed_note:
        lines += ["", note_header, fixed_note]
    return "\n".join(lines)


def format_status_report(*, official_name: str, year: str, day: int, month: int,
                         weekday: str, status: str,
                         rules: dict | None = None) -> str:
    """❌ Did not send HW / ⚠️ Not on Classroom blocks."""
    label = "❌ Did not send HW" if status == "missing" else "⚠️ Not on Classroom"
    header = (rules or load_rules()).get("formatting", {}).get("header", "📝HW Report")
    return f"{header}\n{official_name} ({year} {day}/{month} {weekday})\n{label}"


# ---------- parsing ----------

_GRADE_RE = re.compile(r"(\d+)\s*/\s*(\d+)")
_MISSING_RE = re.compile(r"didn'?t\s+send|did\s+not\s+send|\bnot\s+sent\b|\bmissing\b", re.IGNORECASE)
_NOTON_RE = re.compile(r"not\s+on\s+classroom|not\s+in\s+classroom|not\s+on\s+class", re.IGNORECASE)
_LATE_RE = re.compile(r"\blate\b", re.IGNORECASE)
_DAY_HEADER_RE = re.compile(
    r"^(saturday|sunday|monday|tuesday|wednesday|thursday|friday)\b.*?(\d{1,2}/\d{1,2})?\s*$",
    re.IGNORECASE)
_DASHES_RE = re.compile(r"^-{3,}\s*$")


def _strip_status_phrase(line: str) -> str:
    """'Lina didn't send' -> 'Lina'. Removes the status phrase to recover the name."""
    cut = re.split(r"didn'?t\s+send|did\s+not\s+send|\bnot\s+sent\b"
                   r"|not\s+on\s+classroom|not\s+in\s+classroom", line, flags=re.IGNORECASE)[0]
    return cut.strip(" -–—")


def parse_entry(block: str) -> dict | None:
    """Parse one student's block -> entry dict.

    type: 'report' | 'missing' | 'not_on_classroom'
    report keys: student_query, grade_num/den, mistakes, skipped,
                 understanding ('' when absent), note, late (bool)
    """
    lines = [ln.strip() for ln in (block or "").splitlines() if ln.strip()]
    if not lines:
        return None
    first = lines[0]
    if _NOTON_RE.search(block):
        return {"type": "not_on_classroom", "student_query": _strip_status_phrase(first)}
    if _MISSING_RE.search(block):
        # 'Name\nDidn't send' or single-line "Lina didn't send"
        return {"type": "missing", "student_query": _strip_status_phrase(first)}
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    out: dict = {"type": "report", "student_query": first, "grade_num": None, "grade_den": None,
                 "mistakes": 0, "skipped": 0, "understanding": "", "note": "",
                 "late": bool(_LATE_RE.search(block))}
    m = _GRADE_RE.search(body)
    if m:
        out["grade_num"], out["grade_den"] = int(m.group(1)), int(m.group(2))
    if re.search(r"\bno\s+mistakes?\b", body, re.IGNORECASE):
        out["mistakes"] = 0
    else:
        m = re.search(r"(\d+)\s+mistakes?|mistakes?\s*[:\-]?\s*(\d+)", body, re.IGNORECASE)
        if m:
            out["mistakes"] = int(m.group(1) or m.group(2))
    m = re.search(r"(\d+)\s+questions?\s+skipped", body, re.IGNORECASE)
    if not m:
        m = re.search(r"skips?(?:ped)?\s*\(\s*(\d+)", body, re.IGNORECASE)
    if not m:
        m = re.search(r"skips?(?:ped)?\s*[:\-]?\s*(\d+)|(\d+)\s+skips?\b", body, re.IGNORECASE)
        if m:
            out["skipped"] = int(m.group(1) or m.group(2))
    else:
        out["skipped"] = int(m.group(1))
    for cand in ("excellent", "very good", "good", "weak", "poor"):
        hit = re.search(cand, body, re.IGNORECASE)
        if hit:
            out["understanding"] = hit.group(0)
            break
    note_m = re.search(r"note\s*:?\s*(.+)", block, re.IGNORECASE | re.DOTALL)
    if note_m:
        out["note"] = note_m.group(1).strip()
    return out


def parse_free_text(text: str) -> dict:
    """Single-entry parse (backwards compatible)."""
    entry = parse_entry(text) or {}
    if entry.get("type") != "report":
        return {"type": entry.get("type", "report"), "student_query": entry.get("student_query", ""),
                "grade_num": None, "grade_den": None, "mistakes": 0, "skipped": 0,
                "understanding": "", "note": "", "late": False}
    return entry


def _name_set(students: list[dict]) -> set[str]:
    names: set[str] = set()
    for s in students or []:
        names.add(s.get("officialName", "").lower())
        names.add(s.get("id", "").lower())
        for v in s.get("variants", []):
            names.add(v.lower())
    names.discard("")
    return names


def _entry_start(line: str, names: set[str]) -> bool:
    """Does this line open a new student entry? A roster name (known or not),
    optionally followed by an inline status ("Lina Haddad didn't send").

    Unknown names still open entries (flagged Needs Review downstream) when the
    status-stripped remainder is short, digit-free name text. Bare status lines
    ('Didn't send' following a name line) continue the current entry.
    """
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.lower() in names:
        return True
    if _MISSING_RE.search(stripped) or _NOTON_RE.search(stripped):
        rest = _strip_status_phrase(stripped)
        if not rest:
            return False
        if rest.lower() in names:
            return True
        words = rest.split()
        if len(words) <= 4 and not re.search(r"\d|/", rest):
            return True
    return False


def parse_batch(text: str, students: list[dict] | None = None) -> list[dict]:
    """Split a multi-student paste into day sections (name-anchored).

    A new entry opens at any line that is a roster name (variants included),
    optionally with inline status. Blank lines are insignificant, so notes
    separated by blank lines stay attached to their entry.
    Returns [{header: str|None, entries: [entry...]}].
    """
    names = _name_set(students or load_students())
    sections: list[dict] = [{"header": None, "blocks": []}]
    current: list[str] = []

    def flush():
        if current:
            entry = parse_entry("\n".join(current))
            if entry:
                sections[-1]["blocks"].append(entry)
        current.clear()

    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _DASHES_RE.match(line):
            flush()
            # New section only when the current one has entries; a bare
            # header (e.g. '----- Monday HW -----' lines) stays with the
            # entries that follow it.
            if sections[-1]["blocks"]:
                sections.append({"header": None, "blocks": []})
            continue
        if _DAY_HEADER_RE.match(line) and _GRADE_RE.search(line) is None \
                and not _MISSING_RE.search(line) and not _NOTON_RE.search(line):
            flush()
            if sections[-1]["blocks"] or sections[-1]["header"]:
                sections.append({"header": line, "blocks": []})
            else:
                sections[-1]["header"] = line
            continue
        if _entry_start(line, names):
            flush()
            current.append(line)
            continue
        current.append(line)
    flush()
    out = []
    for s in sections:
        if s["blocks"] or s["header"]:
            out.append({"header": s["header"], "entries": s["blocks"]})
    return out
