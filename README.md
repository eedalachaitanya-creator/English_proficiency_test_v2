# English Proficiency Test

An internal HR tool that lets recruiters send a one-time test link to a candidate, the candidate takes a 2-section English test (Reading MCQs + Speaking with audio recording), and the scores appear in the recruiter's dashboard. Scores are never shown to the candidate.

This document is the place to start if you're new to the repo. It covers what's in each folder, how to run the project locally, and what's done vs pending.

---

## Tech stack

| Layer | What we use | Why |
|---|---|---|
| Backend | Python 3 + FastAPI | Light, fast to iterate, auto-generated `/docs` endpoint |
| Database | PostgreSQL | Same engine in dev (local) and production (Render) so no surprises |
| ORM | SQLAlchemy 2.0 | Lets us write queries in Python instead of raw SQL |
| Frontend | Plain HTML + CSS + vanilla JavaScript | No build step. Open `.html` in a browser, it works. |
| Audio capture | `MediaRecorder` browser API | Built in. No third-party library needed. |
| Speech-to-text | OpenAI Whisper API (planned) | Will transcribe candidate audio for LLM scoring |
| AI scoring | Anthropic Claude API (planned) | Rubric-based scoring of speaking section |

There is no separate frontend server. FastAPI serves the static HTML files alongside the API endpoints.

---

## How it works (high level)

```
HR portal              Server                     Candidate
    |                    |                           |
    | POST /hr/login --->|                           |
    |<-- session cookie  |                           |
    |                    |                           |
    | POST /hr/invite -->|                           |
    |  {email, level}    | generate token,           |
    |<-- exam URL        | save invitation row       |
    |                    |                           |
    |                    |     /exam/{token} <-------| (clicks URL)
    |                    | validate, set session     |
    |                    | redirect to test pages    |
    |                    |                           |
    |                    |     POST /api/submit <----| (audio + answers)
    |                    | save mcq_answers,         |
    |                    | save audio files,         |
    |                    | score reading,            |
    |                    | mark submitted            |
    |                    |                           |
    | GET /hr/results -->|                           |
    |<-- candidate score |                           |
```

Two key invariants:
- **HR can only see their own candidates.** Every results query filters by `hr_admin_id`.
- **Candidate URL is one-time-use** and expires 24 h after creation. Once submitted, the link goes dead.

---

## Folder structure

```
English_Proficiency/
├── README.md                ← this file
├── docs/
│   └── requirements.md      ← source-of-truth spec for what the product must do
├── EPT_UI_Mockup_Workboard.xlsx   ← original wireframe sketch (one tab per screen)
│
├── backend/                 ← FastAPI server + database layer
│   ├── main.py              ← entry point. `uvicorn main:app --reload --port 8000`
│   ├── database.py          ← Postgres connection setup, session factory
│   ├── models.py            ← SQLAlchemy table definitions (8 tables)
│   ├── schemas.py           ← Pydantic request/response shapes — the API contract
│   ├── auth.py              ← bcrypt password hashing + session helpers
│   ├── scoring.py           ← reading scorer (deterministic) + speaking stub
│   ├── seed.py              ← one-time script: load passages, questions, topics
│   ├── create_hr.py         ← CLI to add an HR admin user
│   ├── routes/
│   │   ├── hr.py            ← /api/hr/* endpoints (login, invite, results, audio)
│   │   ├── candidate.py     ← /exam/{token}, /api/test-content
│   │   └── submit.py        ← /api/submit (candidate's final submission)
│   ├── audio_uploads/       ← candidate audio files saved here (gitignored)
│   ├── requirements.txt     ← pip dependencies
│   ├── .env.example         ← copy to .env and fill in real values
│   └── .env                 ← real values (gitignored)
│
└── frontend/                ← static HTML/CSS/JS, served by FastAPI
    ├── index.html           ← HR sign-in page (the entry point — http://localhost:8000)
    ├── hr-dashboard.html    ← HR-only: candidate list, invite modal, score detail
    ├── instructions.html    ← Candidate-facing: test overview before they start
    ├── reading.html         ← Section 1: passage on left, 15 MCQs on right, timer
    ├── speaking.html        ← Section 2: 3 speaking topics + audio recorder
    ├── submitted.html       ← Confirmation page (no scores shown to candidate)
    ├── css/
    │   └── style.css        ← shared styles, brand palette (deep navy + orange)
    ├── js/
    │   ├── common.js        ← shared utilities: api(), Modal.confirm/alert(), timer
    │   ├── login.js         ← HR login form logic
    │   ├── hr.js            ← HR dashboard rendering + invite modal + audio playback
    │   ├── reading.js       ← passage rendering, answer tracking, 30-min timer
    │   └── speaking.js      ← MediaRecorder integration, multi-question flow
    └── data/
        └── content.js       ← obsolete stub (content now comes from /api/test-content)
```

### What each backend file does

