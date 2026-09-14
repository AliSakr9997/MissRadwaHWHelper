"""Localhost UI: stdlib-only HTTP server (no pip deps, works offline).

Routes:
  GET  /                          -> app/static/index.html
  GET  /api/students               -> roster list
  POST /api/students               -> save roster list [{id,officialName,...}]
  GET  /api/assignments            -> [{key, files:[normalized pdfs], reports, missing}]
  GET  /api/missing                 -> {byAssignment, byStudent:[{studentId, officialName, assignments, message}]}
  POST /api/missing/compute         -> {assignmentKey, submittedIds} -> saves missing.json
  POST /api/report/preview         -> {input...} -> {report, officialName, needsReview}
  POST /api/report/approve         -> appends to homework/<key>/reports.json
  POST /api/batch/preview          -> {text, dates, year} -> {output, payload}
  POST /api/batch/approve          -> {payload} -> saves reports + missing
  POST /api/ai/config               -> save {provider, model, api_key, base_url}
  GET  /api/ai/config               -> masked config + provider/model lists
  POST /api/ai/test                 -> test connection (ok/fail only, no key echo)
  POST /api/ai/preview              -> {text, dates, year} -> {output, payload}
  POST /api/ai/approve              -> {payload} -> saves reports + missing
  GET  /homework/...               -> static files (PDFs)
  POST /api/annotate/save          -> {assignmentKey, file, dataUrl} -> marked/<file>.png + .json
  GET  /api/classroom/summary      -> ?courseId= — read-only per-assignment counts
                                     (auth via CLI `classroom auth`; errors if not configured)
  GET  /api/classroom/courses      -> teacher courses (for dropdowns)
  GET  /api/classroom/work         -> ?courseId= coursework list
  POST /api/classroom/fetch        -> {courseId, courseworkId, assignmentKey}
                                     saves missing.json (empty turn-ins = missing)
  POST /api/classroom/download     -> {courseId, courseworkId, assignmentKey,
                                        userIds:[...], assignmentName}
                                     downloads only selected submissions and
                                     normalizes them against the local roster

Run:  python -m app.server  (default http://127.0.0.1:8000)
"""
from __future__ import annotations

import base64
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import batch, file_organizer, missing, report_engine, roster

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


def _report_rules() -> dict:
    """Load shared rules with the teacher's profile template overrides."""
    from . import profiles
    rules = report_engine.load_rules()
    p = profiles.data_dir() / "report_template.json"
    if p.exists():
        try:
            overrides = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(overrides, dict):
                rules.setdefault("formatting", {}).update(
                    {k: str(v) for k, v in overrides.items()
                     if k in ("header", "note_header")})
        except (OSError, ValueError, TypeError):
            pass
    return rules


def _send_json(handler: SimpleHTTPRequestHandler, obj, status: int = 200) -> None:
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: SimpleHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length") or 0)
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {}


