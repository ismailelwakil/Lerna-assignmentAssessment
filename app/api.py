"""Integration-ready REST API for the Assessment Module (spec §27, §34).

The Streamlit testing app uses the service layer directly; THIS API exposes
the same flows over HTTP for the future EDUnation frontend team. Auth:
`Authorization: Bearer <session-token>` from /auth/register|login."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import get_settings
from .exceptions import AssessError
from .services import (analytics_service, assessment_service, audit_service,
                       auth_service, course_service, remediation_service,
                       submission_service)
from .services import progress_service, weakness_service


# ------------------------------------------------------------------ helpers
def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return auth_service.user_from_token(token)
    except AssessError as exc:
        raise HTTPException(exc.status_code, exc.payload())


def instructor_only(user: dict = Depends(current_user)) -> dict:
    auth_service.require_role(user, "instructor")
    return user


def student_only(user: dict = Depends(current_user)) -> dict:
    auth_service.require_role(user, "student")
    return user


def run(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def guard(exc: AssessError) -> HTTPException:
    return HTTPException(exc.status_code, exc.payload())


# ------------------------------------------------------------------- schemas
class RegisterIn(BaseModel):
    username: str
    password: str
    role: str
    display_name: str = ""


class LoginIn(BaseModel):
    username: str
    password: str


class CourseIn(BaseModel):
    title: str
    subject: str = ""
    code: str = ""
    description: str = ""


class QuestionIn(BaseModel):
    type: str
    text: str
    options: list[str] = Field(default_factory=list)
    correct_answer: dict = Field(default_factory=dict)
    model_answer: str = ""
    explanation: str = ""
    points: float = 1
    topic: str = ""
    difficulty: str = "medium"
    notes: str = ""
    rubric: list[dict] = Field(default_factory=list)


class AssessmentIn(BaseModel):
    kind: str
    title: str
    description: str = ""
    instructions: str = ""
    topic: str = ""
    duration_minutes: int = 30
    passing_score_pct: float = 50
    attempt_limit: int = 1
    release_mode: str = "immediate"
    questions: list[QuestionIn]
    show_score: bool = True
    show_percentage: bool = True
    show_correct_incorrect: bool = True
    show_feedback: bool = True
    show_detailed_feedback: bool = True
    show_evidence: bool = True
    show_correct_answers: bool = False
    show_model_answers: bool = False
    contributes_to_progress: bool = True


class AnswerIn(BaseModel):
    question_id: str
    response: dict


class SubmitIn(BaseModel):
    answers: list[AnswerIn]


class VisibilityIn(BaseModel):
    release_mode: str | None = None
    show_score: bool | None = None
    show_percentage: bool | None = None
    show_correct_incorrect: bool | None = None
    show_feedback: bool | None = None
    show_detailed_feedback: bool | None = None
    show_evidence: bool | None = None
    show_correct_answers: bool | None = None
    show_model_answers: bool | None = None
    contributes_to_progress: bool | None = None


class OverrideIn(BaseModel):
    final_score: float
    comment: str = ""


class ReflectionIn(BaseModel):
    difficult_topic: str = ""
    struggled_question: str = ""
    confidence_level: str = "medium"
    needs_practice: str = ""


class PracticeSubmitIn(BaseModel):
    answers: list[dict]


router = APIRouter(prefix="/api/v1/assess")


# ---------------------------------------------------------------------- auth
@router.post("/auth/register")
def register(payload: RegisterIn):
    try:
        return auth_service.register(payload.username, payload.password,
                                     payload.role, payload.display_name)
    except AssessError as exc:
        raise guard(exc)


@router.post("/auth/login")
def login(payload: LoginIn):
    try:
        return auth_service.login(payload.username, payload.password)
    except AssessError as exc:
        raise guard(exc)


@router.get("/auth/me")
def me(user: dict = Depends(current_user)):
    return user


# ------------------------------------------------------------------- courses
@router.post("/courses")
def create_course(payload: CourseIn, user: dict = Depends(instructor_only)):
    try:
        return course_service.create_course(user, payload.title, payload.subject,
                                            payload.code, payload.description)
    except AssessError as exc:
        raise guard(exc)


@router.get("/courses")
def list_courses(user: dict = Depends(current_user)):
    return {"courses": course_service.list_courses(user)}


@router.post("/courses/{course_id}/materials")
async def upload_material(course_id: str, file: UploadFile = File(...),
                          user: dict = Depends(instructor_only)):
    try:
        content = await file.read()
        result = course_service.upload_material(user, course_id,
                                                file.filename or "material", content)
        result.update(course_service.process_material(result["material_id"]))
        return result
    except AssessError as exc:
        raise guard(exc)


@router.get("/courses/{course_id}/materials")
def list_materials(course_id: str, user: dict = Depends(current_user)):
    try:
        return {"materials": course_service.list_materials(course_id, user)}
    except AssessError as exc:
        raise guard(exc)


# --------------------------------------------------------------- assessments
@router.post("/assessments")
def create_assessment(payload: AssessmentIn, course_id: str,
                      user: dict = Depends(instructor_only)):
    try:
        return assessment_service.create_assessment(
            user, course_id, payload.model_dump())
    except AssessError as exc:
        raise guard(exc)


@router.get("/assessments")
def list_assessments(course_id: str, user: dict = Depends(current_user)):
    return {"assessments": assessment_service.list_assessments(course_id, user)}


@router.get("/assessments/{assessment_id}")
def get_assessment(assessment_id: str, user: dict = Depends(current_user)):
    try:
        data = assessment_service.get_assessment(assessment_id, user)
        include_hidden = user["role"] == "instructor"
        data["questions"] = assessment_service.assessment_questions(
            assessment_id, include_hidden=include_hidden)
        return data
    except AssessError as exc:
        raise guard(exc)


@router.post("/assessments/{assessment_id}/status/{status}")
def set_status(assessment_id: str, status: str,
               user: dict = Depends(instructor_only)):
    try:
        return assessment_service.set_status(user, assessment_id, status)
    except AssessError as exc:
        raise guard(exc)


@router.patch("/assessments/{assessment_id}/visibility")
def update_visibility(assessment_id: str, payload: VisibilityIn,
                      user: dict = Depends(instructor_only)):
    try:
        flags = {k: v for k, v in payload.model_dump().items() if v is not None}
        return assessment_service.update_visibility(user, assessment_id, flags)
    except AssessError as exc:
        raise guard(exc)


# ---------------------------------------------------------------- submissions
@router.post("/assessments/{assessment_id}/submissions")
def submit(assessment_id: str, payload: SubmitIn,
           user: dict = Depends(student_only)):
    try:
        result = submission_service.submit(
            user, assessment_id,
            [{"question_id": a.question_id, "response": a.response}
             for a in payload.answers])
        from .services import evaluation_service
        result["evaluation"] = evaluation_service.evaluate_submission_sync(
            result["submission_id"])
        return result
    except AssessError as exc:
        raise guard(exc)


@router.get("/submissions")
def my_submissions(user: dict = Depends(student_only)):
    return {"submissions": submission_service.my_submissions(user)}


@router.get("/submissions/{submission_id}")
def view_submission(submission_id: str, user: dict = Depends(current_user)):
    try:
        if user["role"] == "student":
            return submission_service.student_view(user, submission_id)
        return submission_service.instructor_view(user, submission_id)
    except AssessError as exc:
        raise guard(exc)


@router.get("/assessments/{assessment_id}/submissions")
def list_submissions(assessment_id: str, user: dict = Depends(instructor_only)):
    try:
        return {"submissions": submission_service.list_submissions(user, assessment_id)}
    except AssessError as exc:
        raise guard(exc)


@router.post("/submissions/{submission_id}/release")
def release(submission_id: str, user: dict = Depends(instructor_only)):
    try:
        return submission_service.release(user, submission_id)
    except AssessError as exc:
        raise guard(exc)


@router.post("/evaluations/{evaluation_id}/override")
def override(evaluation_id: str, payload: OverrideIn,
             user: dict = Depends(instructor_only)):
    try:
        return submission_service.override_score(user, evaluation_id,
                                                 payload.final_score,
                                                 payload.comment)
    except AssessError as exc:
        raise guard(exc)


@router.post("/submissions/{submission_id}/reflection")
def reflection(submission_id: str, payload: ReflectionIn,
               user: dict = Depends(student_only)):
    try:
        return submission_service.save_reflection(user, submission_id,
                                                  payload.model_dump())
    except AssessError as exc:
        raise guard(exc)


# ------------------------------------------------------------------ analytics
@router.get("/assessments/{assessment_id}/analytics")
def analytics(assessment_id: str, user: dict = Depends(instructor_only)):
    try:
        return analytics_service.assessment_analytics(user, assessment_id)
    except AssessError as exc:
        raise guard(exc)


@router.get("/courses/{course_id}/heatmap")
def heatmap(course_id: str, user: dict = Depends(instructor_only)):
    try:
        return analytics_service.heatmap(user, course_id)
    except AssessError as exc:
        raise guard(exc)


@router.get("/audit/{entity_type}/{entity_id}")
def audit(entity_type: str, entity_id: str, user: dict = Depends(instructor_only)):
    return {"history": audit_service.history(entity_type, entity_id)}


# --------------------------------------------------- progress and remediation
@router.get("/progress/{student_id}")
def progress(student_id: str, user: dict = Depends(current_user)):
    if user["role"] == "student" and user["user_id"] != student_id:
        raise HTTPException(404, {"error": {"code": "NOT_FOUND",
                                            "message": "Resource not found."}})
    return progress_service.student_overview(student_id)


@router.get("/me/weaknesses")
def my_weaknesses(user: dict = Depends(student_only)):
    return {"weaknesses": weakness_service.list_weaknesses(user["user_id"]),
            "strengths": weakness_service.list_strengths(user["user_id"])}


@router.post("/practice")
def generate_practice(course_id: str, topic: str,
                      user: dict = Depends(student_only)):
    try:
        return remediation_service.generate_practice(user, topic, course_id)
    except AssessError as exc:
        raise guard(exc)


@router.get("/practice")
def list_practice(user: dict = Depends(student_only)):
    return {"practice": remediation_service.list_practice(user)}


@router.get("/practice/{practice_id}")
def get_practice(practice_id: str, user: dict = Depends(student_only)):
    try:
        return remediation_service.get_practice(user, practice_id)
    except AssessError as exc:
        raise guard(exc)


@router.post("/practice/{practice_id}/submit")
def submit_practice(practice_id: str, payload: PracticeSubmitIn,
                    user: dict = Depends(student_only)):
    try:
        return remediation_service.submit_practice(user, practice_id, payload.answers)
    except AssessError as exc:
        raise guard(exc)


# ------------------------------------------------- production safety check
def _production_config_check() -> None:
    """Fail-closed startup check (deployment safety): in production the module
    must not run with the default dev session-secret."""
    import os
    if os.environ.get("ASSESS_ENV", "").lower() not in {"prod", "production"}:
        return
    secret = os.environ.get("ASSESS_SECRET", "")
    if not secret or secret == "dev-only-secret-change-me" or len(secret) < 24:
        raise RuntimeError(
            "ASSESS_SECRET must be set to a strong random value (>=24 chars) "
            "in production. Generate one: "
            "python -c 'import secrets; print(secrets.token_urlsafe(32))'")


_production_config_check()


# ----------------------------------------------------------------------- app
def create_app() -> FastAPI:
    app = FastAPI(title="Lerna — Assessment Module",
                  description="AI-powered, evidence-based assessment: builder, "
                              "submissions, grading, analytics, progress, "
                              "remediation. Integration-ready (see INTEGRATION.md).",
                  version="1.0.0", docs_url="/docs")
    app.include_router(router)

    @app.exception_handler(AssessError)
    async def assess_error(_request, exc: AssessError):
        return JSONResponse(status_code=exc.status_code, content=exc.payload())

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request, exc):
        return JSONResponse(status_code=422, content={
            "error": {"code": "INVALID_REQUEST",
                      "message": "The request was invalid."}})

    @app.get("/health")
    def health():
        settings = get_settings()
        return {"status": "ok", "module": "assessment",
                "llm": bool(settings.openrouter_api_key or settings.groq_api_key),
                "embeddings": settings.embeddings_available,
                "vector": bool(settings.qdrant_url and settings.qdrant_api_key)}

    return app


app = create_app()
