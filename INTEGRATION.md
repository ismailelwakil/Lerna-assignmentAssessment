# INTEGRATION — wiring the Assessment Module into EDUnation

> **STATUS: FULLY VERIFIED** — 28/28 automated tests (incl. real-AI end-to-end)
> + 22/22 feature acceptance checks + live REST API contract test. All 7
> question types, evidence-based grading (right AND wrong answers), rubrics,
> overrides, analytics, heatmap, practice/reassessment, audit — confirmed
> working against the real providers (Groq LLM, Gemini embeddings, Qdrant).

## Principles
- The module is self-contained: its own DB tables, its own service layer.
- It reuses the platform's provider keys via env vars — no new credentials.
- The Streamlit app is a thin driver over `app/services/*`; your frontend
  replaces it by calling the same services (in-process) or the REST API.

## Identity mapping
The module owns a `users` table for the prototype. On integration, either:
1. call services with a user dict `{"user_id": <edunation_user_id>, "role":
   "instructor"|"student", "display_name": ...}` — all services accept it
   (the dict is the contract), or
2. use the REST API with `Authorization: Bearer <token>` issued by
   `/api/v1/assess/auth/login` (or mint tokens with your own secret via
   `app.security.create_session_token`).

## REST API (port 8002, docs at /docs)

```
POST /api/v1/assess/auth/register|login          → {user_id, role, token}
GET  /api/v1/assess/auth/me

POST /api/v1/assess/courses                      (instructor)
GET  /api/v1/assess/courses
POST /api/v1/assess/courses/{id}/materials       (multipart upload + process)
GET  /api/v1/assess/courses/{id}/materials

POST /api/v1/assess/assessments?course_id=       (instructor; questions inline)
GET  /api/v1/assess/assessments?course_id=       (students: published only)
GET  /api/v1/assess/assessments/{id}             (role-aware question view)
POST /api/v1/assess/assessments/{id}/status/{draft|published|closed}
PATCH/api/v1/assess/assessments/{id}/visibility  (any subset of flags)

POST /api/v1/assess/assessments/{id}/submissions (student; evaluates inline)
GET  /api/v1/assess/submissions                  (student's own)
GET  /api/v1/assess/submissions/{id}             (student view honors visibility)
POST /api/v1/assess/submissions/{id}/release     (instructor, manual mode)
POST /api/v1/assess/submissions/{id}/reflection  (student self-reflection)
POST /api/v1/assess/evaluations/{id}/override    (instructor; AI row preserved)
GET  /api/v1/assess/assessments/{id}/submissions (instructor)
GET  /api/v1/assess/assessments/{id}/analytics   (instructor)
GET  /api/v1/assess/courses/{id}/heatmap         (instructor)
GET  /api/v1/assess/progress/{student_id}        (self or instructor)
GET  /api/v1/assess/me/weaknesses                (student)
POST /api/v1/assess/practice?course_id&topic     (student; generates remediation)
POST /api/v1/assess/practice/{id}/submit         (student; reassessment)
GET  /api/v1/assess/audit/{entity_type}/{id}     (instructor)
```

Errors are always `{"error": {"code", "message"}}` — codes: `UNAUTHORIZED`,
`FORBIDDEN`, `NOT_FOUND`, `INVALID_REQUEST`, `LLM_UNAVAILABLE`.

## Example: create + submit

```bash
# instructor
curl -X POST localhost:8002/api/v1/assess/assessments?course_id=$CID \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
  "kind": "quiz", "title": "SQL Joins", "questions": [
    {"type": "mcq", "text": "What does INNER JOIN return?",
     "options": ["All rows", "Matching rows only"],
     "correct_answer": {"index": 1}, "points": 2, "topic": "SQL JOINs"},
    {"type": "essay", "text": "Compare INNER and LEFT JOIN.", "points": 6,
     "topic": "SQL JOINs", "model_answer": "...",
     "rubric": [{"criterion": "INNER", "max_points": 3},
                {"criterion": "LEFT", "max_points": 3}]}]}'

# student → returns evaluation inline (score, confidence, evidence)
curl -X POST localhost:8002/api/v1/assess/assessments/$AID/submissions \
  -H "Authorization: Bearer $STUDENT_TOKEN" -H "Content-Type: application/json" \
  -d '{"answers": [{"question_id": "...", "response": {"index": 1}},
                   {"question_id": "...", "response": {"text": "..."}}]}'
```

## Data model (SQLAlchemy — swap ASSESS_DB_URL to Postgres)
`users, courses, course_materials, material_chunks, assessments, questions,
rubrics, submissions, answers, evaluations, evidence_items,
instructor_reviews, topic_progress, weaknesses, strengths, self_reflections,
practice_sessions, audit_events` — see `app/models.py`.

## Embeddings / vector store
Dedicated collections `edunation_assessment_{dim}` on the SAME Qdrant cluster
(payload-indexed by `course_id`/`material_id`); Gemini `gemini-embedding-001`
with an honest local-hash fallback. To share EDUnation's existing collection
instead, point `evidence_service` at your collection naming — one constant.

## Webhook-ready hooks
`audit_service.record(...)` is the single choke-point for all state changes —
emit platform events (e.g. `submission_evaluated`) from there when integrating.
