# AI Homework Management & Reporting System — Implementation Plan (Revised)

> Local-first, offline. AI does admin, teacher does academic judgment.

## Sec 8 — AI Report Generator v1 (LOCKED)

### 8.1 Input (both supported, same schema)

Structured form is primary:

- `student_id` (dropdown from `config/students.json` officialName)
- `grade_num` / `grade_den` (e.g. 46 / 54)
- `mistakes` (int), `skipped` (int)
- `understanding` (dropdown: excellent / very good / good / weak / poor, free text preserved)
- `ontime_override` (auto | ontime | late, default auto from Classroom dueDate, strict)
- `note` (teacher note, spell-fix only)

Free-text shortcut parses into the same form for approval:

```text
Sara Ali
46/54
8 mistakes
Very good
Note: Study again the fraction flipping in dividing
```

### 8.2 Rules (`config/report_rules.json`, changeable later)

- Timezone: `Africa/Cairo`, date source: `courseWork.dueDate`, late policy: `strict`
- Mistakes: `0-4 🟢, 5+ 🔴`
- Skipped: `0 → Skipped questions (none) 🟢`, `1-2 → Skipped questions (n)` no emoji, `3+ → Skipped questions (n) 🔴`
- Understanding: `excellent + full mark (num==den) ⭐`, `excellent with mistakes 🟢`, `very good 🟢`, `good|weak|poor` preserve text, no emoji
- Submission: `HW sent ontime 🟢` / `HW sent late 🟡`
- Format:
```text
📝HW Report
Sara Ali (Y8 5/9 Saturday)
HW sent ontime 🟢
Mistakes (3) 🟢
Skipped questions (none) 🟢
Understanding (very good) 🟢
Grade: 51/54

‼️NOTE‼️
Make sure to write all calculations.
```

### 8.3 Processing

1. Match `student_id` → `students.json` officialName. Local file wins. Unknown Classroom ID → `Needs Review` queue, never auto-create.
2. Validate `grade_num <= grade_den`. Numbers immutable — AI must not recalculate or invent.
3. Date + weekday from `dueDate` in `Africa/Cairo`, format `5/9 Saturday`.
4. Apply emoji rules above.
5. Spell-fix notes only, preserve meaning.

### 8.4 Output + approval (mandatory)

Flow: `Form → Preview → Edit → Approve → Copy-per-student / Copy-all`.
Resubmit = keep `v1, v2` in `reports.json` + `normalized/Name_v2.pdf`, teacher picks which to send.
Distribution = manual copy-paste, grouped by-student.

## Sec 11 — Data Architecture (local-first offline, no hosting)

### 11.1 Principle

All local. No DB for MVP. Classroom = fetch-only, local FS = truth for names/reports.

### 11.2 Layout

```text
/config
  students.json
  classes.json
  report_rules.json
  preferences.json
/homework
  /2026-09-05_<courseWorkId>_fractions/
    /original/      // as-downloaded, never touched
    /normalized/    // Sara Ali.pdf, Sara Ali_v2.pdf
    /marked/        // teacher-annotated
    reports.json    // per-student reports + versions
    missing.json    // auto-generated
    links.txt       // link-only submissions
/knowledge
  faq.md
  instructions.md
```

### 11.3 Key decisions (locked)

- `teacherInput`: both-structured-form-default-plus-freetext-shortcut
- `rosterSourceOfTruth`: local-students.json, editor = simple UI
- `linkSubmissions`: list-only (no auto-download for MVP)
- `resubmitPolicy`: version-both
- `distribution`: manual-copy-paste
- Files: PDF + images (JPG/PNG/HEIC/WEBP convert) → single `Official Name.pdf`. Filename sanitized, duplicates `Name (2).pdf`.
- Stack: localhost-only web UI (e.g. FastAPI/Flask + PDF.js + canvas annotation), no deploy, works offline with tablet stylus via browser.

### 11.4 Example `students.json` entry

```json
{"id":"sara-ali","officialName":"Sara Ali","arabicFirst":"سارة","gender":"f","classroomId":"123...","variants":["Sara"],"active":true,"onClassroom":true}
```

### 11.5 Example `reports.json` entry

```json
{"studentId":"sara-ali","assignmentId":"...","version":2,"teacherInput":{},"finalReport":"📝HW Report...","status":"approved"}
```
