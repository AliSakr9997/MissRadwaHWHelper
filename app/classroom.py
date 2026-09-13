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
                     fields="studentSubmissions(id,userId,state,late,assignedGrade),nextPageToken")


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
