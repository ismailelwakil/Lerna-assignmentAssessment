"""Submission Service (spec §9, §13, §14) — attempts, validation, and the
visibility-aware student result view (instructor controls are enforced)."""
from __future__ import annotations

from datetime import datetime

from ..db import db
from ..exceptions import ForbiddenError, NotFoundError, ValidationError
from ..models import (Answer, Assessment, AuditEvent, Evaluation, EvidenceItem,
                      Question, SelfReflection, Submission)
from ..security import new_id
from . import assessment_service, evaluation_service


def available_assessments(student: dict, course_id: str) -> list[dict]:
    return assessment_service.list_assessments(course_id)


def attempts_used(student_id: str, assessment_id: str) -> int:
    with db().session_scope() as session:
        return session.query(Submission).filter_by(
            student_id=student_id, assessment_id=assessment_id).count()


def submit(student: dict, assessment_id: str, answers: list[dict]) -> dict:
    """Validate answers against questions, enforce attempt limit, persist, then
    trigger the AI Evaluation Engine (caller awaits evaluate_submission)."""
    with db().session_scope() as session:
        assessment = session.get(Assessment, assessment_id)
        if assessment is None:
            raise NotFoundError("Assessment not found.")
        if assessment.status != "published":
            raise ValidationError("This assessment is not currently published.")
        questions = session.query(Question).filter_by(
            assessment_id=assessment_id).order_by(Question.index).all()

    if not questions:
        raise ValidationError("Assessment has no questions.")
    used = attempts_used(student["user_id"], assessment_id)
    if used >= assessment.attempt_limit:
        raise ValidationError(
            f"Attempt limit reached ({assessment.attempt_limit}).")

    by_qid = {q.id: q for q in questions}
    validated = []
    for answer in answers:
        question = by_qid.get(answer.get("question_id"))
        if question is None:
            raise ValidationError("Answer references an unknown question.")
        response = answer.get("response") or {}
        if question.type in {"mcq"} and "index" not in response:
            raise ValidationError(f"Question {question.index + 1}: choose an option.")
        if question.type == "multi_select" and not response.get("indices"):
            raise ValidationError(f"Question {question.index + 1}: select at least one option.")
        if question.type == "true_false" and "value" not in response:
            raise ValidationError(f"Question {question.index + 1}: choose true or false.")
        if question.type in evaluation_service.__dict__.get("OPEN", set()) and not str(
                response.get("text", "")).strip():
            raise ValidationError(f"Question {question.index + 1}: answer text is required.")
        validated.append((question.id, response))

    with db().session_scope() as session:
        submission = Submission(id=new_id(), assessment_id=assessment_id,
                                student_id=student["user_id"],
                                attempt_number=used + 1, status="submitted",
                                max_score=assessment.total_points)
        session.add(submission)
        session.flush()
        for question_id, response in validated:
            session.add(Answer(id=new_id(), submission_id=submission.id,
                               question_id=question_id, response=response))
        session.add(AuditEvent(actor_id=student["user_id"], event_type="submitted",
                               entity_type="submission", entity_id=submission.id,
                               detail={"assessment_id": assessment_id,
                                       "attempt": used + 1}))
        sid = submission.id
    return {"submission_id": sid, "status": "submitted",
            "attempt": used + 1}


# ------------------------------------------------------------------ release
def _released(assessment: Assessment, submission: Submission) -> bool:
    if assessment.release_mode == "immediate":
        return submission.status in {"evaluated", "reviewed"}
    if assessment.release_mode == "manual":
        return bool(submission.released)
    if assessment.release_mode == "after_end":
        if submission.status not in {"evaluated", "reviewed"}:
            return False
        if assessment.availability_end is None:
            return True
        return datetime.utcnow() >= assessment.availability_end
    return False


def release(instructor: dict, submission_id: str) -> dict:
    """Manual release — INSTRUCTOR ONLY (students must never be able to
    release their own held-back results)."""
    from .auth_service import require_role
    require_role(instructor, "instructor")
    submission, _assessment, assessment_row = _owned_submission(submission_id, instructor)
    with db().session_scope() as session:
        row = session.get(Submission, submission_id)
        row.released = True
        session.add(AuditEvent(actor_id=instructor["user_id"], event_type="results_released",
                               entity_type="submission", entity_id=submission_id))
    _ = assessment_row
    return {"submission_id": submission_id, "released": True}


