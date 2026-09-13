"""AI report pipeline: free teacher text -> provider -> structured JSON ->
application validation -> deterministic rendering.

Division of labor (by design):
  AI: understand wording, match names, extract numbers/notes/dates/statuses.
  APP: roster validation, grade bounds, calendar/weekday, missing logic,
       Arabic messages, final formatting. The AI never formats the report
       and never invents data: anything ambiguous becomes Needs Review.
"""
from __future__ import annotations

import re
from datetime import datetime

from . import ai_providers, batch, report_engine

SCHEMA_DOC = """{
  "entries": [
    {"student": "<official roster name, copied exactly>",
     "status": "submitted | missing | not_on_classroom",
     "late": false,
     "grade_earned": 46, "grade_total": 54,
     "mistakes": 8, "skipped": 0,
     "understanding": "excellent | very good | good | weak | poor | null",
     "note": "<teacher note or empty>",
     "date": "YYYY-MM-DD or null",
     "needs_review": false, "review_reason": ""}
  ]
}"""


def build_prompt(roster: list[dict], year: str, default_dates: list[str]) -> tuple[str, str]:
    names = []
    for s in roster:
        if not s.get("active", True):
            continue
        names.append(f"- {s['officialName']} (also written as: "
                     f"{', '.join(s.get('variants', []) or ['—'])})")
    system = f"""You interpret a teacher's homework log into structured JSON.
Roster (match every name to EXACTLY one of these official names):
{chr(10).join(names)}

Rules you must obey:
- Copy numbers EXACTLY as written (grades, mistakes, skipped). Never invent,
  round, or "correct" them. "46/54" stays 46 and 54.
- "didn't send / did not send / not sent" -> status missing (no grade fields).
- "not on classroom" -> status not_on_classroom.
- "late / done late" -> late true, otherwise false.
- "no mistakes" -> mistakes 0. "X questions skipped" / "skipped (X)" -> skipped X.
- Understanding words map to: excellent, very good, good, weak, poor. If the
  teacher gives none, use null (the app decides).
- Notes: copy the teacher's note text; empty string if none.
- Dates: homework sections may be headed ("Saturday HW 5/9", "Monday HW").
  Resolve each entry's date to YYYY-MM-DD using year {year} when determinable,
  else null. NEVER compute weekday names.
- If anything is ambiguous (unknown name, missing grade on a submitted entry,
  unclear date), set needs_review true with a short review_reason — never guess.
- Reply with JSON ONLY, exactly this shape:
{SCHEMA_DOC}"""
    user_intro = ("Homework log to interpret"
                  + (f" (default date(s) if none determinable: {', '.join(default_dates)})"
                     if default_dates else "")
                  + f", class year {year}:")
    return system, user_intro


def interpret(text: str, provider_name: str, model: str, api_key: str,
              base_url: str = "") -> dict:
    """Call the provider and return the raw structured dict (validated shape)."""
    provider = ai_providers.create(provider_name, api_key, base_url)
    roster = report_engine.load_students()
    system, _ = build_prompt(roster, "Y8", [])
    raw = provider.chat_json(system, text, model)
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ai_providers.ProviderError("model returned no entries list")
    return {"entries": entries}


def _coerce_date(value, default_dates: list[str], year: str = "Y8") -> str | None:
    if not value:
        return default_dates[0] if len(default_dates) == 1 else None
    v = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        try:
            datetime.strptime(v, "%Y-%m-%d")
            return v
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?", v)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), m.group(3)
        try:
            datetime(int(y) if y else datetime.now().year, mo, d)
            return f"{int(y) if y else datetime.now().year}-{mo:02d}-{d:02d}"
        except ValueError:
            return None
    return None


def to_batch_payload(ai_data: dict, students: list[dict], year: str = "Y8",
                     default_dates: list[str] | None = None,
                     rules: dict | None = None) -> dict:
    """Validate AI entries and convert to the deterministic batch payload.

    Anything failing validation becomes Needs Review (never silently fixed).
    """
    default_dates = default_dates or []
    sections: dict[str, list[dict]] = {}
    order: list[str] = []

    for e in ai_data.get("entries", []):
        if not isinstance(e, dict):
            continue
        status = str(e.get("status", "submitted")).lower()
        query = str(e.get("student", "") or "")
        student, needs_review = report_engine.resolve_student(query, students)
        official = student["officialName"] if student else query.strip()
        if e.get("needs_review") or not student or needs_review:
            reason = str(e.get("review_reason") or "unmatched name")
            # still resolve best-effort for display; batch flags review
            entry = {"type": "report" if status == "submitted" else
                     ("missing" if status == "missing" else "not_on_classroom"),
                     "student_query": query or official,
                     "grade_num": e.get("grade_earned"), "grade_den": e.get("grade_total"),
                     "mistakes": e.get("mistakes", 0) or 0,
                     "skipped": e.get("skipped", 0) or 0,
                     "understanding": e.get("understanding") or "",
                     "note": e.get("note") or "", "late": bool(e.get("late", False)),
                     "_force_review": f"{official or query}: {reason}"}
        elif status in ("missing", "not_on_classroom"):
            entry = {"type": status, "student_query": official}
        else:
            gn, gd = e.get("grade_earned"), e.get("grade_total")
            entry = {"type": "report", "student_query": official,
                     "grade_num": gn, "grade_den": gd,
                     "mistakes": e.get("mistakes", 0) or 0,
                     "skipped": e.get("skipped", 0) or 0,
                     "understanding": e.get("understanding") or "",
                     "note": e.get("note") or "", "late": bool(e.get("late", False))}
            try:
                report_engine.validate(int(gn), int(gd),
                                       int(entry["mistakes"]), int(entry["skipped"]))
            except (TypeError, ValueError):
                entry["_force_review"] = f"{official}: bad numbers {gn}/{gd}"
        iso = _coerce_date(e.get("date"), default_dates, year)
        if iso is None and default_dates:
            # multiple sections but AI gave no date: use position? No — review.
            entry["_force_review"] = (entry.get("_force_review") or
                                      f"{official or query}: unclear date")
            iso = default_dates[0]
        if iso is None:
            entry["_force_review"] = (entry.get("_force_review") or
                                      f"{official or query}: unclear date")
            iso = "0000-00-00"
        if iso not in sections:
            sections[iso] = []
            order.append(iso)
        sections[iso].append(entry)

    batch_sections = [{"header": None, "entries": sections[k]} for k in order]
    dates = [k if k != "0000-00-00" else "2026-09-05" for k in order]
    payload = batch.resolve_batch(batch_sections, dates, students, year=year, rules=rules)
    # propagate AI-side review flags into the payload
    for d in payload["days"]:
        for r in d["reports"]:
            fr = (r.get("entry") or {}).get("_force_review")
            if fr and not r.get("needsReview"):
                r["needsReview"] = True
    payload["needsReview"] = [r for d in payload["days"] for r in d["reports"]
                              if r.get("needsReview") or r.get("error")]
    return payload
