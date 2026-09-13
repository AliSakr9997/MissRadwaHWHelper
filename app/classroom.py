"""Read-only Google Classroom fetch -> our roster/submission model.

First run prints a SUMMARY ONLY and changes nothing locally. Later phases
(reconcile roster, download attachments) build on these structures.
"""
from __future__ import annotations


def _service(api: str, version: str, creds):
    from googleapiclient.discovery import build
    return build(api, version, credentials=creds)


def _paginate(callable_list, **kwargs) -> list[dict]:
    """Collect all pages for list() calls using pageToken."""
    items: list[dict] = []
    token = None
    while True:
        req = callable_list(pageToken=token, **kwargs) if token else callable_list(**kwargs)
        resp = req.execute()
        key = next((k for k in ("courses", "students", "courseWork", "studentSubmissions")
                    if k in resp), None)
        items.extend(resp.get(key, []) if key else [])
        token = resp.get("nextPageToken")
        if not token:
            return items


def list_courses(creds) -> list[dict]:
    svc = _service("classroom", "v1", creds)
    return _paginate(svc.courses().list, teacherId="me", courseStates=["ACTIVE"],
                     pageSize=100)


def list_students(creds, course_id: str) -> list[dict]:
    svc = _service("classroom", "v1", creds)
    return _paginate(svc.courses().students().list, courseId=course_id, pageSize=100)


def list_coursework(creds, course_id: str) -> list[dict]:
    svc = _service("classroom", "v1", creds)
    return _paginate(svc.courses().courseWork().list, courseId=course_id,
                     courseWorkStates=["PUBLISHED"], pageSize=100,
                     fields="courseWork(id,title,dueDate,dueTime,maxPoints),nextPageToken")


def list_submissions(creds, course_id: str, coursework_id: str) -> list[dict]:
    svc = _service("classroom", "v1", creds)
    return _paginate(svc.courses().courseWork().studentSubmissions().list,
                     courseId=course_id, courseWorkId=coursework_id, pageSize=100,
                     fields="studentSubmissions(id,userId,state,late,assignedGrade,"
                            "assignmentSubmission),nextPageToken")


def _try(label: str, fn) -> dict:
    """Run one read-only probe, never raise — returns {label, ok, count|error}."""
    try:
        items = fn()
        sample = [{"id": c.get("id"), "name": c.get("name", c.get("title", "")),
                   "state": c.get("courseState", c.get("state", ""))}
                  for c in items[:10]]
        return {"label": label, "ok": True, "count": len(items), "sample": sample}
    except Exception as e:  # surface API errors verbatim (403/404/401...)
        return {"label": label, "ok": False, "error": str(e)[:300]}


def diagnose(creds, course_id: str | None = None) -> dict:
    """Probe what this account can see. Read-only, changes nothing."""
    svc = _service("classroom", "v1", creds)
    probes = [
        _try("courses as teacher (ACTIVE)",
             lambda: _paginate(svc.courses().list, teacherId="me",
                               courseStates=["ACTIVE"], pageSize=100)),
        _try("courses as teacher (any state)",
             lambda: _paginate(svc.courses().list, teacherId="me", pageSize=100)),
        _try("courses as student",
             lambda: _paginate(svc.courses().list, studentId="me", pageSize=100)),
        _try("courses unfiltered",
             lambda: _paginate(svc.courses().list, pageSize=30)),
    ]
    if course_id:
        probes.append(_try(f"coursework in {course_id}",
                           lambda: list_coursework(creds, course_id)))
    return {"probes": probes}


def classify(sub: dict) -> str:
    """Map Classroom state -> our model: submitted | missing."""
    state = sub.get("state", "")
    if state in ("TURNED_IN", "RETURNED"):
        return "submitted"
    return "missing"  # NEW, CREATED, RECLAIMED_BY_STUDENT


