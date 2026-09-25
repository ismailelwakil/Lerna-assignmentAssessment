# 🎓 Lerna

**An AI-Powered, Evidence-Based Assessment Platform for Education**

Lerna transforms assessment from "multiple-choice grading" into a complete learning intelligence loop: instructors build assessments grounded in their own course materials, students take them, and an AI evaluation engine grades every answer **with verifiable evidence, confidence levels, and human-override control** — then turns results into progress tracking, weakness detection, and personalized remediation.

> **Design principle:** the AI never *guesses*. Every graded answer is grounded in the instructor's uploaded course material, the instructor's model answer, or the instructor's rubric — and every evaluation shows exactly which evidence it was based on.

---

## Table of Contents

- [Why Lerna](#why-lerna)
- [Feature Overview](#feature-overview)
- [Architecture](#architecture)
- [The AI Evaluation Engine](#the-ai-evaluation-engine)
- [Data Model](#data-model)
- [Project Structure](#project-structure)
- [Getting Started](#getting-started)
- [Configuration](#configuration)
- [REST API](#rest-api)
- [Example Usage](#example-usage)
- [Testing](#testing)
- [Security](#security)
- [Integrating Lerna into Your Platform](#integrating-lerna-into-your-platform)
- [Graceful Degradation & Failure Recovery](#graceful-degradation--failure-recovery)
- [Known Limitations](#known-limitations)

---

## Why Lerna

Traditional auto-grading handles checkboxes. Lernahandles **learning**:

| Problem with typical AI grading | Lerna's answer |
|---|---|
| AI "hallucinates" correctness with no grounding | Every open-ended answer is graded against **retrieved evidence from the instructor's own course materials** |
| Different wording than the model answer = marked wrong | Model answers are a **semantic reference**, not a string match — equivalent answers earn full credit |
| Essay grading is a black box | Every evaluation returns **citations** (material, page/slide/section, supporting text), a **confidence level**, and per-criterion **rubric results** |
| AI can't be trusted with final grades | Low-confidence or weakly-grounded answers are **flagged for instructor review**; instructors can **override any score** — the original AI evaluation is preserved for audit |
| Results are a dead end | Results feed **progress tracking, weakness detection, personalized practice, and reassessment** |

---

## Feature Overview

### 📝 Assessment Builder (Instructor)

- **Three assessment types** — Quiz, Exam, Assignment
- **Seven question types**, extensible by design:
  - Multiple choice (single correct)
  - Multiple select (partial credit: correct-only selections earn proportional credit)
  - True / False
  - Short answer
  - Long answer
  - Essay
  - Problem-solving / open-ended
- **Per-question configuration**: text, options, correct answer, model answer, explanation, points, topic/skill tag, difficulty, instructor-only notes, and a rubric
- **Rubrics** for open-ended questions — multiple criteria, each with description, expected performance, and max points (validated: criteria must sum to the question's points)
- Assessment-level settings: duration, total points (auto-computed), passing score, attempt limit, instructions, overall topic, and result-release mode (*immediate / after end / manual*)
- Draft → Publish → Close lifecycle

### 📚 Course Materials & Evidence Grounding

- Upload **PDF, DOCX, PPTX, TXT, Markdown, CSV** (validated by extension *and* magic bytes; size-capped)
- Automatic **extraction** (page/slide/section provenance preserved), **chunking**, **embedding** (Gemini), and **vector indexing** (Qdrant) — with an honest score-ranked keyword fallback when vector infrastructure is unavailable
- Material is **course-scoped**: evaluation only ever retrieves evidence from the materials of the course the assessment belongs to

### 🤖 AI Evaluation Engine

- **Objective questions** → deterministic, auditable grading (no AI guessing)
- **Open-ended questions** → evidence-grounded LLM evaluation considering:
  - the instructor's **model answer** (semantic equivalence, not wording)
  - the **rubric** (each criterion graded independently)
  - **retrieved course-material evidence** (with relevance ranking; question-driven retrieval so a vague student answer can't hide the right material)
  - external knowledge **only if explicitly enabled** (`ASSESS_ALLOW_EXTERNAL_KNOWLEDGE`)
- Output per answer: score, correct/partial/incorrect status, concise student-facing feedback, strengths, weaknesses, topic tags, **confidence (high/medium/low)**, needs-review flag with reason, rubric results, and the **evidence items** the grade was based on
- **Insufficient grounding** (no evidence + no model answer + no rubric) → confidence drops to *low* and the answer is flagged for instructor review — never silently invented

### 👁️ Result Visibility Controls

The instructor controls exactly what each student sees, per assessment:

| Flag | Controls |
|---|---|
| `show_score` / `show_percentage` | Score & percentage visibility |
| `show_correct_incorrect` | Correct / partial / incorrect status |
| `show_feedback` / `show_detailed_feedback` | Feedback text & rubric breakdown |
| `show_evidence` | The evidence citations behind each grade |
| `show_correct_answers` / `show_model_answers` | Answer keys & model answers |
| `contributes_to_progress` | Whether results feed mastery tracking |
| `release_mode` | `immediate` / `after_end` / `manual` (manual = instructor releases per submission) |

Visibility is **enforced server-side** in the result-view builder — the UI cannot leak hidden fields.

### 📊 Instructor Analytics

- Submission list with scores, AI confidence, and review status
- Class statistics: submissions, average / highest / lowest, score distribution
- **Question performance** (per-question averages, low-performance flags)
- **Topic performance** (per-topic class averages, struggling/strong student counts)
- **Class-wide common weaknesses** (topic + affected student count + class average)
- **Common strengths**
- **Students needing attention** (flagged reviews, low confidence)
- **Class performance heatmap** — students × topics matrix for spotting learning gaps at a glance
- **Individual student drill-down**: answers, AI evaluation, evidence, feedback, strengths/weaknesses, override any score

### 📈 Student Learning Intelligence

- **Progress tracking** computed from real stored events only (never fabricated): per-topic mastery with trend and improvement vs. previous attempt, status (*Mastered ≥ 80% / Progressing / Needs Practice < 60%*), assessment + practice history
- **Weakness engine** — topic-linked, specific descriptions ("difficulty explaining database normalization and identifying 2NF vs 3NF", never "study more"), severity, lifecycle (open → improving → resolved)
- **Strength engine** — same topic-linking for confirmed strengths
- **Personalized practice** — for any detected weakness, the AI generates a targeted refresher (explanation + worked example + practice MCQs) **grounded in the instructor's course material** — explicitly *remediation*, never a replacement for instructor assessments
- **Reassessment** — practice scored deterministically; before → after improvement tracked and fed back into progress and weakness state
- **Self-reflection** — after submitting, students optionally record perceived difficulty/struggles/confidence; Lerna compares self-perception against the AI analysis ("you thought JOINs were fine — results suggest they need work")

### 🔒 Audit & Control

- **Append-only audit history**: registrations, course/assessment creation, status changes, visibility changes, submissions, evaluations, score overrides, releases, reflections, practice
- **Instructor override**: official score replaces the AI score; the original AI evaluation row is **never overwritten** — full history for every grade
- **Role separation** (instructor/student) and per-student data isolation (students can never read another student's submissions)

---

## Architecture

Lerna is a modular backend with two front doors — a **REST API** for platform integration and a **Streamlit testing UI** for evaluation and demos. Both drive the *same* service layer, so the UI is never a source of truth.

```
┌──────────────────────┐   ┌───────────────────────────────────────────┐
│  Streamlit Testing UI │   │        REST API (FastAPI, /api/v1)        │
│  (evaluation frontend)│   │   auth · courses · assessments · submits  │
└──────────┬───────────┘   └──────────────────────┬────────────────────┘
           │                same service layer     │
           ▼                                        ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              SERVICE LAYER                              │
│  auth_service        registration, login, PBKDF2 hashing, signed tokens │
│  course_service      courses, material upload/extraction/chunking       │
│  evidence_service    embeddings + vector search + keyword fallback      │
│  assessment_service  builder, validation, rubrics, visibility, publish  │
│  submission_service  attempts, release modes, role-aware result views,  │
│                      instructor overrides                               │
│  evaluation_service  ★ AI Evaluation Engine (deterministic + grounded   │
│                      LLM grading, evidence storage, retry/recovery)     │
│  progress_service    topic mastery history & trends                     │
│  weakness_service    strengths/weaknesses detection & lifecycle         │
│  remediation_service personalized practice generation & reassessment    │
│  analytics_service   class stats, common weaknesses, heatmap            │
│  audit_service       append-only event history                          │
├─────────────────────────────────────────────────────────────────────────┤
│                              PROVIDERS                                  │
│  llm.py             OpenRouter → Groq failover chain (JSON-validated    │
│                     structured output, 429 backoff, 402 circuit breaker)│
│  evidence_service   Gemini embeddings → Qdrant (course-filtered),       │
│                     score-ranked keyword fallback                       │
├─────────────────────────────────────────────────────────────────────────┤
│                        DATA LAYER (SQLAlchemy)                          │
│  SQLite by default · ASSESS_DB_URL → PostgreSQL for production          │
└─────────────────────────────────────────────────────────────────────────┘
```

**Key invariants**

- The UI and API are thin: all business logic lives in services.
- Evaluation is **idempotent**: re-running a failed evaluation never duplicates rows; instructor-reviewed answers are never re-graded.
- Progress, strengths, and weaknesses are derived **only** from stored evaluation/practice events.
- Objective questions are always graded deterministically — AI is only consulted for open-ended answers.

---

## The AI Evaluation Engine

Per answer, on every submission:

```
                    ┌─────────────────────────────────────────────┐
                    │ Objective question (MCQ / multi / T-F)?    │
                    └───────┬─────────────────────────┬───────────┘
                       yes  │                         │  no (open-ended)
                            ▼                         ▼
              Deterministic grading        1. Retrieve evidence from the
              (exact, auditable, no AI)       course materials (question-driven
                            │                  query, topic-boosted ranking)
                            │               2. Assemble grounding: model answer
                            │                  + rubric + top evidence
                            │               3. LLM evaluation (OpenRouter→Groq)
                            │                  with strict JSON schema validation
                            │                  and score clamping
                            ▼                         ▼
        ┌──────────────────────────── storage ─────────────────────────────┐
        │ Evaluation row: score · status · feedback · strengths/weaknesses │
        │                · topics · confidence · needs_review · rubric    │
        │                · per-criterion results · model_answer_used      │
        │ Evidence rows: material · page/slide/section · text · relevance │
        └───────────────────────────────┬─────────────────────────────────┘
                                        ▼
             Aggregate submission (totals, confidence, review flags)
                                        ▼
             Progress events + weakness/strength refresh (real results only)
```

**Grounding hierarchy** (strict): model answer → rubric → course-material evidence → external knowledge (only if enabled). If none suffice, the answer is flagged `needs_review` with a stated reason — Lerna does not confidently invent grades.

**Confidence policy:** `low` when there's no evidence *and* no model answer *and* no rubric; `medium` when only a model answer is available; insufficient evidence ⇒ instructor review, never a fabricated grade.

**Failure recovery:** provider outage mid-evaluation leaves a consistent partial state; a retry completes it cleanly (no duplicates, instructor overrides preserved). The student UI offers a one-click *Retry AI evaluation* with answers safely stored.

---

## Data Model

18 SQLAlchemy models (`app/models.py`) — SQLite by default, PostgreSQL via `ASSESS_DB_URL`:

| Model | Purpose |
|---|---|
| `User` | Instructor / student accounts (PBKDF2 hashes) |
| `Course` | Instructor-owned course (subject, code) |
| `CourseMaterial` / `MaterialChunk` | Uploaded material + provenance-preserving chunks |
| `Assessment` | Type, points, passing score, attempts, release mode, all visibility flags |
| `Question` | Type, text, options, correct answer, model answer, points, topic, difficulty |
| `Rubric` | Per-question criteria (1:1 with open-ended questions) |
| `Submission` / `Answer` | Attempts + per-question responses |
| `Evaluation` | AI or instructor grade + feedback + confidence + rubric results |
| `EvidenceItem` | The material citations each evaluation was based on |
| `InstructorReview` | Override records (original + final score, comment) |
| `TopicProgress` | Append-only mastery events (assessment + practice) |
| `Weakness` / `Strength` | Topic-linked detections with lifecycle |
| `SelfReflection` | Student self-perception + AI comparison |
| `PracticeSession` | Generated remediation / reassessment with before→after |
| `AuditEvent` | Append-only history of every state change |

---

## Project Structure

```
lerna/
├── app/
│   ├── api.py                  # FastAPI REST API (24 endpoints)
│   ├── config.py               # Environment-driven configuration
│   ├── db.py                   # SQLAlchemy engine/session (SQLite ⇄ Postgres)
│   ├── models.py               # 18 data models
│   ├── llm.py                  # OpenRouter → Groq client (structured output,
│   │                           #   backoff, circuit breaker)
│   ├── prompts.py              # Evaluation / practice / reflection templates
│   ├── security.py             # Hashing, tokens, upload validation,
│   │                           #   prompt-injection neutralization
│   ├── exceptions.py           # Controlled error envelope
│   └── services/
│       ├── auth_service.py     ├── assessment_service.py
│       ├── course_service.py   ├── submission_service.py
│       ├── evidence_service.py ├── evaluation_service.py
│       ├── progress_service.py ├── weakness_service.py
│       ├── remediation_service.py ├── analytics_service.py
│       └── audit_service.py
├── ui/
│   ├── common.py               # Shared components (cards, badges, evidence UI)
│   ├── instructor.py           # Dashboard, builder, submissions, analytics,
│   │                           #   heatmap, student progress
│   └── student.py              # Assessment taking, results, progress,
│                               #   practice/reassessment, reflection
├── streamlit_app.py            # Testing UI entry point
├── tests/                      # 28 tests incl. real-AI end-to-end
├── INTEGRATION.md              # API contract for platform integration
├── README.md
└── requirements.txt
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- One LLM provider key (OpenRouter **or** Groq — Groq has a free tier)
- Optional: Gemini key (neural embeddings) and Qdrant (vector search) — Lerna degrades gracefully without them

### Install & Run

```bash
pip install -r requirements.txt

# Configure (choose one):
cp .env.example .env        # then add your keys

# Start the REST API
uvicorn app.api:app --port 8002
# → Interactive docs: http://localhost:8002/docs

# Start the testing UI (optional)
streamlit run streamlit_app.py
# → http://localhost:8501
```

### The 5-Minute Demo Flow

1. **Register** as an instructor (role: `instructor`)
2. **Create a course** → **upload a lecture** (PDF/DOCX/PPTX/TXT)
3. **Build an assessment** — e.g. an MCQ + a model-answered short answer + a rubric essay → configure visibility → **Publish**
4. **Log out → register as a student** → take the assessment → submit
5. **Back as instructor**: submissions, per-question AI evaluations **with evidence**, override a score if needed, class analytics, common weaknesses, 🔥 heatmap, student progress
6. **As the student**: 🏋️ generate personalized practice for a weak topic → complete it → watch mastery improve

---

## Configuration

All configuration is environment-driven (`.env`). No keys are ever hard-coded or exposed to the UI.

### Providers

| Variable | Required | Purpose |
|---|---|---|
| `GROQ_API_KEY` | one of | Groq LLM (free tier available) |
| `OPENROUTER_API_KEY` | one of | OpenRouter LLM (fallback/primary) |
| `OPENROUTER_FAST_MODEL` | – | Preferred OpenRouter model |
| `GROQ_MODEL` | – | Preferred Groq model (default `llama-3.3-70b-versatile`) |
| `GEMINI_API_KEY` | – | Neural embeddings (`gemini-embedding-001`) |
| `QDRANT_URL` / `QDRANT_API_KEY` | – | Vector store (course-filtered search) |

### Module

| Variable | Default | Purpose |
|---|---|---|
| `ASSESS_SECRET` | *(required in production)* | HMAC key for session tokens |
| `ASSESS_DB_URL` | `sqlite:///./data/assessment.db` | Database (swap for Postgres) |
| `ASSESS_LLM_TIMEOUT` | `90` | LLM request timeout (s) |
| `ASSESS_MAX_UPLOAD_MB` | `25` | Material upload cap |
| `ASSESS_EMBED_DIM` | `768` | Embedding dimension |
| `ASSESS_ALLOW_EXTERNAL_KNOWLEDGE` | `false` | Allow AI to use non-material knowledge in grading |
| `ASSESS_WEAKNESS_PCT` / `ASSESS_STRENGTH_PCT` | `60` / `80` | Mastery thresholds |
| `ASSESS_EVIDENCE_TOP_K` | `4` | Evidence items retrieved per answer |

---

## REST API

Base path `/api/v1/assess` — 24 endpoints, full interactive docs at `/docs`.

<details>
<summary><b>Authentication & Courses</b></summary>

```
POST /auth/register              # {username, password, role, display_name}
POST /auth/login                 # → {user_id, role, token}
GET  /auth/me
POST /courses                    # instructor only
GET  /courses
POST /courses/{id}/materials     # multipart upload + process + index
GET  /courses/{id}/materials
```
</details>

<details>
<summary><b>Assessments & Submissions</b></summary>

```
POST   /assessments?course_id=            # create (questions inline)
GET    /assessments?course_id=            # students: published only
GET    /assessments/{id}                  # role-aware question view
POST   /assessments/{id}/status/{status}  # draft|published|closed
PATCH  /assessments/{id}/visibility       # any subset of flags
POST   /assessments/{id}/submissions      # student submit → AI evaluation inline
GET    /submissions                       # student's own history
GET    /submissions/{id}                  # student view honors visibility flags
POST   /submissions/{id}/release          # instructor (manual release mode)
POST   /submissions/{id}/reflection       # student self-reflection
GET    /assessments/{id}/submissions      # instructor list
POST   /evaluations/{id}/override         # instructor score override (audited)
```
</details>

<details>
<summary><b>Analytics, Progress & Practice</b></summary>

```
GET  /assessments/{id}/analytics          # class stats, weaknesses, question perf
GET  /courses/{id}/heatmap                # students × topics matrix
GET  /progress/{student_id}               # mastery, trends (self or instructor)
GET  /me/weaknesses                       # student weaknesses + strengths
POST /practice?course_id&topic            # generate remediation (AI, grounded)
GET  /practice / /practice/{id}
POST /practice/{id}/submit                # reassessment scoring + improvement
GET  /audit/{entity_type}/{id}            # append-only history (instructor)
```
</details>

Errors always use the envelope `{"error": {"code", "message"}}`.

---

## Example Usage

**Create an assessment with a rubric essay** *(instructor token)*:

```bash
curl -X POST "localhost:8002/api/v1/assess/assessments?course_id=$CID" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "kind": "exam",
  "title": "SQL Joins Midterm",
  "questions": [
    {"type": "mcq", "text": "What does an INNER JOIN return?",
     "options": ["All rows", "Matching rows only"],
     "correct_answer": {"index": 1}, "points": 2, "topic": "SQL JOINs"},
    {"type": "essay", "text": "Compare INNER and LEFT JOIN.", "points": 6,
     "topic": "SQL JOINs",
     "model_answer": "INNER keeps matches only; LEFT preserves all left rows...",
     "rubric": [{"criterion": "INNER explanation", "max_points": 3},
                {"criterion": "LEFT explanation", "max_points": 3}]}
  ]}'
```

**Submit & get the AI evaluation inline** *(student token)*:

```bash
curl -X POST "localhost:8002/api/v1/assess/assessments/$AID/submissions" \
  -H "Authorization: Bearer $STUDENT_TOKEN" -H "Content-Type: application/json" -d '{
  "answers": [
    {"question_id": "...", "response": {"index": 1}},
    {"question_id": "...", "response": {"text": "An inner join keeps only rows
       matching in both tables, while a left join keeps every left row and pads
       the unmatched right side with NULLs..."}}
  ]}'
```

Response includes per-question score, correct/partial status, feedback, rubric results, confidence, and the **evidence citations** (material name + page + supporting text) each grade was based on — filtered by the instructor's visibility flags.

---

## Testing

```bash
pytest tests -q
```

28 tests covering:

- **Unit**: password/token security, upload validation (type/size/magic bytes), prompt-injection neutralization, deterministic grading & partial credit, builder validation (rubric sums, option counts), visibility redaction, release modes, attempt limits
- **Authorization**: student cannot create courses, students cannot read each other's submissions, non-owner instructors blocked, manual release is instructor-only
- **Resilience**: LLM outage mid-evaluation → consistent partial state → idempotent retry (no duplicates), instructor overrides survive re-evaluation
- **End-to-end (real providers when keys are configured)**: full journey — course → material → publish → two students → AI evaluation with stored evidence + per-criterion rubric results → analytics/common weaknesses → heatmap → override → self-reflection → personalized practice → reassessment → mastery improvement

All 28 pass against the real provider stack; unit/authorization tests run fully offline.

---

## Security

- **Passwords**: PBKDF2-HMAC-SHA256, 120k iterations, per-user salt
- **Sessions**: HMAC-signed tokens with expiry; role embedded and verified
- **Uploads**: extension whitelist + magic-byte validation + size caps
- **Role separation**: instructor/student enforced at every service boundary
- **Isolation**: students can never access another student's submissions (uniform 404s — no existence oracle)
- **Visibility enforcement**: server-side result filtering; the UI cannot leak hidden fields
- **Prompt-injection defense**: course material is wrapped as untrusted data with role-marker neutralization before reaching the LLM
- **Auditability**: append-only event log; score overrides preserve the original AI evaluation
- **Secrets**: environment-only; never hard-coded, never returned by the API, never rendered in the UI

---

## Integrating Lerna into Your Platform

Lerna is designed to sit behind **your** authentication and navigation — it does not own users or sessions at the platform level.

**Two integration paths:**

1. **REST API** — your backend calls the 24 endpoints with `Authorization: Bearer <token>` (issued by `/auth/login`, or mint your own with `app.security.create_session_token`). Full contract: [`INTEGRATION.md`](INTEGRATION.md).
2. **In-process services** — import `app.services.*` directly and pass a user dict `{"user_id", "role", "display_name"}`; all services accept it as the identity contract (maps 1:1 to your platform's user IDs).

**Production switch**: point `ASSESS_DB_URL` at PostgreSQL. No code changes.

The Streamlit UI is a **testing/demo frontend** — replace it with your own; every capability is reachable through the service layer and API.

---

## Graceful Degradation & Failure Recovery

| Condition | Behavior |
|---|---|
| No LLM key configured | UI shows an honest banner; everything except AI grading still works |
| LLM rate-limited (429) | Automatic backoff & retry; honest "temporarily unavailable" message |
| OpenRouter out of credits (402) | 10-minute circuit breaker; Groq serves seamlessly |
| Qdrant unreachable | 60-second circuit breaker; score-ranked keyword evidence retrieval |
| Gemini embeddings unavailable | Local hashed embeddings; honestly reported |
| Provider outage mid-evaluation | Answers saved; one-click **Retry AI evaluation**; no data loss, no duplicate rows |

---

## Known Limitations

- Practice questions are AI-generated for **remediation only** — Lerna never auto-generates instructor assessment questions (by design)
- Free-tier LLM quotas can rate-limit under rapid-fire demo load (retry / wait ~1 min)
- The Streamlit interface is a testing tool, not the final frontend
- Neural embeddings and vector search require the optional Gemini/Qdrant keys; without them, evidence retrieval falls back to ranked keyword search (honestly labeled in logs)

---

## License

Proprietary — © Lerna. All rights reserved.
