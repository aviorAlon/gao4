# Exam Diagnosis

**English** · [简体中文](README.zh-CN.md)

A local-first tool that turns a student's exam papers into a diagnosis: which questions went wrong, which knowledge points they map to, *why* they went wrong, what to practise next, and how the picture changes across exams. It ships as a web app (FastAPI backend + React frontend) and as a Windows desktop app (Tauri) that embeds the backend.

Everything runs on your machine. Model inference is optional and pluggable: any OpenAI-compatible provider works, one role at a time, and every step has a deterministic fallback that is honest about being a fallback — the application never fakes an OCR result, a diagnosis or a grade it did not actually obtain.

Status: **0.1.0, alpha.** The mechanics are complete and tested (about 400 backend tests, CI on Linux and Windows); the bundled public knowledge base is a placeholder partition (high-school mathematics, Guangdong, 14 knowledge points). See [Current limitations](#current-limitations).

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start (web)](#quick-start-web)
- [Configuration](#configuration)
  - [Model roles](#model-roles)
  - [Default provider](#default-provider)
  - [Vision parsing (images and scanned PDFs)](#vision-parsing-images-and-scanned-pdfs)
  - [Cost control](#cost-control)
  - [Login and multi-user mode](#login-and-multi-user-mode)
  - [All environment variables](#all-environment-variables)
- [Desktop app (Tauri)](#desktop-app-tauri)
- [Phone photo upload over the LAN](#phone-photo-upload-over-the-lan)
- [Knowledge bases](#knowledge-bases)
- [Reports, exports and the learning loop](#reports-exports-and-the-learning-loop)
- [Background tasks and observability](#background-tasks-and-observability)
- [API overview](#api-overview)
- [Development](#development)
- [Project layout](#project-layout)
- [Current limitations](#current-limitations)
- [Contributing, security, license](#contributing-security-license)

## Features

- **Upload anything**: text, PDF (with a text layer or scanned), images — from the computer, or straight from a phone camera over the local network via a QR code (no internet, no HTTPS setup).
- **Parse and confirm**: questions are split out, shown for review, and confirmed by a human before anything is analysed. Machine value → user correction → confirmed value is recorded per question.
- **Knowledge-point mapping**: keyword matching plus optional semantic matching by a model, against a public knowledge base layered with the user's own.
- **Root-cause diagnosis** per wrong question (missed condition, calculation error, unclear concept, misread question, sloppy writing, or *needs manual confirmation*), a knowledge tree with error rates and review priorities, and a confidence checklist that states exactly why a conclusion is only partially supported.
- **Verification questions**: one variant practice question with a worked reference answer per error cluster, gated by an independent review step — an unreviewed answer is never shown to the student. Student attempts are graded by a model against the *reviewed* answer, or self-assessed when no model is configured.
- **Review plan** (spaced repetition ladder 1/3/7/16/35 days), **wrong-question book** (with a printable practice version), and **cross-exam trends**.
- **Exports**: Markdown, HTML, PDF and Word from one shared block structure, honouring the same review gate; personal data export/import as a portable bundle.
- **Desktop app** with the backend embedded, a settings page for keys (never returned in any API response), a system-status page and a model-call log with budget and rate limits.
- **Optional accounts**: single local user with zero configuration by default; token-based login with per-user isolation when required.

## How it works

```
upload ──► parse (text / PDF / vision) ──► review & confirm questions ──► confirm exam
                                                                             │
   review plan ◄── verification questions ◄── report ◄── analysis run ◄──────┘
   wrong-question book                          │
   trends across exams                          └── root causes · knowledge tree · priorities
```

- **Parsing** is per page. Text and PDFs with a text layer are read directly; images and scanned PDFs (no text layer) go through the vision parser if one is configured, otherwise the page is flagged `manual_confirmation_required`. Pages whose subject does not match the exam are excluded from statistics but kept and listed.
- **Confirmation** is the trust boundary: only confirmed questions enter analysis. Reopening a confirmed exam bumps its version and supersedes (never deletes) the old question records, and a report always states which exam versions it was built from.
- **Analysis** maps each question to knowledge points, diagnoses wrong questions, builds the knowledge tree, computes `review_priority` from error *rate* (1/1 wrong outranks 1/10), separates *curriculum scope* from *what this exam actually assessed*, and collects `quality_flags`.
- **Models are optional per role** (see [Model roles](#model-roles)). Without a key, that role uses its rule-based fallback and the output says so — e.g. regex question splitting is always marked `review_required`.

## Quick start (web)

Requirements: Python 3.11+, Node.js 20+ (CI uses 24). Rust is only needed for the desktop build.

```bash
git clone <this repository> exam_diagnosis
cd exam_diagnosis
python -m pip install -e .[dev]
```

Start the backend (JSON API only, on `127.0.0.1:8765`):

```bash
uvicorn exam_diagnosis.app:app --host 127.0.0.1 --port 8765
```

Start the frontend (first time: `npm --prefix frontend install`):

```bash
npm --prefix frontend run dev
```

Open <http://localhost:5173/>. Without any model key the whole pipeline runs on its deterministic fallbacks, which is enough to see every screen.

To enable model inference, either fill in keys on the **Settings** page (`/settings`, stored in `<data_dir>/config.json`, effective immediately) or copy [`.env.local.example`](.env.local.example) to `.env.local` and fill in the roles you want.

## Configuration

### Data directory

All writable data lives under one root, `EXAM_DIAGNOSIS_DATA_DIR` (default `./data`): the SQLite database `exam_diagnosis.sqlite3` (or `EXAM_DIAGNOSIS_DB`), `uploads/`, and `config.json`. The desktop app points it at the user's application-data directory. Public knowledge-base YAML files live in `data/knowledge/` (or `EXAM_DIAGNOSIS_KNOWLEDGE_DIR`).

Settings precedence is **environment (including `.env.local`) > `config.json`**. A value owned by the environment is locked on the settings page — saving it would be inert, so the API refuses with `409 locked_by_environment` instead. In multi-user mode the settings page is read-only and the operator configures the server through the environment. Keys are reported only as *set/unset + source*, never in plain text.

### Model roles

Six text roles, each opt-in and independently configurable through `EXAM_DIAGNOSIS_<ROLE>_{API_KEY,BASE_URL,MODEL,TIMEOUT}`. Setting the `API_KEY` alone activates a role; `BASE_URL`/`MODEL` fall back to the [default provider](#default-provider).

| Role | Tier | What it does | Fallback when off |
|---|---|---|---|
| `split` | fast | Split a page's extracted text into questions | Built-in regex, always marked `review_required` |
| `match` | fast | Pick knowledge points for a question by meaning; merged with keyword hits (`keyword`, `model`, `keyword+model`) | Keyword matching only |
| `analysis` | reasoning | Root cause + explanation + avoidance advice per wrong question (cached per question) | Keyword-based root cause |
| `verify` | reasoning | One real variant question with a worked reference answer per error cluster | Fixed template prompt |
| `grade` | reasoning | Grade a student's answer against a *reviewed* reference answer (0–1, pass ≥ 0.8, one line of feedback); never runs on unreviewed answers | Student self-assessment |
| `report` | fast | Narrative summary from the already-computed statistics only; may not invent data | Structured report without narrative |

All roles share the same rule as the vision parser: not configured, request failed, or response not valid JSON of the expected shape ⇒ the role is treated as unavailable and the fallback is used. Every call — success, failure or blocked — is logged (see [observability](#background-tasks-and-observability)); the attempt record's `graded_by` (`model`/`self`) says who graded.

### Default provider

Every role talks to an OpenAI-compatible `/chat/completions` endpoint. Rather than configuring six roles, point them all at your provider once:

```bash
EXAM_DIAGNOSIS_DEFAULT_BASE_URL=https://api.your-provider.com/v1
EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL=your-reasoning-model   # analysis / verify / grade
EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL=your-fast-model             # split / report / match
```

The same three fields are on the settings page under **Default provider**. Resolution order per role: `EXAM_DIAGNOSIS_<ROLE>_*` → `EXAM_DIAGNOSIS_DEFAULT_*` → the built-in profile in [`llm.py`](exam_diagnosis/llm.py) (`BUILTIN_PROVIDER`). `GET /api/system/status` shows the effective values and where each came from (`env` / `config` / `builtin`).

### Vision parsing (images and scanned PDFs)

Enabled only when all three of `EXAM_DIAGNOSIS_VISION_BASE_URL`, `EXAM_DIAGNOSIS_VISION_API_KEY` and `EXAM_DIAGNOSIS_VISION_MODEL` are set (also editable on the settings page). It sends an OpenAI-compatible multimodal request with the image as a base64 data URL (PNG/JPEG detected from bytes) and requires a JSON response — so the model must genuinely accept image input. Timeout `EXAM_DIAGNOSIS_VISION_TIMEOUT` (default 60 s).

Scanned PDFs (pages but no text layer) are detected automatically and rendered page by page at 150 dpi through the vision parser; results are merged back into one asset (`ocr_text` concatenated per page, questions accumulated in page order, confidence = worst page). Any failed page marks the asset `review_required` and is named in `warning`; if no page succeeds or no vision parser is configured, it degrades to manual entry with the reason spelled out. Each page is a paid call, so at most `EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES` (default 30) pages are processed. Vision calls are logged under the role `vision` and count against the same budget.

### Cost control

Two optional global limits shared by all roles (default: unlimited): `EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET` (successful tokens per UTC day) and `EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE`. A blocked call never touches the network but is still logged with `blocked: true` and the reason (`daily_token_budget_exceeded` / `rate_limited`), and the role's fallback runs.

### Login and multi-user mode

- `POST /api/auth/register` (username 3–32 chars of `[a-z0-9_.-]`, password ≥ 8), `/login`, `/logout`, `GET /api/auth/me`. Bearer tokens expire after `EXAM_DIAGNOSIS_SESSION_TTL_HOURS` (default 720) or on logout. Passwords: pbkdf2-sha256, 200 000 rounds, random salt; only the token's sha256 is stored; five failed logins lock the username for 15 minutes (429); unknown user and wrong password return the same `invalid_credentials`.
- `EXAM_DIAGNOSIS_AUTH_REQUIRED` unset (default): identity comes from the `X-User-Id` header (default `local`) — zero-config single user. A valid token overrides it; an invalid or expired token is a 401, never a silent fallback.
- `EXAM_DIAGNOSIS_AUTH_REQUIRED=1`: every endpoint except register/login needs a valid token and `X-User-Id` is ignored. Resource ownership is enforced in one dependency; another user's resource is a 404, not a 403.

### All environment variables

| Variable | Default | Purpose |
|---|---|---|
| `EXAM_DIAGNOSIS_DATA_DIR` | `./data` | Root of all writable data |
| `EXAM_DIAGNOSIS_DB` | `<data_dir>/exam_diagnosis.sqlite3` | SQLite file |
| `EXAM_DIAGNOSIS_KNOWLEDGE_DIR` | `data/knowledge` | Public knowledge-base YAML directory |
| `EXAM_DIAGNOSIS_<ROLE>_API_KEY` | unset | Activates the role (`SPLIT`, `MATCH`, `ANALYSIS`, `VERIFY`, `GRADE`, `REPORT`) |
| `EXAM_DIAGNOSIS_<ROLE>_BASE_URL` / `_MODEL` / `_TIMEOUT` | provider default / 30 s | Per-role overrides |
| `EXAM_DIAGNOSIS_DEFAULT_BASE_URL` | built-in profile | Provider-wide endpoint |
| `EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL` | built-in profile | Model for `analysis`/`verify`/`grade` |
| `EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL` | built-in profile | Model for `split`/`report`/`match` |
| `EXAM_DIAGNOSIS_VISION_BASE_URL` / `_API_KEY` / `_MODEL` / `_TIMEOUT` | unset / 60 s | Vision parser (all three required) |
| `EXAM_DIAGNOSIS_SCANNED_PDF_MAX_PAGES` | 30 | Page cap for scanned PDFs |
| `EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET` | unlimited | Daily token budget (UTC) |
| `EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE` | unlimited | Rate limit |
| `EXAM_DIAGNOSIS_TASK_WORKERS` | 1 | Background worker threads |
| `EXAM_DIAGNOSIS_AUTH_REQUIRED` | 0 | Require login |
| `EXAM_DIAGNOSIS_SESSION_TTL_HOURS` | 720 | Token lifetime |
| `EXAM_DIAGNOSIS_BACKEND_EXE` | auto | Desktop shell: explicit backend executable path |

## Desktop app (Tauri)

`frontend/src-tauri/` is a Tauri 2 shell around the same React frontend (Windows uses the system WebView2), with the Python backend frozen by PyInstaller and shipped as a resource. Requirements: the Rust toolchain (`cargo`), `npm install` in `frontend/`, and `pip install -e .[desktop]`.

```bash
python scripts/build_backend.py                 # freeze backend → frontend/src-tauri/resources/backend/ and smoke-test it
npm --prefix frontend run desktop:build         # installer under frontend/src-tauri/target/release/bundle/
npm --prefix frontend run desktop:dev           # development window with hot reload
```

`build_backend.py` produces a onedir bundle (~100 MB) and then actually runs it: create a submission → confirm → report → export PDF and Word → generate a QR code. PyInstaller drops data files silently (python-docx templates, PyMuPDF's native library, knowledge YAML, the QR SVG factory), and those only fail at runtime, so the smoke test is not optional.

At runtime the shell launches `exam-diagnosis-backend --port 0 --data-dir <AppData> --parent-pid <shell pid>`, reads the `LISTENING host:port` line it prints, and the frontend fetches that address through the Tauri command `api_base` and polls `/api/health` before showing the UI. On normal exit the shell kills the backend; if the shell is force-killed, the backend's parent-process watchdog exits by itself (Win32 handle wait on Windows, pid polling elsewhere), so no orphan keeps the port. The child's environment is scrubbed of every `EXAM_DIAGNOSIS_*_API_KEY`: desktop keys come from the settings page's `config.json` only. Data lives in `%APPDATA%\com.examdiagnosis.desktop\` on Windows. If no embedded backend is found (development), the shell launches nothing and the frontend falls back to `127.0.0.1:8765`.

**Hard rule: no feature may depend on browser chrome.** The desktop window has no back button, address bar, download manager, tabs or F5. Concretely:

- Navigation: a global nav bar on every page (back / home / knowledge / status / settings / identity); "back" uses the in-app router history. The login page offers "continue without logging in" when login is optional.
- Downloads: every export (PDF/Word/Markdown/HTML/YAML/JSON) goes through `saveBlob()` — a native "Save as" dialog plus the Rust command `save_file` on desktop, `<a download>` only in a browser. Never link `<a href>` directly to an export endpoint: the WebView replaces the whole app with the response and cannot come back, and the link carries no auth.
- No `window.location.assign/reload`, `window.open` or `target=_blank`. Session loss is an `exam-diagnosis:auth-lost` event handled by the router.
- External resources: only Google Fonts, which degrade to system fonts offline. The backend CORS allows the WebView origins (`http://tauri.localhost`, `tauri://localhost`); the CSP allows any local port because the backend port is dynamic.

## Phone photo upload over the LAN

Photographing a paper is a phone job; the app runs on the computer. This channel connects them **without the internet**:

1. Click "Upload from phone" on an exam → a QR code appears (with every candidate IP of this machine listed, because a machine often has Wi-Fi, Ethernet, VPN and WSL addresses and guessing silently is the worst failure mode).
2. Phone on the same Wi-Fi scans it → a tiny self-contained page served by the backend (`/m/<token>`) opens the camera with `<input type="file" capture="environment">` — no `getUserMedia`, so plain HTTP is fine and no certificate is needed.
3. Files land directly on that exam; the desktop counts them live; click "Done, start parsing".

Security model: the main server keeps listening on `127.0.0.1` only. Opening a channel starts a second listener on `0.0.0.0` that stops when the channel is closed or expires (15 min). Requests arriving through that listener may only reach `/m/*` and `/api/mobile-upload/*` — everything else is 403, decided by *which port the request came in on*, so binding your own web deployment to `0.0.0.0` is unaffected. The token in the QR is one-shot and scoped to a single exam (upload only; stored as SHA-256). It is shown once; after a page reload the UI says so and offers "regenerate". Windows asks for a firewall permission the first time — allow *private networks*.

## Knowledge bases

Two layers: the **public** knowledge base shipped as YAML partitions in `data/knowledge/` (keyed by `stage / subject / province / curriculum_year`), and each user's **own** knowledge base in SQLite, isolated per user.

- A partition YAML declares `stage`, `subject`, `province`, `curriculum_year`, `academic_year_range`, `version` and `knowledge_points`; only one partition per key. Adding a YAML enables a new stage/subject/province/curriculum — nothing is hard-coded to the bundled partition. Knowledge points carry `grade_scope`, `dependencies`, keywords and a confidence value, all validated on load.
- A user base either **extends** the public partition (same `knowledge_id` overrides the public definition, e.g. to add keywords) or is **standalone** for a subject/region the product does not cover. Resolution always prefers the user's base; `GET /api/knowledge-partitions` is the user's view (`source: public|user`), and merged points carry `origin`.
- Management: `GET/POST /api/knowledge-bases`, `GET/PUT/DELETE /api/knowledge-bases/{id}`, `POST .../points` (add or override), `DELETE .../points/{knowledge_id}`, `POST /api/knowledge-bases/import` (YAML in the public format), `GET .../export`. Every write is fully validated (422 with the reason); a user base invalidated by a public update is skipped at resolution time and its `problem` field explains why.
- Submissions must target an existing partition (`422 knowledge_partition_not_found` lists the available ones), the grade must be within the partition's `academic_year_range` (`422 grade_outside_partition_scope`), and the academic-year label (`25-26` or `2025-2028`) must cover at least one full year. Every submission, report and export records the partition, curriculum, version and years it used.

Building out content is a later phase; the mechanism is done.

## Reports, exports and the learning loop

- **Report**: wrong questions, root causes with evidence, avoidance advice, knowledge tree (`question_count`, `wrong_count`, `error_rate`, `review_priority`, `priority_score`), `review_order`, `scope_coverage` (each curriculum point `assessed_with_errors` / `assessed_without_errors` / `not_assessed` — the last means *not covered by this paper*, not *not mastered*), `quality_flags` (incomplete material, pages needing confirmation, low confidence, excluded pages, unmapped questions, missing scores — each with `code`, `severity`, `message`), `exam_versions`, and the completeness checklist (wrong questions, original questions, reference answers, student answers, teacher marks).
- **Review gate**: after generation an independent `report.review` step checks knowledge mapping, evidence references and every verification question's answer. Failures are `needs_review`: the answer is withheld everywhere (API returns `answer_withheld` + `withheld_reason`; exports show a placeholder). `POST /api/verification-questions/{id}/review` with `{approved, answer, note}` lifts it; approving an empty answer is refused (`422 cannot_approve_empty_answer`).
- **Exports**: `GET /api/reports/{run_id}/export?format=md|html|pdf|docx`. PDF uses PyMuPDF's Story engine with a bundled CJK fallback font (subsetted with fontTools when available), A4 with automatic pagination; Word uses python-docx with built-in heading/list styles. Both render the same block structure as the Markdown version.
- **Verification & mastery**: each attempt stores the answer, score, pass/fail, time taken and `graded_by`. Mastery is tracked per knowledge point + error cluster: `not_started` → `in_progress` → `provisionally_resolved` → `resolved`, dropping back on failure; every attempt is kept.
- **Review plan**: a spaced-repetition ladder (1, 3, 7, 16, 35 days) per item, advanced or reset by each attempt; the nav bar shows what is due.
- **Wrong-question book**: per submission or across all, grouped by knowledge point, with a practice version that hides answers; exportable.
- **Trends**: error rate, root-cause mix and knowledge-point movement across a student's exams, with a direction judgement (improving / worsening / flat, threshold 0.2). Exams with zero wrong questions are confirmable precisely so the curve can reach zero.
- **Portability**: `GET /api/submissions/{id}/export` (personal data JSON), a zip **bundle** export/import that remaps ids on import and guards against path traversal, and cascading delete that also removes the submission's files under `uploads/`.
- **Deduplication**: identical bytes within one exam are rejected with the existing asset's id, filename and sha256, regardless of filename.

## Background tasks and observability

- Parsing (per-page vision calls) and report generation (many model calls) run on an in-process worker pool, not inside the HTTP request: `POST /api/jobs/{job_id}/enqueue` and `POST /api/analysis-runs/{run_id}/generate` return `202` with a `task_id`; poll `GET /api/tasks/{task_id}` (`queued`/`running`/`completed`/`failed`), `GET /api/jobs/{job_id}` for per-stage progress, or `GET /api/analysis-runs/{run_id}/report` (`202 report_generating` until ready). Duplicate enqueues are refused (`409 job_already_enqueued`) or return the same task. The synchronous `POST /api/jobs/{job_id}/run` remains for scripts.
- No Redis/Celery: `EXAM_DIAGNOSIS_TASK_WORKERS` threads (default 1 — the work is waiting on models, and parallel workers only compete for one budget). Tasks persist in SQLite; on restart anything still `queued`/`running` is marked `failed` (`error: process_restarted`) so no ghost task stays "running" forever.
- `GET /api/model-calls?role=&limit=`: the current user's model calls (role, model, latency, success/failure/blocked, error, tokens) with summary statistics and the live `budget` state. The log is capped at 500 rows.
- `GET /api/system/status`: every role (configured?, effective model/endpoint/timeout, env prefix — never the key), the effective default provider and its sources, auth mode, task counts, budget block reason, storage paths, the user's knowledge partitions and optional features (`qr_code`). The `/status` page shows all of it together.
- `GET /api/health`: public liveness with version, data dir and `auth_required`.

## API overview

All endpoints are JSON under `/api/`; the backend serves no pages except the phone-upload page under `/m/`. The main flow:

```
POST /api/submissions                                   create a submission (stage / subject / province / grade / years)
POST /api/submissions/{sid}/exams                       add an exam
POST /api/exams/{eid}/upload-sessions                   open an upload session
POST /api/upload-sessions/{sid}/assets                  upload files (multipart) · POST .../finish creates the parse job
POST /api/jobs/{jid}/enqueue                            parse in the background · GET /api/jobs/{jid} · POST .../retry
GET  /api/exams/{eid}/pages · PATCH /api/pages/{pid}     material type / include_in_analysis
GET  /api/exams/{eid}/parse-draft · PATCH .../parse-draft/questions/{i}    review the split questions
POST /api/exams/{eid}/confirm                           confirm (422 questions_required / wrong_question_original_required)
POST /api/exams/{eid}/reopen · /reparse                 bump version and supersede records · parse again
PATCH /api/question-records/{rid}                       scores, teacher marks
POST /api/submissions/{sid}/analysis-runs → POST /api/analysis-runs/{rid}/confirm-start → /generate
GET  /api/analysis-runs/{rid}/report · GET /api/reports/{rid}/export?format=md|html|pdf|docx
GET  /api/analysis-runs/{rid}/verification-questions · POST /api/verification-questions/{vid}/attempts · /review
GET  /api/submissions/{sid}/trends · /wrong-questions · /mastery-records · GET /api/review-plan
GET  /api/submissions/{sid}/export · POST /api/submissions/import       portable bundles
POST/GET/DELETE /api/exams/{eid}/mobile-upload           phone upload channel
GET  /api/knowledge-partitions · /api/knowledge-bases…
GET  /api/system/status · /api/model-calls · /api/tasks · GET/PUT /api/settings · GET /api/health
```

Interactive documentation is at `/docs` (FastAPI) while the backend runs.

## Development

```bash
python -m pip install -e .[dev]
python -m pytest -q                 # backend tests, no model keys needed, no paid calls ever
npm --prefix frontend run lint      # oxlint
npm --prefix frontend run build     # tsc -b && vite build
python scripts/check.py             # all three, exactly what CI runs (--backend / --frontend for one side)
```

- Tests never make network calls: `tests/conftest.py` scrubs every `EXAM_DIAGNOSIS_*_API_KEY` and the `EXAM_DIAGNOSIS_DEFAULT_*` variables from the environment, and `app.py` refuses to load `.env.local` under pytest. Each test gets an isolated SQLite file.
- CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs the backend tests on Ubuntu and Windows (the parent-process watchdog differs per platform) and the frontend `npm ci → lint → build`.
- `scripts/verify_server.py` drives a running server through the full flow; `scripts/build_backend.py --smoke-only` re-runs the packaged-backend smoke test.
- Frontend pages are lazy-loaded chunks; responsive below 720 px; antd messages go through `src/feedback.tsx` so they render inside the `App` context.

## Project layout

```
exam_diagnosis/        FastAPI backend
  app.py               routes, task wiring, middleware
  storage.py           SQLite-backed mappings
  llm.py               role-based OpenAI-compatible client, provider defaults
  parser.py            text / PDF / vision parsing
  knowledge.py         public + user knowledge bases, matching
  analysis.py, report.py, export.py, trends.py, review_plan.py, wrong_book.py, transfer.py
  auth.py, settings.py, paths.py, env_file.py
  mobile.py, lan_listener.py     phone upload channel
  tasks.py             in-process task queue
  server.py            desktop entry (--port 0, --data-dir, --parent-pid)
frontend/              React + TypeScript + Vite + antd
  src-tauri/           Tauri 2 desktop shell (Rust)
data/knowledge/        public knowledge-base partitions (YAML)
scripts/               build_backend.py, check.py, verify_server.py, backend_entry.py
tests/                 pytest suite
```

## Current limitations

These hold **today**; items are removed from this list when done.

- The public knowledge base ships one partition (Guangdong, high-school mathematics) with 14 placeholder knowledge points. Other stages, subjects and regions need their own YAML before a submission can be created for them.
- Image and scanned-PDF recognition needs an external OpenAI-compatible vision service; without one, pages are marked for manual confirmation rather than pretending OCR succeeded.
- Automatic grading is a model's judgement against a reviewed reference answer, not symbolic mathematics; it falls back to self-assessment when the answer is unreviewed.
- The desktop build has no auto-update and no code signing (SmartScreen warns once on first run).
- Data stays on one machine — no sync; move between machines with export/import.
- Deleting a submission cascades through its exams, questions, analyses, verification and mastery records and removes its files under `uploads/`.

## Contributing, security, license

- [CONTRIBUTING.md](CONTRIBUTING.md) — how to set up, test and submit changes.
- [SECURITY.md](SECURITY.md) — reporting vulnerabilities; what the application does and does not protect.
- [CHANGELOG.md](CHANGELOG.md).
- License: [Apache License 2.0](LICENSE).