def summarize_course(creds, course_id: str) -> dict:
    """Per-assignment submitted/missing/late counts. Read-only."""
    students = list_students(creds, course_id)
    work = list_coursework(creds, course_id)
    assignments = []
    for cw in work:
        subs = list_submissions(creds, course_id, cw["id"])
        by_state: dict[str, int] = {}
        late = 0
        submitted_ids: list[str] = []
        for s in subs:
            cls = classify(s)
            by_state[cls] = by_state.get(cls, 0) + 1
            if cls == "submitted":
                submitted_ids.append(s["userId"])
            if s.get("late"):
                late += 1
        due = cw.get("dueDate", {})
        assignments.append({
            "id": cw["id"], "title": cw.get("title", ""),
            "due": f"{due.get('year')}-{due.get('month', 0):02d}-{due.get('day', 0):02d}"
                   if due else None,
            "maxPoints": cw.get("maxPoints"),
            "submitted": by_state.get("submitted", 0),
            "missing": by_state.get("missing", 0),
            "late": late,
            "submittedIds": submitted_ids,
        })
    return {"courseId": course_id, "rosterSize": len(students),
            "assignments": assignments}


def _classroom_display_name(entry: dict) -> str:
    prof = entry.get("profile", {})
    name = (prof.get("name", {}) or {})
    full = (name.get("fullName") or "").strip()
    if full:
        return full
    email = (prof.get("emailAddress") or "").strip()
    return email.split("@")[0] if email else entry.get("userId", "?")



def reconcile(creds, course_id: str, students: list[dict]) -> dict:
    """Match Classroom roster to local roster (read-only, no writes).

    Returns {matched: [{studentId, userId, name}], review: [{userId, name}],
             unlisted: [studentId]}. Local matching reuses name/variant logic:
    exact officialName/id, then variants, then single contains-hit.
    """
    from . import report_engine
    remote = list_students(creds, course_id)
    by_userid = {s.get("userId"): s for s in remote}
    matched, review, seen_local = [], [], set()
    for uid, entry in by_userid.items():
        name = _classroom_display_name(entry)
        direct = next((s for s in students if s.get("classroomId") == uid), None)
        if direct is not None:
            matched.append({"studentId": direct["id"], "userId": uid, "name": name})
            seen_local.add(direct["id"])
            continue
        student, needs_review = report_engine.resolve_student(name, students)
        if student is not None and not needs_review and student["id"] not in seen_local:
            matched.append({"studentId": student["id"], "userId": uid, "name": name})
            seen_local.add(student["id"])
        else:
            review.append({"userId": uid, "name": name})
    unlisted = [s["id"] for s in students if s.get("active", True) and s["id"] not in seen_local]
    return {"matched": matched, "review": review, "unlisted": unlisted}


def apply_reconcile(students: list[dict], matched: list[dict]) -> list[dict]:
    """Write matched Classroom userIds into the roster. Returns mutated list."""
    by_id = {s["id"]: s for s in students}
    for m in matched:
        s = by_id.get(m["studentId"])
        if s is not None:
            s["classroomId"] = m["userId"]
    return students


def fetch_status(creds, course_id: str, coursework_id: str, students: list[dict]) -> dict:
    """Submitted/missing/late for one assignment, mapped to roster ids.

    submittedIds/missingIds/lateIds use local roster ids where the Classroom
    user is reconciled; unknown users land in reviewIds (Classroom userIds).
    """
def _attachment_count(sub: dict) -> int:
    return len(((sub.get("assignmentSubmission") or {}).get("attachments") or []))