# ------------------------------------------------------------------- views
def _owned_submission(submission_id: str, viewer: dict):
    with db().session_scope() as session:
        submission = session.get(Submission, submission_id)
        if submission is None:
            raise NotFoundError("Submission not found.")
        assessment = session.get(Assessment, submission.assessment_id)
        if viewer["role"] == "student":
            if submission.student_id != viewer["user_id"]:
                raise NotFoundError("Submission not found.")  # no oracle
        elif assessment.instructor_id != viewer["user_id"]:
            raise NotFoundError("Submission not found.")
        return submission, assessment, assessment


def student_view(student: dict, submission_id: str) -> dict:
    """The ONLY shape students may receive — instructor visibility settings
    are enforced field-by-field here (defense in depth: UI + this filter)."""
    submission, assessment, _ = _owned_submission(submission_id, student)
    if not _released(assessment, submission):
        return {"submission_id": submission_id, "released": False,
                "status": submission.status,
                "message": "Results are not available yet. "
                           f"Release mode: {assessment.release_mode}."}
    visibility = assessment_service.get_assessment(assessment.id)["visibility"]
    with db().session_scope() as session:
        evaluations = session.query(Evaluation).filter_by(
            submission_id=submission_id).all()
        questions = {q.id: q for q in session.query(Question).filter_by(
            assessment_id=assessment.id).all()}
        evidence_rows = session.query(EvidenceItem).filter(
            EvidenceItem.evaluation_id.in_([e.id for e in evaluations])).all()
        evidence_by_eval: dict[str, list] = {}
        for ev in evidence_rows:
            evidence_by_eval.setdefault(ev.evaluation_id, []).append(ev)

    per_question = []
    for evaluation in evaluations:
        question = questions.get(evaluation.question_id)
        item = {"question_text": question.text if question else "",
                "question_type": question.type if question else "",
                "points": evaluation.max_points,
                "score": evaluation.score}
        if visibility["show_correct_incorrect"]:
            item["correct_status"] = evaluation.correct_status
        if visibility["show_feedback"]:
            item["feedback"] = evaluation.feedback
        if visibility["show_detailed_feedback"]:
            item["rubric_results"] = evaluation.rubric_results or []
        if visibility["show_evidence"]:
            item["evidence"] = [{
                "material": e.material_title, "page": e.page, "slide": e.slide,
                "section": e.section, "text": e.text, "relevance": e.relevance}
                for e in evidence_by_eval.get(evaluation.id, [])]
        if visibility["show_correct_answers"] and question:
            item["correct_answer"] = question.correct_answer
        if visibility["show_model_answers"] and question:
            item["model_answer"] = question.model_answer
        per_question.append(item)

    result = {"submission_id": submission_id, "released": True,
              "status": submission.status,
              "attempt": submission.attempt_number,
              "confidence": submission.ai_confidence,
              "needs_review": submission.needs_review,
              "per_question": per_question}
    if visibility["show_score"]:
        result["total_score"] = submission.total_score
        result["max_score"] = submission.max_score
    if visibility["show_percentage"]:
        result["percentage"] = submission.percentage
        result["passed"] = submission.percentage >= assessment.passing_score_pct
    if visibility["show_feedback"]:
        result["strengths"] = submission.strengths
        result["weaknesses"] = submission.weaknesses
        result["topics"] = submission.topics
    return result


