"""Assessment Service — instructor builder (quiz/exam/assignment), questions,
model answers, rubrics, visibility controls, publish (spec §3-§6, §14)."""
from __future__ import annotations

from ..db import db
from ..exceptions import ForbiddenError, NotFoundError, ValidationError
from ..models import (Assessment, AuditEvent, Course, Question, Rubric)
from ..security import new_id
from .course_service import owned_course

QUESTION_TYPES = {"mcq", "multi_select", "true_false", "short_answer",
                  "long_answer", "essay", "problem"}


def remap_choice_options(raw_options: list[str]) -> tuple[list[str], dict[int, int]]:
    """Drop blank option inputs and remap original positions → filtered indices.

    Used by the builder UI so instructors may leave trailing/inner option
    fields empty without breaking correct-answer indices.
    Returns (non_blank_options, {original_position: filtered_index})."""
    options: list[str] = []
    remap: dict[int, int] = {}
    for position, value in enumerate(raw_options or []):
        if (value or "").strip():
            remap[position] = len(options)
            options.append(value.strip())
    return options, remap
OPEN_TYPES = {"short_answer", "long_answer", "essay", "problem"}


def _validate_question(data: dict, index: int) -> dict:
    qtype = data.get("type")
    if qtype not in QUESTION_TYPES:
        raise ValidationError(f"Question {index + 1}: unknown type '{qtype}'.")
    text = (data.get("text") or "").strip()
    if len(text) < 3:
        raise ValidationError(f"Question {index + 1}: question text is required.")
    correct = data.get("correct_answer") or {}
    if qtype == "mcq":
        options = data.get("options") or []
        if len(options) < 2:
            raise ValidationError(f"Question {index + 1}: MCQ needs ≥2 options.")
        if not isinstance(correct.get("index"), int) or not 0 <= correct["index"] < len(options):
            raise ValidationError(f"Question {index + 1}: MCQ needs correct_answer.index.")
    elif qtype == "multi_select":
        options = data.get("options") or []
        if len(options) < 3:
            raise ValidationError(f"Question {index + 1}: multi-select needs ≥3 options.")
        indices = correct.get("indices") or []
        if not indices or not all(isinstance(i, int) and 0 <= i < len(options) for i in indices):
            raise ValidationError(f"Question {index + 1}: multi-select needs correct_answer.indices.")
    elif qtype == "true_false":
        if str(correct.get("value", "")).lower() not in {"true", "false"}:
            raise ValidationError(f"Question {index + 1}: true/false needs correct_answer.value.")
    points = data.get("points", 1)
    try:
        points = float(points)
    except (TypeError, ValueError):
        raise ValidationError(f"Question {index + 1}: points must be a number.")
    if points <= 0 or points > 100:
        raise ValidationError(f"Question {index + 1}: points must be 0<p≤100.")
    rubric = data.get("rubric") or []
    if rubric:
        total = 0.0
        for criterion in rubric:
            try:
                total += float(criterion.get("max_points", 0))
            except (TypeError, ValueError):
                raise ValidationError(f"Question {index + 1}: rubric max_points must be numbers.")
            if not (criterion.get("criterion") or "").strip():
                raise ValidationError(f"Question {index + 1}: rubric criterion needs a name.")
        if abs(total - points) > 0.01:
            raise ValidationError(
                f"Question {index + 1}: rubric criteria points ({total:g}) must sum "
                f"to the question points ({points:g}).")
    return {"type": qtype, "text": text[:4000],
            "options": [str(o)[:500] for o in (data.get("options") or [])],
            "correct_answer": correct, "model_answer": (data.get("model_answer") or "")[:6000],
            "explanation": (data.get("explanation") or "")[:2000],
            "points": points, "topic": (data.get("topic") or "").strip()[:200],
            "difficulty": data.get("difficulty", "medium"),
            "notes": (data.get("notes") or "")[:2000], "rubric": rubric}