- **`main.py`** — Boots the FastAPI app, loads `.env`, creates DB tables on startup, mounts session middleware (signed cookies), CORS, and the static frontend folder. This is what `uvicorn` runs.
- **`database.py`** — Connects to Postgres via `DATABASE_URL`. Provides `get_db()` (per-request session) and `init_db()` (creates tables idempotently).
- **`models.py`** — Eight SQLAlchemy classes, one per table: `HRAdmin`, `Passage`, `Question`, `SpeakingTopic`, `Invitation`, `MCQAnswer`, `AudioRecording`, `Score`.
- **`schemas.py`** — Pydantic shapes for what the API accepts and returns. Critically: the `QuestionPublic` shape deliberately omits `correct_answer` so the candidate's browser never sees the answer key.
- **`auth.py`** — `hash_password()` / `verify_password()` (bcrypt), `generate_token()` (32-byte URL-safe random), and `require_hr` dependency that protects HR routes.
- **`scoring.py`** — `score_reading()` runs deterministic comparison; `score_speaking_stub()` is a placeholder until Whisper + Claude are wired in. Rating bands: ≥75 Recommended, 60–74 Borderline, <60 Not Recommended.
- **`routes/hr.py`** — POST /login, POST /logout, GET /me, POST /invite, GET /results, GET /results/{id}, GET /audio/{id}.
- **`routes/candidate.py`** — GET /exam/{token} (validate + set session + redirect), GET /api/test-content (returns assigned passage + 15 questions + 3 topics).
- **`routes/submit.py`** — POST /api/submit accepts FormData (answers JSON + 3 audio blobs), saves to DB, scores reading.
- **`seed.py`** — Run once to populate the DB with 4 passages, 44 questions, 8 speaking topics. `--reset` clears existing content first.
- **`create_hr.py`** — CLI: `python create_hr.py --name X --email Y --password Z` creates an HR admin row.

### What each frontend file does

- **`index.html`** — HR sign-in form. Posts to `/api/hr/login`, redirects to dashboard on success.
- **`hr-dashboard.html`** — HR's home screen. KPI cards, candidate table, invite modal, candidate detail panel with audio playback.
- **`instructions.html`** — First page candidates see after clicking their URL. Shows their name, the section breakdown, mic test button.
- **`reading.html`** — Two-column layout: passage on the left (scrollable), 15 questions on the right. 30-minute countdown timer top-right.
- **`speaking.html`** — Walks the candidate through 3 speaking prompts. Records audio using the browser's MediaRecorder API, shows a live waveform.
- **`submitted.html`** — Thank-you screen with a reference ID. No scores shown.
- **`js/common.js`** — `api()` wrapper around `fetch` (handles cookies, JSON, error messages). `Modal.confirm()` / `Modal.alert()` for styled in-app dialogs. `startCountdown()` factory.
- **`js/login.js`** — HR login submit handler.
- **`js/hr.js`** — Loads candidate list, renders KPIs and table, handles invite modal, renders detail panel with `<audio>` players.
- **`js/reading.js`** — Fetches `/api/test-content`, renders passage and questions, runs the timer, persists answers in `sessionStorage`.
- **`js/speaking.js`** — Fetches the 3 topics, captures one audio blob per topic, posts everything to `/api/submit` as FormData on Finish.

---

## Database schema

Eight tables, all created automatically by `init_db()` on first server startup.

| Table | Purpose |
|---|---|
| `hr_admins` | One row per HR user. Stores bcrypt-hashed password. |
| `passages` | Reading passages, tagged Intermediate or Expert. |
| `questions` | MCQs. `passage_id` is set for reading-comp questions, NULL for grammar/vocab. Holds the `correct_answer` server-side. |
| `speaking_topics` | Impromptu speaking prompts, tagged by difficulty. |
| `invitations` | One row per candidate URL. Holds token, expiry, who invited them, and which content they're assigned (passage_id, assigned_question_ids, assigned_topic_ids). |
| `mcq_answers` | Each row = one answer the candidate selected. |
| `audio_recordings` | Pointers to `.webm` audio files on disk + their Whisper transcripts. |
| `scores` | One row per submitted invitation. Holds reading_score, speaking_breakdown (JSON), total_score, rating, ai_feedback. |

The full schema is mirrored in code at `backend/models.py`. Inspect tables directly in pgAdmin under `Local → ept → Schemas → public → Tables`.

---

## Running locally

### Prerequisites
- Python 3.10+
- PostgreSQL running locally (Postgres.app or Homebrew)
- pgAdmin (optional, for inspecting the DB visually)
- A database called `ept` created via pgAdmin (`Create Database` with `template0` to avoid locale provider issues)

### First-time setup
```bash
cd backend
cp .env.example .env
# Edit .env: set DATABASE_URL, generate a real SESSION_SECRET (see comment in .env.example)

python3 -m pip install -r requirements.txt   # install dependencies
python3 seed.py                              # populate passages/questions/topics
python3 create_hr.py --name "Your Name" --email you@company.com --password "SomePassword123"
```