def instructor_view(instructor: dict, submission_id: str) -> dict:
    submission, assessment, _ = _owned_submission(submission_id, instructor)
    with db().session_scope() as session:
        evaluations = session.query(Evaluation).filter_by(
            submission_id=submission_id).all()
        answers = session.query(Answer).filter_by(submission_id=submission_id).all()
        questions = {q.id: q for q in session.query(Question).filter_by(
            assessment_id=assessment.id).all()}
        evidence_rows = session.query(EvidenceItem).filter(
            EvidenceItem.evaluation_id.in_([e.id for e in evaluations])).all()
        evidence_by_eval: dict[str, list] = {}
        for ev in evidence_rows:
            evidence_by_eval.setdefault(ev.evaluation_id, []).append(ev)
        student = session.get(__import__("app.models", fromlist=["User"]).User,
                              submission.student_id)
        overrides = {o.evaluation_id: o for o in session.query(
            __import__("app.models", fromlist=["InstructorReview"]).InstructorReview).filter(
            __import__("app.models", fromlist=["InstructorReview"]).InstructorReview.evaluation_id.in_(
                [e.id for e in evaluations])).all()}

    per_question = []
    for evaluation in evaluations:
        question = questions.get(evaluation.question_id)
        answer = next((a for a in answers if a.question_id == evaluation.question_id), None)
        override = overrides.get(evaluation.id)
        per_question.append({
            "evaluation_id": evaluation.id,
            "question_text": question.text if question else "",
            "question_type": question.type if question else "",
            "student_answer": (answer.response if answer else {}),
            "score": evaluation.score, "max_points": evaluation.max_points,
            "final_score": override.final_score if override else evaluation.score,
            "overridden": bool(override),
            "override_comment": override.comment if override else "",
            "correct_status": evaluation.correct_status,
            "feedback": evaluation.feedback,
            "confidence": evaluation.confidence,
            "needs_review": evaluation.needs_review,
            "review_reason": evaluation.review_reason,
            "rubric_results": evaluation.rubric_results,
            "strengths": evaluation.strengths, "weaknesses": evaluation.weaknesses,
            "topics": evaluation.topics,
            "evidence": [{"material": e.material_title, "page": e.page,
                          "slide": e.slide, "section": e.section,
                          "text": e.text, "relevance": e.relevance}
                         for e in evidence_by_eval.get(evaluation.id, [])]})
    return {"submission_id": submission_id,
            "student": {"user_id": submission.student_id,
                        "name": student.display_name if student else "?",
                        "username": student.username if student else "?"},
            "assessment_id": assessment.id, "attempt": submission.attempt_number,
            "status": submission.status, "total_score": submission.total_score,
            "max_score": submission.max_score, "percentage": submission.percentage,
            "confidence": submission.ai_confidence,
            "needs_review": submission.needs_review,
            "strengths": submission.strengths, "weaknesses": submission.weaknesses,
            "per_question": per_question}


def list_submissions(instructor: dict, assessment_id: str) -> list[dict]:
    assessment_service._owned_assessment(assessment_id, instructor)
    with db().session_scope() as session:
        rows = session.query(Submission).filter_by(
            assessment_id=assessment_id).order_by(
            Submission.submitted_at).all()
        out = []
        for submission in rows:
            student = session.get(__import__("app.models", fromlist=["User"]).User,
                                  submission.student_id)
            out.append({"submission_id": submission.id,
                        "student_id": submission.student_id,
                        "student_name": student.display_name if student else "?",
                        "attempt": submission.attempt_number,
                        "status": submission.status,
                        "percentage": submission.percentage,
                        "total_score": submission.total_score,
                        "max_score": submission.max_score,
                        "confidence": submission.ai_confidence,
                        "needs_review": submission.needs_review,
                        "submitted_at": submission.submitted_at.isoformat()})
        return out


def my_submissions(student: dict) -> list[dict]:
    with db().session_scope() as session:
        rows = session.query(Submission).filter_by(
            student_id=student["user_id"]).order_by(
            Submission.submitted_at.desc()).all()
        out = []
        for submission in rows:
            assessment = session.get(Assessment, submission.assessment_id)
            out.append({"submission_id": submission.id,
                        "assessment_id": submission.assessment_id,
                        "assessment_title": assessment.title if assessment else "?",
                        "kind": assessment.kind if assessment else "?",
                        "status": submission.status,
                        "percentage": submission.percentage,
                        "submitted_at": submission.submitted_at.isoformat()})
        return out


