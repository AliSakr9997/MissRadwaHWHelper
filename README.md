# MissRadwaHWHelper (offline, local-first)

Teacher assistant: organize HW files → mark → preview/approve reports → copy/send.
AI does admin. Teacher does academic judgment.

## Quick start (no hosting, works offline)

```powershell
pip install -r requirements.txt
python -m app.server          # -> http://127.0.0.1:8000
```

### Windows double-click launcher

After installing the dependencies once, double-click **`Start MissRadwaHWHelper.bat`**
in the project folder. It starts the local server and opens the app in your
default browser automatically. Keep the black server window open while using
the app; close it to stop the local server.

Tabs: **1 · Reports** (form + free-text → preview → approve → copy),
**2 · Batch paste** (multi-student log → full output document → approve all),
**3 · Files & Mark** (PDF + stylus overlay → save to `marked/`),
**4 · Roster** (local `students.json` wins, incl. Arabic names + m/f + onClassroom),
**5 · Missing HW** (checklist → missing list → grouped Arabic messages + copy).

## Batch paste (the daily workflow)

Paste the WhatsApp-style log, one section per day
(`-----` / `Monday HW` lines separate days):

```powershell
python -m app.main batch --file paste.txt --dates 2026-09-05,2026-09-07 --year Y8
python -m app.main batch --file paste.txt --dates 2026-09-05,2026-09-07 --save --out output.txt
```

Rules the batch follows: `Didn't send` → `❌ Did not send HW` block,
`not on classroom` → `⚠️ Not on Classroom`, roster students with no entry
default to missing, mistakes `0` → `(none)`, late → red, missing
understanding + failing grade → `poor`, otherwise the line is omitted.

## CLI (no browser)

```powershell
python -m app.main report --student "Sara Ali" --num 51 --den 54 --mistakes 3 --skipped 0 --understanding "very good" --date 2026-09-05 --year Y8 --ontime --note "Make sure to write all calculations"
python -m app.main organize --assignment 2026-09-05_hw1 --student "Sara Ali" --images IMG_1021.jpg IMG_1022.jpg
python -m app.main missing --assignment 2026-09-05_hw1 --compute --submitted sara-ali omar-khaled
python -m app.main missing
```

## Rules (changeable later)

`config/report_rules.json`: mistakes `0 (none) 🟢 / 1-4 🟢 / 5+ 🔴`, skipped `0 🟢 / 1-2 none / 3+ 🔴`,
understanding `excellent+full ⭐ / excellent 🟢 / very good 🟢 / poor 🔴`,
missing understanding + failing (<50%) → infer `poor`, else omit line,
late → `HW sent late 🔴`, strict policy. `note_fixes` holds teacher-curated phrase fixes.
Roster: `config/students.json` is source of truth, edit via UI.
Files: `homework/<YYYY-MM-DD_key>/{original,normalized,marked}/ + reports.json + links.txt`.
Resubmits versioned (`Name.pdf`, `Name_v2.pdf`).

## Google Classroom (read-only)

Roster starts empty — add your students via the Roster UI
(or copy `config/students.example.json` to `config/students.json` and edit).
Real names and `homework/` data never commit (see `.gitignore`).

One-time setup (in Google Cloud Console):

1. New project → enable **Google Classroom API** + **Google Drive API**.
2. OAuth consent screen → **External**, add yourself as test user.
3. Credentials → OAuth client ID → **Desktop app** → download JSON →
   save as `config/client_secret.json` (gitignored, never share).
4. Back here, run (browser opens once, token stays local):

```powershell
pip install -r requirements.txt
python -m app.main classroom auth
python -m app.main classroom courses
python -m app.main classroom summary --course COURSE_ID
```

If sign-in fails with a permissions/scope message: revoke the app at
myaccount.google.com → Security → Third-party access, run
`python -m app.main classroom logout`, then `auth` again keeping ALL
permission checkboxes ticked.

## AI reports (optional, your own key)

Deterministic batch paste always works offline. For free-form wording, add an
AI provider (tab **3 · AI** or CLI). The AI only extracts structured data —
the app validates everything (roster, grade bounds, dates) and renders:

```powershell
python -m app.main ai config show
python -m app.main ai set --provider openrouter --model openai/gpt-4o-mini --key YOUR_KEY
python -m app.main ai test
python -m app.main ai run --file paste.txt --dates 2026-09-05,2026-09-07
```

Providers: openai, openrouter, groq, mistral, anthropic, google, custom
(any OpenAI-compatible URL, e.g. local LM Studio). Keys stay in gitignored
`data/<profile>/ai_config.json` and are never echoed back.

## Second teacher (same or another machine)

Each teacher gets an isolated profile (own roster, token, homework):

```powershell
python -m app.main --profile sara classroom auth   # her Google account
HW_PROFILE=sara python -m app.server               # her workspace
```

- Same OAuth client works for everyone: in Cloud Console add each teacher
  as a **Test User** (Testing mode, up to 100). They'll see an "unverified
  app" screen once and click through.
- Beyond that (whole school), Google requires **verification** of the app
  for these sensitive scopes — a console paperwork process, not a code change.
- One machine per teacher needs no profiles at all — just install and use.