def fetch_status(creds, course_id: str, coursework_id: str, students: list[dict]) -> dict:
    """Submitted/missing/late for one assignment, mapped to roster ids.

    TURNED_IN with zero attachments counts as EMPTY (kids click Turn In with
    nothing attached) and is treated as missing. submittedIds/missingIds/
    lateIds use local roster ids; unknown Classroom users land in reviewIds.
    """
    subs = list_submissions(creds, course_id, coursework_id)
    by_userid = {s["id"]: s.get("classroomId", "") for s in students}
    # invert: classroom userId -> roster id
    roster_by_uid = {v: k for k, v in by_userid.items() if v}
    submitted, late, empty, review = [], [], [], []
    for s in subs:
        uid = s.get("userId", "")
        cls = classify(s)
        sid = roster_by_uid.get(uid)
        if sid is None:
            review.append(uid)
            continue
        if cls == "submitted":
            if _attachment_count(s) == 0:
                empty.append(sid)
            else:
                submitted.append(sid)
        if s.get("late"):
            late.append(sid)
    # Missing = eligible (active + on Classroom) reconciled students who did
    # not submit. onClassroom=false students (e.g. Kinza) are excluded here —
    # the batch layer gives them a ⚠️ block instead. Unreconciled students
    # (e.g. Alya) are covered by the batch implicit-missing rule.
    from . import report_engine
    eligible = set(report_engine.eligible_ids(students))
    submitted_set = set(submitted)
    # Empty turn-ins join the missing list (teacher sees them as not sent).
    missing = [sid for sid in roster_by_uid.values()
               if sid in eligible and (sid not in submitted_set or sid in empty)]
    missing = sorted(set(missing))
    return {"courseId": course_id, "courseworkId": coursework_id,
            "submittedIds": submitted, "missingIds": missing,
            "lateIds": late, "emptyIds": sorted(set(empty)),
            "reviewIds": sorted(set(review))}


def _drive_service(creds):
    return _service("drive", "v3", creds)


def _download_drive_file(drive, file_id: str, dest) -> str:
    """Download a Drive file; export Google-native docs as PDF. Returns kind."""
    from googleapiclient.http import MediaIoBaseDownload
    import io as _io
    meta = drive.files().get(fileId=file_id,
                             fields="id,name,mimeType").execute()
    mime = meta.get("mimeType", "")
    if mime.startswith("application/vnd.google-apps."):
        data = drive.files().export_media(fileId=file_id,
                                          mimeType="application/pdf").execute()
        dest.write_bytes(data if isinstance(data, bytes) else data)
        return "exported-pdf"
    request = drive.files().get_media(fileId=file_id)
    buf = _io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    dest.write_bytes(buf.getvalue())
    return "file"


def download_assignment(creds, course_id: str, coursework_id: str,
                        assignment_key: str) -> dict:
    """Download TURNED_IN/RETURNED attachments into homework/<key>/original/.

    PDFs/images land as files; Google Docs/Slides export to PDF; bare links
    are appended to links.txt. Returns a manifest (no names, ids only).
    """
    from . import file_organizer
    import re as _re
    dirs = file_organizer.assignment_dirs(assignment_key)
    drive = _drive_service(creds)
    subs = list_submissions(creds, course_id, coursework_id)
    manifest = []
    for s in subs:
        if classify(s) != "submitted":
            continue
        attachments = ((s.get("assignmentSubmission") or {}).get("attachments") or [])
        got = []
        for a in attachments:
            df = a.get("driveFile") or {}
            fid = df.get("id")
            if fid:
                title = df.get("title") or fid
                safe = _re.sub(r'[<>:"/\\|?*]', "", title).strip() or fid
                dest = dirs["original"] / f"{s.get('userId', 'unknown')}__{safe}"
                n = 2
                while dest.exists():
                    # Same-titled attachments (e.g. 3x "Photo.pdf") must not
                    # overwrite each other.
                    dest = dirs["original"] / f"{s.get('userId', 'unknown')}__{safe}__{n}"
                    n += 1
                try:
                    kind = _download_drive_file(drive, fid, dest)
                    got.append({"kind": kind, "file": dest.name, "driveId": fid})
                except Exception as e:
                    got.append({"kind": "error", "file": dest.name,
                                "driveId": fid, "error": str(e)[:200]})
            elif a.get("link"):
                url = (a["link"].get("url") or "").strip()
                if url:
                    file_organizer.record_link(assignment_key, s.get("userId", "?"), url)
                    got.append({"kind": "link", "url": url})
        manifest.append({"userId": s.get("userId"), "late": bool(s.get("late")),
                         "attachments": got})
    return {"assignmentKey": assignment_key, "submissions": manifest}