# ------------------------------------------------------------------ override
def override_score(instructor: dict, evaluation_id: str, final_score: float,
                   comment: str = "") -> dict:
    """Instructor review/override — the AI evaluation row is preserved; the
    official score becomes the instructor-approved one (audit kept)."""
    with db().session_scope() as session:
        evaluation = session.get(Evaluation, evaluation_id)
        if evaluation is None:
            raise NotFoundError("Evaluation not found.")
        submission = session.get(Submission, evaluation.submission_id)
        assessment = session.get(Assessment, submission.assessment_id)
        if assessment.instructor_id != instructor["user_id"]:
            raise NotFoundError("Evaluation not found.")
        final_score = min(max(float(final_score), 0.0), evaluation.max_points)

        from ..models import InstructorReview
        session.add(InstructorReview(
            id=new_id(), evaluation_id=evaluation_id,
            instructor_id=instructor["user_id"],
            original_score=evaluation.score, final_score=final_score,
            comment=(comment or "")[:1000]))
        evaluation.score = final_score
        evaluation.evaluator = "instructor"
        if evaluation.correct_status in {"correct", "partial", "incorrect"}:
            ratio = final_score / evaluation.max_points if evaluation.max_points else 0
            evaluation.correct_status = ("correct" if ratio >= 0.999 else
                                         "partial" if ratio > 0 else "incorrect")
        evaluation.needs_review = False
        evaluation.review_reason = ""

        # recompute submission totals from ALL evaluations
        all_evals = session.query(Evaluation).filter_by(
            submission_id=submission.id).all()
        submission.total_score = round(sum(e.score for e in all_evals), 2)
        submission.percentage = round(100 * submission.total_score /
                                      submission.max_score, 1) if submission.max_score else 0
        submission.status = "reviewed"
        submission.needs_review = any(e.needs_review for e in all_evals)
        submission_id = submission.id
        session.add(AuditEvent(
            actor_id=instructor["user_id"], event_type="score_overridden",
            entity_type="evaluation", entity_id=evaluation_id,
            detail={"original": None, "final": final_score}))
    # refresh derived analytics for the student
    from . import progress_service, weakness_service
    with db().session_scope() as session:
        submission = session.get(Submission, submission_id)
        assessment = session.get(Assessment, submission.assessment_id)
    if assessment.contributes_to_progress:
        progress_service.record_assessment(  # re-record with official scores
            submission.student_id, assessment.course_id, assessment.id,
            [{"question_id": e["question_id"] for e in [{"question_id": q}]}] if False else
            [{"question_id": evaluation.question_id, "score": evaluation.score,
              "max": evaluation.max_points}
             for evaluation in all_evals])
        weakness_service.refresh(submission.student_id, assessment.course_id)
    return {"evaluation_id": evaluation_id, "final_score": final_score,
            "submission_id": submission_id}


# ------------------------------------------------------------- self-reflection
def save_reflection(student: dict, submission_id: str, data: dict) -> dict:
    _owned_submission(submission_id, {"user_id": student["user_id"], "role": "student"})
    from ..prompts import reflection_comparison
    from . import weakness_service
    weaknesses = [w["topic"] for w in weakness_service.list_weaknesses(student["user_id"])]
    strengths = [s["topic"] for s in weakness_service.list_strengths(student["user_id"])]
    comparison = reflection_comparison(data, weaknesses, strengths)
    with db().session_scope() as session:
        existing = session.query(SelfReflection).filter_by(
            submission_id=submission_id).first()
        if existing:
            existing.difficult_topic = (data.get("difficult_topic") or "")[:200]
            existing.struggled_question = (data.get("struggled_question") or "")[:1000]
            existing.confidence_level = data.get("confidence_level", "medium")
            existing.needs_practice = (data.get("needs_practice") or "")[:1000]
            existing.ai_comparison = comparison
        else:
            session.add(SelfReflection(
                id=new_id(), submission_id=submission_id,
                student_id=student["user_id"],
                difficult_topic=(data.get("difficult_topic") or "")[:200],
                struggled_question=(data.get("struggled_question") or "")[:1000],
                confidence_level=data.get("confidence_level", "medium"),
                needs_practice=(data.get("needs_practice") or "")[:1000],
                ai_comparison=comparison))
        session.add(AuditEvent(actor_id=student["user_id"], event_type="reflection_saved",
                               entity_type="submission", entity_id=submission_id))
    return {"saved": True, "ai_comparison": comparison}
