"""Remediation / Personalized Practice / Reassessment (spec §21-§22).

Practice content is generated per weak topic, grounded in course material —
explicitly NOT the instructor's assessment. Practice scores update progress;
before/after improvement is computed from real stored events."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..db import db
from ..exceptions import LLMUnavailableError, NotFoundError, ValidationError
from ..llm import llm
from ..models import (AuditEvent, Course, PracticeSession, TopicProgress)
from ..prompts import practice_prompt
from ..security import new_id
from . import evidence_service, progress_service, weakness_service


class PracticeContent(BaseModel):
    explanation: str = ""
    example: str = ""
    questions: list[dict] = Field(default_factory=list)

    @field_validator("questions")
    @classmethod
    def _questions_ok(cls, value: list[dict]) -> list[dict]:
        cleaned = []
        for question in value:
            if not (question.get("text") or "").strip():
                continue
            if not question.get("options") or len(question["options"]) < 2:
                continue
            if not isinstance(question.get("correct_index"), int):
                continue
            cleaned.append({"type": "mcq", "text": str(question["text"])[:1000],
                            "options": [str(o)[:300] for o in question["options"][:5]],
                            "correct_index": question["correct_index"],
                            "explanation": str(question.get("explanation", ""))[:800]})
        if not cleaned:
            raise ValueError("no valid practice questions generated")
        return cleaned[:5]


def generate_practice(student: dict, topic: str, course_id: str) -> dict:
    """Build targeted practice for a weak topic (LLM + course evidence)."""
    topic = (topic or "").strip()
    if not topic:
        raise ValidationError("A topic is required.")
    weaknesses = {w["topic"]: w for w in weakness_service.list_weaknesses(student["user_id"])}
    if topic not in weaknesses:
        # also allow practicing any tracked topic with low mastery
        mastery = {m["topic"]: m for m in progress_service.topic_mastery(
            student["user_id"], course_id)}
        if topic not in mastery or mastery[topic]["current_pct"] >= 95:
            raise ValidationError(
                "Practice is generated for your weak topics — take an assessment first.")
    with db().session_scope() as session:
        course = session.get(Course, course_id)
        course_title = course.title if course else ""
    if course is None:
        raise NotFoundError("Course not found.")

    evidence = evidence_service.retrieve(course_id, topic, k=3)
    content = _generate(topic, weaknesses.get(topic, {}).get(
        "description", f"{topic} needs practice"), course_title,
        [e.text for e in evidence])

    before_pct = _latest_pct(student["user_id"], course_id, topic)
    with db().session_scope() as session:
        practice = PracticeSession(
            id=new_id(), student_id=student["user_id"], course_id=course_id,
            topic=topic[:200], kind="remediation",
            explanation=content.explanation[:4000], example=content.example[:4000],
            questions=content.questions,
            materials_used=[{"material": e.material_title, "page": e.page,
                             "slide": e.slide} for e in evidence],
            before_pct=before_pct)
        session.add(practice)
        session.add(AuditEvent(actor_id=student["user_id"], event_type="practice_generated",
                               entity_type="practice", entity_id=practice.id,
                               detail={"topic": topic}))
        pid = practice.id
    return {"practice_id": pid, "topic": topic, "explanation": content.explanation,
            "example": content.example, "questions": content.questions,
            "materials_used": practice.materials_used, "before_pct": before_pct}


def _generate(topic: str, description: str, course_title: str,
              evidence_texts: list[str]) -> PracticeContent:
    try:
        return llm_sync_structured(practice_prompt(
            topic=topic, weakness_description=description,
            course_title=course_title, evidence_texts=evidence_texts), PracticeContent)
    except LLMUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise LLMUnavailableError(detail_log=f"practice generation failed: {exc}") from exc


def llm_sync_structured(messages, schema):
    import asyncio
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, llm.structured(messages, schema)).result()
    return asyncio.run(llm.structured(messages, schema))


def _latest_pct(student_id: str, course_id: str, topic: str) -> float | None:
    with db().session_scope() as session:
        events = session.query(TopicProgress).filter_by(
            student_id=student_id, course_id=course_id, topic=topic
        ).order_by(TopicProgress.created_at).all()
    return events[-1].score_pct if events else None


def get_practice(student: dict, practice_id: str) -> dict:
    with db().session_scope() as session:
        practice = session.get(PracticeSession, practice_id)
        if practice is None or practice.student_id != student["user_id"]:
            raise NotFoundError("Practice session not found.")
        return {"practice_id": practice.id, "topic": practice.topic,
                "kind": practice.kind, "explanation": practice.explanation,
                "example": practice.example, "questions": practice.questions,
                "materials_used": practice.materials_used,
                "status": practice.status, "score_pct": practice.score_pct,
                "before_pct": practice.before_pct, "after_pct": practice.after_pct,
                "improvement": practice.improvement}


def list_practice(student: dict) -> list[dict]:
    with db().session_scope() as session:
        rows = session.query(PracticeSession).filter_by(
            student_id=student["user_id"]).order_by(
            PracticeSession.created_at.desc()).all()
        return [{"practice_id": p.id, "topic": p.topic, "status": p.status,
                 "before_pct": p.before_pct, "after_pct": p.after_pct,
                 "improvement": p.improvement,
                 "created_at": p.created_at.isoformat()} for p in rows]


def submit_practice(student: dict, practice_id: str, answers: list[dict]) -> dict:
    """Grade practice MCQs deterministically, update progress, compute
    before/after improvement from real events (reassessment flow §22)."""
    with db().session_scope() as session:
        practice = session.get(PracticeSession, practice_id)
        if practice is None or practice.student_id != student["user_id"]:
            raise NotFoundError("Practice session not found.")
        if practice.status == "completed":
            raise ValidationError("This practice session is already completed.")
        questions = practice.questions
        course_id, topic = practice.course_id, practice.topic

    if len(answers) != len(questions):
        raise ValidationError(f"Answer all {len(questions)} practice questions.")
    correct = 0
    for index, answer in enumerate(answers):
        if int(answer.get("choice", -1)) == questions[index]["correct_index"]:
            correct += 1
    score_pct = round(100 * correct / len(questions), 1) if questions else 0.0

    progress_service.record_practice(student["user_id"], course_id, topic, score_pct)
    after = _latest_pct(student["user_id"], course_id, topic)
    # after = weighted latest event including this practice
    improvement = (round(score_pct - (practice.before_pct or 0), 1)
                   if practice.before_pct is not None else None)
    with db().session_scope() as session:
        row = session.get(PracticeSession, practice_id)
        row.status, row.score_pct, row.after_pct = "completed", score_pct, score_pct
        row.improvement = improvement
        row.kind = "reassessment"
        row.completed_at = __import__("app.models", fromlist=["utcnow"]).utcnow()
        session.add(AuditEvent(actor_id=student["user_id"], event_type="practice_completed",
                               entity_type="practice", entity_id=practice_id,
                               detail={"topic": topic, "score_pct": score_pct}))
    weakness_service.refresh(student["user_id"], course_id)
    remaining = [w["topic"] for w in weakness_service.list_weaknesses(student["user_id"])]
    return {"practice_id": practice_id, "score_pct": score_pct,
            "before_pct": practice.before_pct, "after_pct": score_pct,
            "improvement": improvement, "topic": topic,
            "topic_still_weak": topic in remaining,
            "remaining_weaknesses": remaining}
