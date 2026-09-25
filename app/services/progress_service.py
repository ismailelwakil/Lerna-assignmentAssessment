"""Progress Service (spec §18) — topic mastery + trends computed from REAL
stored results (assessment + practice events). Never fabricated."""
from __future__ import annotations

from ..db import db
from ..models import TopicProgress


def record_assessment(student_id: str, course_id: str, assessment_id: str,
                      per_question: list[dict]) -> None:
    """One progress event per (topic of question) from evaluated answers."""
    by_topic: dict[str, tuple[float, float]] = {}
    questions = _load_questions([q["question_id"] for q in per_question])
    for item in per_question:
        topic = questions.get(item["question_id"], "").strip()
        if not topic:
            continue
        earned, maximum = by_topic.get(topic, (0.0, 0.0))
        by_topic[topic] = (earned + item["score"], maximum + item["max"])
    with db().session_scope() as session:
        for topic, (earned, maximum) in by_topic.items():
            session.add(TopicProgress(
                student_id=student_id, course_id=course_id, topic=topic[:200],
                kind="assessment", assessment_id=assessment_id,
                score_pct=round(100 * earned / maximum, 1) if maximum else 0.0))


def record_practice(student_id: str, course_id: str, topic: str,
                    score_pct: float) -> None:
    with db().session_scope() as session:
        session.add(TopicProgress(student_id=student_id, course_id=course_id,
                                  topic=topic[:200], kind="practice",
                                  score_pct=round(score_pct, 1)))


def _load_questions(question_ids: list[str]) -> dict[str, str]:
    from ..models import Question
    with db().session_scope() as session:
        rows = session.query(Question).filter(Question.id.in_(question_ids)).all()
        return {q.id: q.topic for q in rows}


def topic_mastery(student_id: str, course_id: str | None = None) -> list[dict]:
    """Current mastery per topic = latest event (assessment or practice),
    with trend vs the previous event (improvement)."""
    with db().session_scope() as session:
        query = session.query(TopicProgress).filter(
            TopicProgress.student_id == student_id)
        if course_id:
            query = query.filter(TopicProgress.course_id == course_id)
        events = query.order_by(TopicProgress.created_at).all()

    by_topic: dict[str, list] = {}
    for event in events:
        by_topic.setdefault(event.topic, []).append(event)
    out = []
    for topic, history in by_topic.items():
        latest, previous = history[-1], (history[-2] if len(history) > 1 else None)
        improvement = round(latest.score_pct - previous.score_pct, 1) if previous else None
        if latest.score_pct >= 80:
            status = "Mastered"
        elif latest.score_pct >= 60:
            status = "Progressing"
        else:
            status = "Needs Practice"
        out.append({
            "topic": topic, "course_id": latest.course_id,
            "current_pct": latest.score_pct,
            "previous_pct": previous.score_pct if previous else None,
            "improvement": improvement, "status": status,
            "attempts": len(history),
            "last_kind": latest.kind,
            "history": [{"score_pct": h.score_pct, "kind": h.kind,
                         "at": h.created_at.isoformat()} for h in history],
        })
    out.sort(key=lambda t: t["current_pct"])
    return out


def student_overview(student_id: str) -> dict:
    mastery = topic_mastery(student_id)
    strengths = [m for m in mastery if m["current_pct"] >= 80]
    weaknesses = [m for m in mastery if m["current_pct"] < 60]
    avg = round(sum(m["current_pct"] for m in mastery) / len(mastery), 1) if mastery else 0.0
    return {"overall_mastery_pct": avg, "topics_tracked": len(mastery),
            "strengths": strengths, "weaknesses": weaknesses,
            "topic_mastery": mastery}
