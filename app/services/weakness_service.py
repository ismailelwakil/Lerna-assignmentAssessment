"""Weakness + Strength Detection Engines (spec §19-§20).

Weaknesses/strengths are derived from stored evaluations & progress and kept
as first-class rows with specific, topic-linked descriptions — never vague."""
from __future__ import annotations

from ..config import get_settings
from ..db import db
from ..models import (Evaluation, Strength, Submission, TopicProgress, Weakness)


def refresh(student_id: str, course_id: str | None = None) -> dict:
    """Recompute weakness/strength rows from the student's latest evaluations."""
    settings = get_settings()
    with db().session_scope() as session:
        query = (session.query(Evaluation, Submission)
                 .join(Submission, Evaluation.submission_id == Submission.id)
                 .filter(Submission.student_id == student_id))
        if course_id:
            query = query.filter(Submission.assessment_id.in_(
                session.execute(__import__("sqlalchemy").select(
                    __import__("app.models", fromlist=["Assessment"]).Assessment.id
                ).where(__import__("app.models", fromlist=["Assessment"]).Assessment.course_id == course_id)
                ).scalars().all()))
        rows = query.all()

        latest_progress = {}
        progress_query = session.query(TopicProgress).filter(
            TopicProgress.student_id == student_id)
        if course_id:
            progress_query = progress_query.filter(TopicProgress.course_id == course_id)
        for event in progress_query.order_by(TopicProgress.created_at).all():
            latest_progress[event.topic] = event.score_pct

        # aggregate per topic from evaluations
        topics: dict[str, dict] = {}
        for evaluation, _submission in rows:
            for topic in (evaluation.topics or []):
                entry = topics.setdefault(topic, {"earned": 0.0, "max": 0.0,
                                                  "weak_notes": [], "strong_notes": []})
                entry["earned"] += evaluation.score
                entry["max"] += evaluation.max_points
                for weakness in (evaluation.weaknesses or []):
                    if weakness not in entry["weak_notes"]:
                        entry["weak_notes"].append(weakness)
                for strength in (evaluation.strengths or []):
                    if strength not in entry["strong_notes"]:
                        entry["strong_notes"].append(strength)

        existing_weak = {w.topic: w for w in session.query(Weakness).filter(
            Weakness.student_id == student_id)}
        existing_strong = {s.topic: s for s in session.query(Strength).filter(
            Strength.student_id == student_id)}

        for topic, entry in topics.items():
            pct = 100 * entry["earned"] / entry["max"] if entry["max"] else 0
            if pct < settings.weakness_threshold_pct:
                note = "; ".join(entry["weak_notes"][:2]) or f"{topic}: {pct:.0f}% average"
                row = existing_weak.get(topic)
                if row:
                    row.description = note[:1000]
                    row.severity = "high" if pct < 40 else "medium"
                    row.status = ("improving"
                                  if latest_progress.get(topic, 0) > pct
                                  else row.status)
                else:
                    session.add(Weakness(
                        student_id=student_id,
                        course_id=course_id or "", topic=topic[:200],
                        description=note[:1000],
                        severity="high" if pct < 40 else "medium", status="open"))
            elif pct >= settings.strength_threshold_pct:
                note = "; ".join(entry["strong_notes"][:2]) or f"{topic}: {pct:.0f}% average"
                if topic not in existing_strong:
                    session.add(Strength(student_id=student_id,
                                         course_id=course_id or "",
                                         topic=topic[:200], description=note[:1000]))
                # resolved weakness?
                if topic in existing_weak:
                    existing_weak[topic].status = "resolved"

    return list_weaknesses(student_id)


def list_weaknesses(student_id: str) -> list[dict]:
    with db().session_scope() as session:
        rows = session.query(Weakness).filter(
            Weakness.student_id == student_id,
            Weakness.status != "resolved").order_by(Weakness.last_seen.desc()).all()
        return [{"topic": w.topic, "description": w.description,
                 "severity": w.severity, "status": w.status,
                 "course_id": w.course_id} for w in rows]


def list_strengths(student_id: str) -> list[dict]:
    with db().session_scope() as session:
        rows = session.query(Strength).filter(
            Strength.student_id == student_id).order_by(Strength.last_seen.desc()).all()
        return [{"topic": s.topic, "description": s.description,
                 "course_id": s.course_id} for s in rows]
