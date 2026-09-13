"""Offline CLI: preview a report or organize files without the browser.

Examples:
  python -m app.main report --student "Sara Ali" --num 46 --den 54 --mistakes 8 --skipped 0 --understanding "very good" --date 2026-09-05 --year Y8 --ontime --note "Study again the fraction flipping in dividing"
  python -m app.main report --free-text-file note.txt --date 2026-09-05
  python -m app.main organize --assignment 2026-09-05_hw1 --student "Sara Ali" --pdf original_scan.pdf
  python -m app.main organize --assignment 2026-09-05_hw1 --student "Sara Ali" --images IMG_1021.jpg IMG_1022.jpg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import batch, file_organizer, missing, report_engine, roster


def cmd_report(args: argparse.Namespace) -> int:
    students = roster.active_only(roster.load())
    rules = report_engine.load_rules()
    if args.free_text_file:
        text = Path(args.free_text_file).read_text(encoding="utf-8")
        parsed = report_engine.parse_free_text(text)
        query = parsed["student_query"]
        num, den = parsed["grade_num"], parsed["grade_den"]
        mistakes, skipped = parsed["mistakes"], parsed["skipped"]
        understanding, note = parsed["understanding"], parsed["note"]
    else:
        query, num, den = args.student, args.num, args.den
        mistakes, skipped = args.mistakes, args.skipped
        understanding, note = args.understanding, args.note
    if num is None or den is None:
        print("error: grade num/den required (or free text containing N/M)", file=sys.stderr)
        return 2
    student, needs_review = report_engine.resolve_student(query, students)
    official = student["officialName"] if student else query
    d = args.date.split("-")
    report = report_engine.format_report(
        official_name=official, year=args.year, day=int(d[2]), month=int(d[1]),
        weekday=report_engine.weekday_name(args.date), ontime=not args.late,
        mistakes=mistakes, skipped=skipped, understanding=understanding,
        grade_num=num, grade_den=den, note=note, rules=rules)
    print(report)
    if needs_review:
        print("\n[Needs Review: name did not match roster exactly]", file=sys.stderr)
        return 3
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    dirs = file_organizer.assignment_dirs(args.assignment)
    if args.pdf:
        dest = file_organizer.normalize_pdf(Path(args.pdf), dirs["normalized"], args.student)
        print(f"PDF -> {dest}")
    if args.images:
        dest = file_organizer.normalize_images([Path(p) for p in args.images],
                                               dirs["normalized"], args.student)
        print(f"images -> {dest}")
    for link in args.link or []:
        file_organizer.record_link(args.assignment, args.student, link)
        print(f"link recorded -> links.txt")
    return 0


def cmd_missing(args: argparse.Namespace) -> int:
    students = roster.load()
    by_id = {s["id"]: s for s in students}
    if args.compute:
        miss = missing.compute_missing(report_engine.eligible_ids(students), args.submitted or [])
        missing.save_missing(args.assignment, miss)
        print(f"missing ({len(miss)}): {', '.join(miss) if miss else 'none'}")
        return 0
    by_assignment = missing.load_missing_all()
    if args.assignment:
        by_assignment = {args.assignment: by_assignment.get(args.assignment, [])}
    grouped = missing.group_by_student(by_assignment)
    if not grouped:
        print("no missing homework")
        return 0
    for sid, keys in grouped.items():
        st = by_id.get(sid, {"officialName": sid})
        print(f"--- {st.get('officialName', sid)} ---")
        print(missing.build_student_message(st, keys))
        print()
    return 0


def cmd_batch(args: argparse.Namespace) -> int:
    text = Path(args.file).read_text(encoding="utf-8")
    students = roster.load()
    sections = report_engine.parse_batch(text, students)
    dates = [d.strip() for d in (args.dates or "").split(",") if d.strip()]
    try:
        payload = batch.resolve_batch(sections, dates, students, year=args.year)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    out = batch.render_output(payload, students)
    if args.save:
        batch.save_payload(payload)
    if args.out:
        Path(args.out).write_text(out, encoding="utf-8")
    else:
        print(out, end="")
    if payload["needsReview"]:
        names = sorted({r["officialName"] for r in payload["needsReview"]})
        print(f"\n[Needs Review: {', '.join(names)}]", file=sys.stderr)
        return 3
    return 0


def cmd_classroom(args: argparse.Namespace) -> int:
    from . import classroom, classroom_auth
    try:
        if args.action == "auth":
            classroom_auth.get_credentials(
                open_browser=not getattr(args, "no_browser", False))
            print("authenticated — token saved in your profile folder (gitignored)")
            return 0
        if args.action == "logout":
            print("logged out" if classroom_auth.logout() else "no local token")
            return 0
        creds = classroom_auth.get_credentials()
    except Exception as e:
        import traceback as _tb
        from . import profiles as _prof
        try:
            log = _prof.data_dir() / "auth-debug.log"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(_tb.format_exc(), encoding="utf-8")
            print(f"(full traceback saved to {log} — never share it, it may "
                  f"contain secrets)", file=sys.stderr)
        except Exception:
            pass
        print(f"error: {e}", file=sys.stderr)
        return 2
    if args.action == "courses":
        courses = classroom.list_courses(creds)
        if not courses:
            print("no ACTIVE courses where you are teacher. "
                  "Run `classroom diagnose` to see what this account can access.")
            return 0
        for c in courses:
            print(f"{c['id']}  {c.get('name', '')}  [{c.get('courseState', '')}]")
        return 0
    if args.action == "diagnose":
        import json as _json
        d = classroom.diagnose(creds, args.course)
        for p in d["probes"]:
            if p["ok"]:
                print(f"[ok] {p['label']}: {p['count']}")
                for s in p["sample"]:
                    print(f"     - {s['id']}  {s['name']}  [{s['state']}]")
            else:
                print(f"[ERROR] {p['label']}: {p['error']}")
        return 0
    if args.action == "summary":
        if not args.course:
            print("error: summary needs --course COURSE_ID", file=sys.stderr)
            return 2
        s = classroom.summarize_course(creds, args.course)
        print(f"course {s['courseId']} — roster {s['rosterSize']}")
        for a in s["assignments"]:
            print(f"- {a['title']} (due {a['due']}, max {a['maxPoints']}): "
                  f"submitted {a['submitted']}, missing {a['missing']}, late {a['late']}")
        return 0
    if args.action == "reconcile":
        from . import roster as _roster
        if not args.course:
            print("error: reconcile needs --course COURSE_ID", file=sys.stderr)
            return 2
        students = _roster.load()
        rec = classroom.reconcile(creds, args.course, students)
        print(f"matched {len(rec['matched'])}, needs-review {len(rec['review'])}, "
              f"not-in-classroom {len(rec['unlisted'])}")
        for m in rec["matched"]:
            print(f"  = {m['studentId']}  <-  {m['name']}")
        for r in rec["review"]:
            print(f"  ? {r['name']} (uid {r['userId']})")
        for u in rec["unlisted"]:
            print(f"  - {u} not found in Classroom roster")
        if getattr(args, "apply", False):
            classroom.apply_reconcile(students, rec["matched"])
            _roster.save(students)
            print(f"saved classroomId for {len(rec['matched'])} students")
        else:
            print("preview only — re-run with --apply to write classroomIds")
        return 0
    if args.action == "fetch":
        from . import missing as _missing, roster as _roster2
        for flag in ("course", "work", "assignment"):
            if not getattr(args, flag, None):
                print(f"error: fetch needs --course, --work and --assignment",
                      file=sys.stderr)
                return 2
        students = _roster2.load()
        st = classroom.fetch_status(creds, args.course, args.work, students)
        _missing.save_missing(args.assignment, st["missingIds"])
        by_id = {s["id"]: s.get("officialName", s["id"]) for s in students}
        print(f"submitted {len(st['submittedIds'])}, missing {len(st['missingIds'])} "
              f"(empty turn-in {len(st['emptyIds'])}), late {len(st['lateIds'])}, "
              f"unmapped {len(st['reviewIds'])}")
        for sid in st["missingIds"]:
            tag = " (turned in EMPTY)" if sid in st["emptyIds"] else ""
            print(f"  - {by_id.get(sid, sid)}{tag}")
        for uid in st["reviewIds"]:
            print(f"  ? unmapped classroom uid {uid} (reconcile --apply first)")
        return 0
    if args.action == "download":
        if not args.course or not args.work or not args.assignment:
            print("error: download needs --course, --work and --assignment",
                  file=sys.stderr)
            return 2
        man = classroom.download_assignment(creds, args.course, args.work,
                                            args.assignment)
        n_files = sum(1 for s in man["submissions"]
                      for a in s["attachments"] if a["kind"] in ("file", "exported-pdf"))
        print(f"downloaded {n_files} files from {len(man['submissions'])} "
              f"submissions into {args.assignment}/original/")
        for s in man["submissions"]:
            for a in s["attachments"]:
                if a["kind"] == "error":
                    print(f"  ! {a['file']}: {a.get('error', '')}")
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hwhelper", description="MissRadwaHWHelper offline CLI")
    p.add_argument("--profile", default=None,
                   help="teacher profile (isolated roster/token/homework, e.g. --profile sara)")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="preview a HW report")
    r.add_argument("--student", default="")
    r.add_argument("--num", type=int, default=None)
    r.add_argument("--den", type=int, default=None)
    r.add_argument("--mistakes", type=int, default=0)
    r.add_argument("--skipped", type=int, default=0)
    r.add_argument("--understanding", default="")
    r.add_argument("--note", default="")
    r.add_argument("--date", default="2026-09-05")
    r.add_argument("--year", default="Y8")
    r.add_argument("--late", action="store_true")
    r.add_argument("--ontime", action="store_true")
    r.add_argument("--free-text-file", default=None)
    r.set_defaults(func=cmd_report)
    o = sub.add_parser("organize", help="normalize submission files")
    o.add_argument("--assignment", required=True)
    o.add_argument("--student", required=True)
    o.add_argument("--pdf", default=None)
    o.add_argument("--images", nargs="*", default=None)
    o.add_argument("--link", action="append", default=None)
    o.set_defaults(func=cmd_organize)
    m = sub.add_parser("missing", help="compute missing lists + Arabic messages")
    m.add_argument("--assignment", default=None)
    m.add_argument("--compute", action="store_true",
                   help="compute + save missing for --assignment from --submitted ids")
    m.add_argument("--submitted", nargs="*", default=None)
    m.set_defaults(func=cmd_missing)
    b = sub.add_parser("batch", help="paste multi-student log -> full output document")
    b.add_argument("--file", required=True, help="text file with the pasted log")
    b.add_argument("--dates", required=True, help="comma ISO dates per day section, e.g. 2026-09-05,2026-09-07")
    b.add_argument("--year", default="Y8")
    b.add_argument("--save", action="store_true", help="persist reports.json + missing.json")
    b.add_argument("--out", default=None, help="write output document to file")
    b.set_defaults(func=cmd_batch)
    c = sub.add_parser("classroom", help="Google Classroom read-only access")
    c.add_argument("action", choices=["auth", "logout", "courses", "summary", "diagnose",
                                       "reconcile", "fetch", "download"])
    c.add_argument("--course", default=None, help="course id")
    c.add_argument("--work", default=None, help="courseWork id (fetch/download)")
    c.add_argument("--assignment", default=None,
                   help="local assignment key, e.g. 2026-09-05_hw (fetch/download)")
    c.add_argument("--apply", action="store_true",
                   help="reconcile: write classroomIds into the roster")
    c.add_argument("--no-browser", action="store_true",
                   help="print the Google URL instead of auto-opening a browser "
                        "(use when your default browser is the wrong Google account)")
    c.set_defaults(func=cmd_classroom)
    return p


def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    args = build_parser().parse_args(argv)
    if getattr(args, "profile", None):
        from . import profiles
        profiles.set_current(args.profile)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