### Run the server
```bash
cd backend
uvicorn main:app --reload --port 8000
```

Visit:
- `http://localhost:8000/` → HR sign-in
- `http://localhost:8000/api/health` → quick health check
- `http://localhost:8000/docs` → interactive Swagger UI for every endpoint

### Trying the candidate flow
1. Sign in to the HR dashboard.
2. Click **+ INVITE NEW CANDIDATE**, fill in any name/email, pick a difficulty.
3. Copy the URL from the modal.
4. Open it in **a different browser or incognito window** (otherwise the candidate session collides with the HR session).
5. Take the test, submit.
6. Back in the HR dashboard, refresh — the candidate row flips from Pending to Submitted, with a Reading score.

---

## Environment variables

All in `backend/.env` (copy from `.env.example`).

| Variable | Used for | When needed |
|---|---|---|
| `DATABASE_URL` | Postgres connection string | Always |
| `SESSION_SECRET` | Signs HR/candidate session cookies | Always — generate random with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `APP_BASE_URL` | What URL gets put in invitation emails | Always (default `http://localhost:8000`) |
| `INVITATION_TTL_HOURS` | How long an invitation URL stays alive | Always (default 24) |
| `CORS_ALLOWED_ORIGINS` | Browsers allowed to make credentialed API calls | Always (defaults to localhost) |
| `ANTHROPIC_API_KEY` | Claude API for speaking scoring | When real LLM scoring is wired in |
| `OPENAI_API_KEY` | Whisper API for transcription | When real LLM scoring is wired in |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM_EMAIL` | Gmail/SendGrid SMTP for sending invite emails | When email delivery is wired in |

`.env` is gitignored. Never commit it.

---

## Status — what's done vs pending

### Done
- HR auth: login, logout, session cookies (signed, 8-hour expiry).
- HR can invite candidates with two difficulty levels (Intermediate / Expert).
- Tokenized exam URL, 24-hour expiry, single-use (dies on submission).
- Candidate test flow: instructions → reading (passage + 15 MCQs + timer) → speaking (3 prompts + audio capture).
- Real submission: candidate's MCQ answers saved, audio files saved to disk, invitation marked submitted.
- Reading scoring: deterministic, runs immediately on submit, real numeric score appears in HR dashboard.
- HR dashboard with KPI cards, candidate table, score detail panel, audio playback per question.
- Multi-tenancy: HR-A cannot see HR-B's candidates or audio.

### Pending
- **Speaking scoring with Whisper + Claude.** Currently the speaking score is `null` and a placeholder note appears. Needs Anthropic + OpenAI API keys, then ~half a day to wire up.
- **Email delivery of invitation URL.** Right now the URL is shown in the modal and HR copies it manually. Needs Gmail SMTP app password (or SendGrid) to automate.
- **Production deploy to Render.** Local-only for now. Render free tier with Postgres add-on is the planned target.
- **"Mark Reviewed" toggle, exportable PDF report, more dashboard filters** — nice-to-haves, deferred to v2.

---

## Common pitfalls (read before debugging)

- **Browser cache after JS changes.** If something looks broken after a code update, hard-refresh with **Cmd+Shift+R** (Mac) / Ctrl+Shift+R. The browser caches JS aggressively.
- **HR session and candidate session in the same browser collide.** Always test the candidate flow in incognito/private mode.
- **`.env` not loaded.** If `DATABASE_URL` is wrong, the server won't start. Check that you copied `.env.example` to `.env` and edited it.
- **`pip install` to wrong Python.** On macOS with Anaconda, `pip` and `python3` can point to different interpreters. Use `python3 -m pip install ...` to force them to match.
- **Empty `mcq_answers` after candidate submits.** Means the candidate browser had cached old `speaking.js` that didn't POST. Hard refresh.
- **`No passages have been seeded`** when opening the exam URL. Run `python3 seed.py` once.

---

## API reference (quick scan)

Full interactive docs at `http://localhost:8000/docs`.

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | none | Liveness check |
| POST | `/api/hr/login` | none | Email + password → session cookie |
| POST | `/api/hr/logout` | none | Clear session |
| GET | `/api/hr/me` | hr cookie | Current logged-in HR profile |
| POST | `/api/hr/invite` | hr cookie | Create a candidate invitation |
| GET | `/api/hr/results` | hr cookie | All your candidates with score summaries |
| GET | `/api/hr/results/{id}` | hr cookie | One candidate, full detail incl. audio |
| GET | `/api/hr/audio/{id}` | hr cookie | Stream a single audio recording |
| GET | `/exam/{token}` | none | Candidate's URL — validates + redirects |
| GET | `/api/test-content` | candidate cookie | Returns assigned passage + questions + topics |
| POST | `/api/submit` | candidate cookie | Submit answers + audio, mark complete |

---

## Where to ask

- Check `docs/requirements.md` for the authoritative product spec.
- For schema questions, read `backend/models.py` — the comments there are the source of truth.
- For API shape questions, the auto-generated docs at `/docs` show every request/response schema.
