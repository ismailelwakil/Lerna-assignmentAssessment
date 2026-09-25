"""AI Evaluation Engine (spec §10-§12).

Per answer: retrieve course-material evidence → deterministic grade for
objective questions / evidence-grounded LLM evaluation for open-ended →
score + concise user-facing feedback + evidence stored + confidence +
needs-review flags. Never exposes chain-of-thought."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..core_log import log_event
from ..db import db
from ..exceptions import LLMUnavailableError, NotFoundError, ValidationError
from ..llm import llm
from ..models import (Answer, Assessment, Evaluation, EvidenceItem, Question,
                      Rubric)
from ..prompts import evaluation_prompt
from . import evidence_service, progress_service, weakness_service

OBJECTIVE = {"mcq", "multi_select", "true_false"}


class EvalResult(BaseModel):
    score: float = 0
    correct_status: str = "incorrect"
    feedback: str = ""
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    confidence: str = "medium"
    needs_review: bool = False
    review_reason: str = ""
    rubric_results: list[dict] = Field(default_factory=list)
    evidence_refs: list[int] = Field(default_factory=list)

    @field_validator("score")
    @classmethod
    def _score_ok(cls, value: float) -> float:
        return max(0.0, value)

    @field_validator("correct_status")
    @classmethod
    def _status_ok(cls, value: str) -> str:
        return value if value in {"correct", "partial", "incorrect"} else "partial"

    @field_validator("confidence")
    @classmethod
    def _conf_ok(cls, value: str) -> str:
        return value if value in {"high", "medium", "low"} else "medium"


# ------------------------------------------------- deterministic (objective)
def grade_objective(question: Question, response: dict) -> tuple[float, str]:
    """Deterministic, auditable grading for choice questions — no AI guessing."""
    correct = question.correct_answer or {}
    qtype = question.type
    if qtype == "mcq":
        chosen = response.get("index")
        return (question.points, "correct") if chosen == correct.get("index") else (0.0, "incorrect")
    if qtype == "true_false":
        chosen = str(response.get("value", "")).lower()
        right = str(correct.get("value", "")).lower()
        return (question.points, "correct") if chosen == right else (0.0, "incorrect")
    if qtype == "multi_select":
        chosen = set(response.get("indices") or [])
        right = set(correct.get("indices") or [])
        if not chosen:
            return 0.0, "incorrect"
        if chosen == right:
            return question.points, "correct"
        if chosen.issubset(right):  # partial: selected only correct ones
            return round(question.points * len(chosen) / max(1, len(right)), 2), "partial"
        return 0.0, "incorrect"
    raise ValidationError("Not an objective question.")


# ------------------------------------------------------------- AI (open-ended)
async def evaluate_open(question: Question, rubric_criteria: list[dict],
                        student_answer: str, course_id: str) -> tuple[EvalResult, list]:
    settings = get_settings()
    answer_text = (student_answer or "").strip()
    if not answer_text:
        return EvalResult(score=0, correct_status="incorrect",
                          feedback="No answer was provided.",
                          confidence="high", needs_review=False), []

    # 1. evidence retrieval from the instructor's course materials.
    # The QUESTION (+topic) drives the lookup so evidence points at the
    # material section defining the concept; the student's answer only
    # boosts ranking — a long or vague answer must never dilute retrieval.
    evidence = evidence_service.retrieve(
        course_id, question.text, k=settings.evidence_top_k,
        boost_text=f"{question.topic} {answer_text[:200]}")
    evidence_texts = [e.text for e in evidence]

    # 2. LLM evaluation (model answer + rubric + evidence grounded)
    messages = evaluation_prompt(
        question_text=question.text, question_type=question.type,
        points=question.points, student_answer=answer_text,
        model_answer=question.model_answer, rubric_criteria=rubric_criteria,
        evidence_texts=evidence_texts, allow_external=settings.allow_external_knowledge)
    result = await llm.structured(messages, EvalResult)

    # 3. sanity clamps + honest confidence handling
    result.score = min(max(result.score, 0.0), question.points)
    if rubric_results := result.rubric_results:
        capped = min(sum(min(float(r.get("points", 0)), float(r.get("max_points", 0)))
                         for r in rubric_results
                         if r.get("max_points") is not None), question.points)
        if abs(capped - result.score) > 0.01:
            result.score = capped  # rubric criteria are authoritative
    if not evidence and not question.model_answer and not rubric_criteria:
        result.confidence = "low"
        result.needs_review = True
        result.review_reason = ("No course evidence, model answer, or rubric "
                                "was available to verify this answer.")
    if not evidence and question.model_answer and not rubric_criteria:
        result.confidence = "medium"
    return result, evidence


# ----------------------------------------------------------------- submission
async def evaluate_submission(submission_id: str) -> dict:
    """Evaluate every answer of a submission; aggregate; update progress,
    strengths/weaknesses; store evidence; audit. Preserves all AI rows for
    history — instructor overrides are stored separately."""
    settings = get_settings()
    with db().session_scope() as session:
        submission = session.get(__import__("app.models", fromlist=["Submission"]).Submission, submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.")
        assessment = session.get(Assessment, submission.assessment_id)
        assessment_id, course_id = assessment.id, assessment.course_id
        student_id = submission.student_id
        answers = session.query(Answer).filter_by(submission_id=submission_id).all()
        answer_ids = [(a.id, a.question_id, a.response) for a in answers]

    # Idempotent retry: a previous evaluation run may have failed part-way
    # (provider outage / rate limit). Discard its partial AI rows so a retry
    # never duplicates evaluations. Instructor-reviewed rows are PRESERVED and
    # remain the official results for their answers.
    with db().session_scope() as session:
        session.query(Evaluation).filter(
            Evaluation.submission_id == submission_id,
            Evaluation.evaluator == "ai").delete(synchronize_session=False)
        instructor_rows = {row.answer_id: row for row in session.query(Evaluation).filter(
            Evaluation.submission_id == submission_id,
            Evaluation.evaluator == "instructor").all()}

    total, maximum = 0.0, 0.0
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    overall_confidence = "high"
    needs_review = False
    strengths, weaknesses, topics = [], [], []
    per_question = []

    for answer_id, question_id, response in answer_ids:
        question, rubric_criteria = __import__(
            "app.services.assessment_service", fromlist=["get_question"]).get_question(question_id)
        instructor_row = instructor_rows.get(answer_id)
        if instructor_row is not None:
            # Instructor already reviewed this answer — keep the official
            # (instructor-approved) result; do not re-run AI grading on it.
            score, status = instructor_row.score, instructor_row.correct_status
            confidence = instructor_row.confidence or "high"
            feedback, review_reason = instructor_row.feedback, ""
            strengths_w = instructor_row.strengths or []
            weaknesses_w = instructor_row.weaknesses or []
            q_topics = instructor_row.topics or []
            rubric_results = instructor_row.rubric_results or []
            model_used = instructor_row.model_answer_used
            evidence = []  # the row's stored evidence stays attached to it
            eid = instructor_row.id
        elif question.type in OBJECTIVE:
            score, status = grade_objective(question, response)
            confidence = "high"
            review_reason = ""
            evidence = evidence_service.retrieve(
                course_id, question.text, k=2,
                boost_text=f"{question.topic} {' '.join(question.options or [])}")
            feedback = question.explanation or (
                "Correct — well done." if status == "correct" else
                ("Partially correct." if status == "partial" else "Incorrect."))
            strengths_w, weaknesses_w, q_topics = [], [], [question.topic] if question.topic else []
            if status == "correct" and question.topic:
                strengths_w = [f"Correctly answered a {question.topic} question"]
            elif status != "correct" and question.topic:
                weaknesses_w = [f"{question.topic}: answered incorrectly"]
            rubric_results, model_used = [], bool(question.model_answer)
        else:
            student_answer = str(response.get("text", ""))
            result, evidence = await evaluate_open(
                question, rubric_criteria, student_answer, course_id)
            score, status = result.score, result.correct_status
            confidence = result.confidence
            review_reason = result.review_reason
            feedback = result.feedback
            strengths_w, weaknesses_w = result.strengths, result.weaknesses
            q_topics = result.topics or ([question.topic] if question.topic else [])
            rubric_results, model_used = result.rubric_results, bool(question.model_answer)
            if result.needs_review:
                needs_review = True
            if confidence_rank.get(confidence, 2) < confidence_rank[overall_confidence]:
                overall_confidence = confidence

        # store evaluation + evidence copies (audit-grade, never overwritten)
        # instructor-reviewed answers keep their existing official row — a
        # retry never re-grades nor duplicates them.
        if instructor_row is None:
            with db().session_scope() as session:
                evaluation = Evaluation(
                    id=__import__("app.security", fromlist=["new_id"]).new_id(),
                    answer_id=answer_id, submission_id=submission_id,
                    question_id=question_id, evaluator="ai", score=score,
                    max_points=question.points, correct_status=status,
                    feedback=feedback, strengths=strengths_w, weaknesses=weaknesses_w,
                    topics=q_topics, confidence=confidence,
                    needs_review=bool(review_reason), review_reason=review_reason,
                    rubric_results=rubric_results, model_answer_used=model_used)
                session.add(evaluation)
                session.flush()
                eid = evaluation.id
                for ev in evidence[: settings.evidence_top_k]:
                    session.add(EvidenceItem(
                        id=__import__("app.security", fromlist=["new_id"]).new_id(),
                        evaluation_id=evaluation.id, material_id=ev.material_id,
                        material_title=ev.material_title, page=ev.page, slide=ev.slide,
                        section=ev.section, text=ev.text[:4000], relevance=ev.relevance,
                        used=True))

        total += score
        maximum += question.points
        strengths += [s for s in strengths_w if s not in strengths]
        weaknesses += [w for w in weaknesses_w if w not in weaknesses]
        topics += [t for t in q_topics if t and t not in topics]
        per_question.append({"question_id": question_id, "evaluation_id": eid,
                             "score": score, "max": question.points, "status": status})

    percentage = round(100 * total / maximum, 1) if maximum else 0.0
    settings = get_settings()
    with db().session_scope() as session:
        submission = session.get(__import__("app.models", fromlist=["Submission"]).Submission, submission_id)
        submission.status = "reviewed" if instructor_rows else "evaluated"
        submission.evaluated_at = __import__("app.models", fromlist=["utcnow"]).utcnow()
        submission.total_score, submission.max_score = round(total, 2), maximum
        submission.percentage = percentage
        submission.ai_confidence = overall_confidence
        submission.needs_review = needs_review
        submission.strengths, submission.weaknesses, submission.topics = strengths, weaknesses, topics

    # progress + strengths/weaknesses engines (real stored results only)
    if __import__("app.models", fromlist=["Assessment"]).Assessment:
        with db().session_scope() as session:
            contributes = session.get(Assessment, assessment_id).contributes_to_progress
        if contributes:
            progress_service.record_assessment(
                student_id, course_id, assessment_id, per_question)
            weakness_service.refresh(student_id, course_id)

    log_event("submission_evaluated", submission=submission_id[:12],
              score=total, max=maximum, confidence=overall_confidence,
              needs_review=needs_review)
    return {"submission_id": submission_id, "total_score": round(total, 2),
            "max_score": maximum, "percentage": percentage,
            "confidence": overall_confidence, "needs_review": needs_review,
            "strengths": strengths, "weaknesses": weaknesses, "topics": topics,
            "questions": per_question}


def evaluate_submission_sync(submission_id: str) -> dict:
    """Synchronous entry point for the Streamlit app and tests."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run,
                               evaluate_submission(submission_id)).result()
    return asyncio.run(evaluate_submission(submission_id))
