"""Batch flow: teacher pastes a multi-student, multi-day log -> full output document.

Output: per-day report blocks (sorted) + per-day missing lists + Arabic messages.
"""
from __future__ import annotations

from . import missing, report_engine


def resolve_batch(sections: list[dict], iso_dates: list[str], students: list[dict],
                  year: str = "Y8", rules: dict | None = None) -> dict:
    """Resolve every entry against the roster. Returns save-ready payload + review flags."""
    rules = rules or report_engine.load_rules()
    by_id = {s["id"]: s for s in students}
    days = []
    for i, sec in enumerate(sections):
        if i >= len(iso_dates):
            raise ValueError(f"section {i + 1} has no date — pass --dates for every day section")
        iso = iso_dates[i]
        y, m, d = iso.split("-")
        weekday = report_engine.weekday_name(iso)
        day, month = int(d), int(m)
        resolved = []
        for e in sec["entries"]:
            student, needs_review = report_engine.resolve_student(e.get("student_query", ""), students)
            official = student["officialName"] if student else (e.get("student_query") or "?").strip()
            sid = student["id"] if student else None
            item = {"type": e["type"], "studentId": sid, "officialName": official,
                    "needsReview": needs_review, "entry": e}
            if e["type"] == "report":
                if e.get("grade_num") is None:
                    item["error"] = "no grade found"
                    item["text"] = ""
                else:
                    item["text"] = report_engine.format_report(
                        official_name=official, year=year, day=day, month=month, weekday=weekday,
                        ontime=not e.get("late", False), mistakes=e.get("mistakes", 0),
                        skipped=e.get("skipped", 0), understanding=e.get("understanding") or None,
                        grade_num=e["grade_num"], grade_den=e["grade_den"],
                        note=e.get("note", ""), rules=rules)
            else:
                status = "missing" if e["type"] == "missing" else "not_on_classroom"
                item["text"] = report_engine.format_status_report(
                    official_name=official, year=year, day=day, month=month,
                    weekday=weekday, status=status)
            resolved.append(item)
        # Roster completes the day: an active student with no entry at all is
        # treated as missing (or not-on-classroom when onClassroom=false).
        mentioned = {r["studentId"] for r in resolved if r["studentId"]}
        for s in students:
            if not s.get("active", True) or s["id"] in mentioned:
                continue
            status = "missing" if s.get("onClassroom", True) else "not_on_classroom"
            resolved.append({
                "type": status, "studentId": s["id"], "officialName": s["officialName"],
                "needsReview": False, "implicit": True, "entry": {},
                "text": report_engine.format_status_report(
                    official_name=s["officialName"], year=year, day=day, month=month,
                    weekday=weekday, status=status),
            })
        resolved.sort(key=lambda r: r["officialName"].lower())
        key = f"{iso}_hw"
        miss_ids = [r["studentId"] for r in resolved
                    if r["type"] == "missing" and r["studentId"]
                    and r["studentId"] in report_engine.eligible_ids(students)]
        days.append({"key": key, "iso_date": iso, "title": f"{weekday} {day}/{month}",
                     "reports": resolved, "missingIds": miss_ids})
    # cross-day grouping for Arabic messages (eligible students only)
    by_assignment = {d["key"]: d["missingIds"] for d in days}
    grouped = missing.group_by_student(by_assignment)
    messages = []
    for sid in sorted(grouped, key=lambda s: by_id.get(s, {}).get("officialName", s).lower()):
        st = by_id.get(sid, {"officialName": sid})
        messages.append({"studentId": sid, "officialName": st.get("officialName", sid),
                         "assignments": grouped[sid],
                         "message": missing.build_student_message(st, grouped[sid])})
    return {"days": days, "messages": messages,
            "needsReview": [r for d in days for r in d["reports"] if r["needsReview"] or r.get("error")]}


def render_output(payload: dict, students: list[dict]) -> str:
    """Render the full copy-friendly document."""
    by_id = {s["id"]: s for s in students}
    parts: list[str] = []
    for d in payload["days"]:
        parts.append(f"### {d['title']}")
        parts.append("")
        for r in d["reports"]:
            parts.append(r["text"])
            parts.append("")
        # strip trailing blank, add separator handling below
        if parts and parts[-1] == "":
            parts.pop()
        parts.append("")
    for d in payload["days"]:
        names = sorted([by_id.get(sid, {}).get("officialName", sid) for sid in d["missingIds"]],
                       key=str.lower)
        if names:
            parts.append(missing.format_missing_list(d["title"], names))
            parts.append("")
    if payload["messages"]:
        parts.append("---\n")
        parts.append("## Arabic Missing-HW Messages\n")
        for m in payload["messages"]:
            parts.append(m["message"])
            parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def save_payload(payload: dict) -> None:
    """Persist reports + missing per assignment (versioning kept by server/CLI)."""
    import json
    from . import file_organizer
    for d in payload["days"]:
        base = file_organizer.assignment_dirs(d["key"])["base"]
        (base / "meta.json").write_text(json.dumps({"date": d["iso_date"]}, ensure_ascii=False),
                                        encoding="utf-8")
        rf = base / "reports.json"
        try:
            reports = json.loads(rf.read_text(encoding="utf-8"))
        except Exception:
            reports = []
        for r in d["reports"]:
            if r.get("error"):
                continue
            sid = r["studentId"] or r["officialName"]
            ver = sum(1 for x in reports if x.get("studentId") == sid) + 1
            reports.append({"studentId": sid, "officialName": r["officialName"],
                            "assignmentKey": d["key"], "version": ver,
                            "finalReport": r["text"], "status": "approved"})
        rf.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        missing.save_missing(d["key"], d["missingIds"])