def create_assessment(instructor: dict, course_id: str, payload: dict) -> dict:
    owned_course(course_id, instructor)
    if instructor["role"] != "instructor":
        raise ForbiddenError("Only instructors can create assessments.")
    if payload.get("kind") not in {"quiz", "exam", "assignment"}:
        raise ValidationError("kind must be quiz, exam or assignment.")
    questions = payload.get("questions") or []
    if not questions:
        raise ValidationError("An assessment needs at least one question.")
    validated = [_validate_question(q, i) for i, q in enumerate(questions)]
    total_points = sum(q["points"] for q in validated)

    with db().session_scope() as session:
        assessment = Assessment(
            id=new_id(), course_id=course_id, instructor_id=instructor["user_id"],
            kind=payload["kind"], title=(payload.get("title") or "Untitled")[:200],
            description=(payload.get("description") or "")[:2000],
            instructions=(payload.get("instructions") or "")[:2000],
            topic=(payload.get("topic") or "")[:200],
            duration_minutes=int(payload.get("duration_minutes") or 30),
            total_points=total_points,
            passing_score_pct=float(payload.get("passing_score_pct") or 50),
            attempt_limit=max(1, int(payload.get("attempt_limit") or 1)),
            release_mode=payload.get("release_mode", "immediate"),
            show_score=payload.get("show_score", True),
            show_percentage=payload.get("show_percentage", True),
            show_correct_incorrect=payload.get("show_correct_incorrect", True),
            show_feedback=payload.get("show_feedback", True),
            show_detailed_feedback=payload.get("show_detailed_feedback", True),
            show_evidence=payload.get("show_evidence", True),
            show_correct_answers=payload.get("show_correct_answers", False),
            show_model_answers=payload.get("show_model_answers", False),
            contributes_to_progress=payload.get("contributes_to_progress", True))
        session.add(assessment)
        for index, question in enumerate(validated):
            row = Question(id=new_id(), assessment_id=assessment.id, index=index,
                           type=question["type"], text=question["text"],
                           options=question["options"],
                           correct_answer=question["correct_answer"],
                           model_answer=question["model_answer"],
                           explanation=question["explanation"],
                           points=question["points"], topic=question["topic"],
                           difficulty=question["difficulty"], notes=question["notes"])
            session.add(row)
            if question["rubric"]:
                session.add(Rubric(id=new_id(), question_id=row.id,
                                   criteria=question["rubric"]))
        session.add(AuditEvent(
            actor_id=instructor["user_id"], event_type="assessment_created",
            entity_type="assessment", entity_id=assessment.id,
            detail={"title": assessment.title, "questions": len(validated),
                    "points": total_points}))
        aid = assessment.id
    return {"assessment_id": aid, "total_points": total_points,
            "questions": len(validated)}


def set_status(instructor: dict, assessment_id: str, status: str) -> dict:
    assessment = _owned_assessment(assessment_id, instructor)
    if status not in {"draft", "published", "closed"}:
        raise ValidationError("status must be draft, published or closed.")
    if status == "published" and not assessment_questions(assessment_id):
        raise ValidationError("Cannot publish an assessment without questions.")
    with db().session_scope() as session:
        row = session.get(Assessment, assessment_id)
        row.status = status
        session.add(AuditEvent(actor_id=instructor["user_id"],
                               event_type="assessment_status_changed",
                               entity_type="assessment", entity_id=assessment_id,
                               detail={"status": status}))
    return {"assessment_id": assessment_id, "status": status}


def update_visibility(instructor: dict, assessment_id: str, flags: dict) -> dict:
    _owned_assessment(assessment_id, instructor)
    allowed = {"release_mode", "show_score", "show_percentage",
               "show_correct_incorrect", "show_feedback", "show_detailed_feedback",
               "show_evidence", "show_correct_answers", "show_model_answers",
               "contributes_to_progress"}
    updates = {k: v for k, v in flags.items() if k in allowed}
    if not updates:
        raise ValidationError("No valid visibility flags provided.")
    with db().session_scope() as session:
        row = session.get(Assessment, assessment_id)
        for key, value in updates.items():
            setattr(row, key, value)
        session.add(AuditEvent(actor_id=instructor["user_id"],
                               event_type="visibility_changed",
                               entity_type="assessment", entity_id=assessment_id,
                               detail=updates))
    return {"assessment_id": assessment_id, **updates}