def _assignments() -> list[dict]:
    out = []
    from . import profiles
    hw = profiles.homework_dir()
    if not hw.exists():
        return out
    for base in sorted(p for p in hw.iterdir() if p.is_dir()):
        norm = base / "normalized"
        files = sorted([p.name for p in norm.glob("*.pdf")]) if norm.exists() else []
        for stub in ("reports.json", "missing.json"):
            f = base / stub
            if not f.exists():
                f.write_text("[]", encoding="utf-8")
        reports = json.loads((base / "reports.json").read_text(encoding="utf-8"))
        missing = json.loads((base / "missing.json").read_text(encoding="utf-8"))
        out.append({"key": base.name, "files": files, "reportCount": len(reports), "missing": missing})
    return out


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *args):  # quieter
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html; charset=utf-8")
        elif parsed.path == "/api/students":
            _send_json(self, roster.load())
        elif parsed.path == "/api/assignments":
            _send_json(self, _assignments())
        elif parsed.path == "/api/missing":
            students = {s["id"]: s for s in roster.load()}
            by_assignment = missing.load_missing_all()
            grouped = missing.group_by_student(by_assignment)
            by_student = []
            for sid, keys in grouped.items():
                s = students.get(sid, {"id": sid, "officialName": sid})
                by_student.append({"studentId": sid, "officialName": s.get("officialName", sid),
                                   "assignments": keys,
                                   "message": missing.build_student_message(s, keys)})
            _send_json(self, {"byAssignment": by_assignment, "byStudent": by_student})
        elif parsed.path == "/api/classroom/summary":
            from . import classroom, classroom_auth
            course = (parse_qs(parsed.query).get("courseId") or [None])[0]
            if not course:
                _send_json(self, {"ok": False, "error": "courseId required"}, 400)
                return
            try:
                creds = classroom_auth.get_credentials()
                summary = classroom.summarize_course(creds, course)
            except Exception as e:  # not configured / no browser in server context
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            _send_json(self, {"ok": True, "summary": summary})
        elif parsed.path == "/api/classroom/courses":
            from . import classroom, classroom_auth
            try:
                creds = classroom_auth.get_credentials()
                courses = [{"id": c["id"], "name": c.get("name", ""),
                            "state": c.get("courseState", "")}
                           for c in classroom.list_courses(creds)]
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            _send_json(self, {"ok": True, "courses": courses})
        elif parsed.path == "/api/classroom/work":
            from . import classroom, classroom_auth
            course = (parse_qs(parsed.query).get("courseId") or [None])[0]
            if not course:
                _send_json(self, {"ok": False, "error": "courseId required"}, 400)
                return
            try:
                creds = classroom_auth.get_credentials()
                work = [{"id": w["id"], "title": w.get("title", "")}
                        for w in classroom.list_coursework(creds, course)]
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            _send_json(self, {"ok": True, "coursework": work})
        elif parsed.path == "/api/profile":
            from . import profiles
            _send_json(self, {"profile": profiles.current()})
        elif parsed.path == "/api/report/template":
            from . import profiles
            p = profiles.data_dir() / "report_template.json"
            if p.exists():
                try:
                    template = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    template = {}
            else:
                template = {}
            defaults = report_engine.load_rules().get("formatting", {})
            _send_json(self, {"ok": True, "template": {
                "header": template.get("header", defaults.get("header", "📝HW Report")),
                "note_header": template.get("note_header", defaults.get("note_header", "‼️NOTE‼️"))
            }})
        elif parsed.path == "/api/ai/config" and self.command == "GET":
            from . import ai_config, ai_providers
            cfg = ai_config.masked()
            models = {p: cls.default_models()
                      for p, cls in ai_providers.PROVIDERS.items()}
            _send_json(self, {"ok": True, "config": cfg,
                              "providers": sorted(models), "models": models})
        elif parsed.path.startswith("/homework/"):
            from . import profiles
            hwroot = profiles.homework_dir().resolve()
            target = (hwroot / parsed.path[len("/homework/"):]).resolve()
            if not str(target).startswith(str(hwroot)) or not target.is_file():
                self.send_error(404)
                return
            ctype = "application/pdf" if target.suffix == ".pdf" else "application/octet-stream"
            self._serve_file(target, ctype)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/students":
            students = _read_json(self).get("students", [])
            roster.save(students)
            _send_json(self, {"ok": True, "count": len(students)})
        elif parsed.path == "/api/report/template":
            from . import profiles
            data = _read_json(self)
            template = {k: str(data.get(k, "")).strip()
                        for k in ("header", "note_header")}
            if not template["header"] or not template["note_header"]:
                _send_json(self, {"ok": False, "error": "header and note_header are required"}, 400)
                return
            p = profiles.data_dir() / "report_template.json"
            p.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
            _send_json(self, {"ok": True, "template": template})
        elif parsed.path == "/api/report/preview":
            data = _read_json(self)
            students = roster.load()
            rules = _report_rules()
            if data.get("freeText"):
                parsed_free = report_engine.parse_free_text(data["freeText"])
                data["_entry_type"] = parsed_free.get("type", "report")
                for k in ("grade_num", "grade_den", "mistakes", "skipped", "understanding", "note"):
                    data.setdefault(k, parsed_free.get(k))
                data.setdefault("student_query", parsed_free.get("student_query", ""))
                if parsed_free.get("late"):
                    data["ontime"] = False
            student, needs_review = report_engine.resolve_student(
                data.get("student_query", ""), roster.active_only(students)
            )
            official = student["officialName"] if student else (data.get("student_query") or "").strip()
            iso = data.get("iso_date", "2026-09-05")
            try:
                y, m, d = iso.split("-")
                weekday = data.get("weekday") or report_engine.weekday_name(iso)
                if data.get("_entry_type") == "missing":
                    report = report_engine.format_status_report(
                        official_name=official or "Unknown", year=data.get("year", "Y8"),
                        day=int(d), month=int(m), weekday=weekday, status="missing", rules=rules)
                elif data.get("_entry_type") == "not_on_classroom":
                    report = report_engine.format_status_report(
                        official_name=official or "Unknown", year=data.get("year", "Y8"),
                        day=int(d), month=int(m), weekday=weekday,
                        status="not_on_classroom", rules=rules)
                else:
                    report = report_engine.format_report(
                        official_name=official or "Unknown",
                        year=data.get("year", "Y8"),
                        day=int(data.get("day", int(d))),
                        month=int(data.get("month", int(m))),
                        weekday=weekday,
                        ontime=bool(data.get("ontime", True)),
                        mistakes=int(data.get("mistakes", 0)),
                        skipped=int(data.get("skipped", 0)),
                        understanding=(data.get("understanding") or None),
                        grade_num=int(data["grade_num"]),
                        grade_den=int(data["grade_den"]),
                        note=str(data.get("note", "")),
                        rules=rules,
                    )
            except (KeyError, ValueError, TypeError) as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            _send_json(self, {"ok": True, "report": report, "officialName": official,
                              "needsReview": needs_review})
        elif parsed.path == "/api/report/approve":
            data = _read_json(self)
            key = data.get("assignmentKey", "2026-09-05_example_assignment")
            base = file_organizer.assignment_dirs(key)["base"]
            rf = base / "reports.json"
            reports = json.loads(rf.read_text(encoding="utf-8"))
            # version both: next version for this student+assignment
            sid = data.get("studentId") or data.get("officialName", "unknown")
            existing = [r for r in reports if r.get("studentId") == sid
                        and r.get("assignmentKey", key) == key]
            data["version"] = len(existing) + 1
            data["assignmentKey"] = key
            data["status"] = "approved"
            reports.append(data)
            rf.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
            _send_json(self, {"ok": True, "version": data["version"]})
        elif parsed.path == "/api/missing/compute":
            data = _read_json(self)
            key = data.get("assignmentKey", "")
            if not key:
                _send_json(self, {"ok": False, "error": "assignmentKey required"}, 400)
                return
            submitted = data.get("submittedIds", [])
            miss = missing.compute_missing(report_engine.eligible_ids(roster.load()), submitted)
            missing.save_missing(key, miss)
            _send_json(self, {"ok": True, "assignmentKey": key, "missing": miss})
        elif parsed.path == "/api/batch/preview":
            data = _read_json(self)
            students = roster.load()
            dates = [d.strip() for d in str(data.get("dates", "")).split(",") if d.strip()]
            try:
                sections = report_engine.parse_batch(data.get("text", ""), students)
                payload = batch.resolve_batch(
                    sections, dates, students, year=data.get("year", "Y8"),
                    rules=_report_rules())
            except ValueError as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            output = batch.render_output(payload, students)
            # payload is JSON-safe (no datetimes); echo back for approve
            _send_json(self, {"ok": True, "output": output, "payload": payload,
                              "needsReview": sorted({r["officialName"]
                                                     for r in payload["needsReview"]})})
        elif parsed.path == "/api/batch/approve":
            data = _read_json(self)
            payload = data.get("payload")
            if not payload:
                _send_json(self, {"ok": False, "error": "payload required"}, 400)
                return
            batch.save_payload(payload)
            _send_json(self, {"ok": True,
                              "assignments": [d["key"] for d in payload.get("days", [])]})
        elif parsed.path == "/api/classroom/fetch":
            from . import classroom, classroom_auth
            data = _read_json(self)
            if not data.get("courseId") or not data.get("courseworkId") \
                    or not data.get("assignmentKey"):
                _send_json(self, {"ok": False,
                                  "error": "courseId, courseworkId, assignmentKey required"},
                           400)
                return
            try:
                creds = classroom_auth.get_credentials()
                st = classroom.fetch_status(creds, data.get("courseId", ""),
                                            data.get("courseworkId", ""), roster.load())
                missing.save_missing(data.get("assignmentKey", ""), st["missingIds"])
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            names = {s["id"]: s.get("officialName", s["id"]) for s in roster.load()}
            _send_json(self, {"ok": True,
                              "assignmentKey": data.get("assignmentKey", ""),
                              "submittedIds": st["submittedIds"],
                              "missingIds": st["missingIds"],
                              "lateIds": st["lateIds"],
                              "submitted": [names.get(i, i) for i in st["submittedIds"]],
                              "missing": [names.get(i, i) for i in st["missingIds"]],
                              "empty": [names.get(i, i) for i in st["emptyIds"]],
                              "late": [names.get(i, i) for i in st["lateIds"]],
                              "unmapped": st["reviewIds"]})
        elif parsed.path == "/api/classroom/download":
            from . import classroom, classroom_auth
            data = _read_json(self)
            required = ("courseId", "courseworkId", "assignmentKey")
            if any(not data.get(k) for k in required):
                _send_json(self, {"ok": False,
                                  "error": "courseId, courseworkId, assignmentKey required"},
                           400)
                return
            try:
                creds = classroom_auth.get_credentials()
                selected = data.get("userIds")
                if selected is None:
                    user_ids = None
                else:
                    by_local_id = {str(s.get("id")): s for s in roster.load()}
                    user_ids = {
                        str(by_local_id[str(item)].get("classroomId"))
                        for item in selected
                        if str(item) in by_local_id
                        and by_local_id[str(item)].get("classroomId")
                    }
                manifest = classroom.download_assignment(
                    creds, str(data["courseId"]), str(data["courseworkId"]),
                    str(data["assignmentKey"]), user_ids=user_ids)
                students = roster.load()
                uid_to_name = {s.get("classroomId"): s.get("officialName", s["id"])
                               for s in students if s.get("classroomId")}
                organized = file_organizer.organize_originals(
                    str(data["assignmentKey"]), uid_to_name,
                    str(data.get("assignmentName") or "Homework"))
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            _send_json(self, {"ok": True, "manifest": manifest,
                              "organized": organized})
        elif parsed.path == "/api/annotate/save":
            data = _read_json(self)
            key = data.get("assignmentKey", "2026-09-05_example_assignment")
            fname = Path(data.get("file", "overlay")).stem
            marked = file_organizer.assignment_dirs(key)["marked"]
            data_url = data.get("dataUrl", "")
            if "," in data_url:
                _, b64 = data_url.split(",", 1)
                (marked / f"{fname}.overlay.png").write_bytes(base64.b64decode(b64))
            (marked / f"{fname}.overlay.json").write_text(
                json.dumps({"strokes": data.get("strokes", []), "file": data.get("file")},
                           ensure_ascii=False), encoding="utf-8")
            _send_json(self, {"ok": True})
        elif parsed.path == "/api/ai/config":
            from . import ai_config, ai_providers
            data = _read_json(self)
            if data.get("provider", "").lower() not in ai_providers.PROVIDERS:
                _send_json(self, {"ok": False, "error": "unknown provider"}, 400)
                return
            cur = ai_config.load()
            key = data.get("api_key", "")
            if not key:
                key = cur.get("api_key", "")  # blank = keep existing key
            saved = ai_config.save(data.get("provider", ""), data.get("model", ""),
                                   key, data.get("base_url", ""))
            _send_json(self, {"ok": True, "config": saved})
        elif parsed.path == "/api/ai/test":
            from . import ai_config, ai_providers
            try:
                provider, model, key = ai_config.credentials()
                cfg = ai_config.load()
                prov = ai_providers.create(provider, key, cfg.get("base_url", ""))
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            ok, msg = prov.test(model)
            _send_json(self, {"ok": ok, "message": msg})
        elif parsed.path == "/api/ai/preview":
            from . import ai_config, ai_reports
            data = _read_json(self)
            students = roster.load()
            dates = [d.strip() for d in str(data.get("dates", "")).split(",") if d.strip()]
            try:
                provider, model, key = ai_config.credentials()
                cfg = ai_config.load()
                raw = ai_reports.interpret(
                    data.get("text", ""), provider, model, key,
                    cfg.get("base_url", ""), roster=students,
                    year=data.get("year", "Y8"), default_dates=dates)
                payload = ai_reports.to_batch_payload(
                    raw, students, year=data.get("year", "Y8"),
                    default_dates=dates, rules=_report_rules())
            except Exception as e:
                _send_json(self, {"ok": False, "error": str(e)}, 400)
                return
            output = batch.render_output(payload, students)
            _send_json(self, {"ok": True, "output": output, "payload": payload,
                              "needsReview": sorted({r["officialName"]
                                                     for r in payload["needsReview"]})})
        elif parsed.path == "/api/ai/approve":
            data = _read_json(self)
            payload = data.get("payload")
            if not payload:
                _send_json(self, {"ok": False, "error": "payload required"}, 400)
                return
            batch.save_payload(payload)
            _send_json(self, {"ok": True,
                              "assignments": [d["key"] for d in payload.get("days", [])]})
        else:
            self.send_error(404)

    def _serve_file(self, path: Path, ctype: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def run(host: str = "127.0.0.1", port: int = 8000) -> None:
    print(f"MissRadwaHWHelper local UI -> http://{host}:{port}  (offline, Ctrl+C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    import sys
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    run(port=port)