def _owned_assessment(assessment_id: str, instructor: dict) -> Assessment:
    with db().session_scope() as session:
        assessment = session.get(Assessment, assessment_id)
        if assessment is None:
            raise NotFoundError("Assessment not found.")
        if instructor["role"] == "instructor" and assessment.instructor_id != instructor["user_id"]:
            raise NotFoundError("Assessment not found.")
        return assessment


def get_assessment(assessment_id: str, instructor: dict | None = None) -> dict:
    assessment = _owned_assessment(assessment_id, instructor) if instructor else _get(assessment_id)
    return assessment_summary(assessment)


def _get(assessment_id: str) -> Assessment:
    with db().session_scope() as session:
        assessment = session.get(Assessment, assessment_id)
        if assessment is None:
            raise NotFoundError("Assessment not found.")
        return assessment


def assessment_summary(assessment: Assessment) -> dict:
    return {"assessment_id": assessment.id, "course_id": assessment.course_id,
            "kind": assessment.kind, "title": assessment.title,
            "description": assessment.description,
            "instructions": assessment.instructions, "topic": assessment.topic,
            "duration_minutes": assessment.duration_minutes,
            "total_points": assessment.total_points,
            "passing_score_pct": assessment.passing_score_pct,
            "attempt_limit": assessment.attempt_limit, "status": assessment.status,
            "release_mode": assessment.release_mode,
            "visibility": {
                "show_score": assessment.show_score,
                "show_percentage": assessment.show_percentage,
                "show_correct_incorrect": assessment.show_correct_incorrect,
                "show_feedback": assessment.show_feedback,
                "show_detailed_feedback": assessment.show_detailed_feedback,
                "show_evidence": assessment.show_evidence,
                "show_correct_answers": assessment.show_correct_answers,
                "show_model_answers": assessment.show_model_answers,
                "contributes_to_progress": assessment.contributes_to_progress},
            "created_at": assessment.created_at.isoformat()}


def list_assessments(course_id: str, instructor: dict | None = None) -> list[dict]:
    with db().session_scope() as session:
        query = session.query(Assessment).filter(Assessment.course_id == course_id)
        if instructor and instructor["role"] == "instructor":
            query = query.filter(Assessment.instructor_id == instructor["user_id"])
        else:
            query = query.filter(Assessment.status == "published")
        return [assessment_summary(a) for a in query.order_by(
            Assessment.created_at.desc()).all()]


def assessment_questions(assessment_id: str, include_hidden: bool = False) -> list[dict]:
    with db().session_scope() as session:
        questions = session.query(Question).filter_by(
            assessment_id=assessment_id).order_by(Question.index).all()
        rubrics = {r.question_id: r.criteria for r in session.query(Rubric).all()}
        out = []
        for question in questions:
            item = {"question_id": question.id, "index": question.index,
                    "type": question.type, "text": question.text,
                    "options": question.options, "points": question.points,
                    "topic": question.topic, "difficulty": question.difficulty}
            if include_hidden:
                item.update({"correct_answer": question.correct_answer,
                             "model_answer": question.model_answer,
                             "explanation": question.explanation,
                             "notes": question.notes,
                             "rubric": rubrics.get(question.id, [])})
            out.append(item)
        return out


def get_question(question_id: str):
    with db().session_scope() as session:
        question = session.get(Question, question_id)
        if question is None:
            raise NotFoundError("Question not found.")
        rubric = session.query(Rubric).filter_by(question_id=question.id).first()
        return question, (rubric.criteria if rubric else [])
