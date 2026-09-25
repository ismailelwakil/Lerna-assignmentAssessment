"""Analytics Service (spec §15-§17, §24) — class stats, question/topic
performance, common weaknesses/strengths, students needing attention, and the
students × topics heatmap matrix."""
from __future__ import annotations

from collections import defaultdict

from ..db import db
from ..models import (Answer, Assessment, Evaluation, Question, Submission, User)


def assessment_analytics(instructor: dict, assessment_id: str) -> dict:
    from . import assessment_service
    assessment_service._owned_assessment(assessment_id, instructor)
    with db().session_scope() as session:
        submissions = session.query(Submission).filter_by(
            assessment_id=assessment_id).all()
        evaluated = [s for s in submissions if s.status in {"evaluated", "reviewed"}]
        students_total = session.query(User).filter_by(role="student").count()

        # per-student latest attempt
        latest_by_student: dict[str, Submission] = {}
        for submission in submissions:
            current = latest_by_student.get(submission.student_id)
            if not current or submission.attempt_number > current.attempt_number:
                latest_by_student[submission.student_id] = submission

        percentages = [s.percentage for s in latest_by_student.values()
                       if s.status in {"evaluated", "reviewed"}]
        scores = [s.total_score for s in latest_by_student.values()
                  if s.status in {"evaluated", "reviewed"}]

        # question + topic performance (all evaluations of latest attempts)
        latest_ids = [s.id for s in latest_by_student.values()]
        evaluations = session.query(Evaluation).filter(
            Evaluation.submission_id.in_(latest_ids)).all()
        answers = {a.question_id: a for a in session.query(Answer).filter(
            Answer.submission_id.in_(latest_ids)).all()}
        questions = {q.id: q for q in session.query(Question).filter_by(
            assessment_id=assessment_id).order_by(Question.index).all()}

        q_stats: dict[str, dict] = defaultdict(lambda: {"earned": 0.0, "max": 0.0, "n": 0})
        t_stats: dict[str, dict] = defaultdict(lambda: {"earned": 0.0, "max": 0.0, "n": 0})
        for evaluation in evaluations:
            question = questions.get(evaluation.question_id)
            if not question:
                continue
            entry = q_stats[evaluation.question_id]
            entry["earned"] += evaluation.score
            entry["max"] += evaluation.max_points
            entry["n"] += 1
            topic = question.topic or "General"
            t_entry = t_stats[topic]
            t_entry["earned"] += evaluation.score
            t_entry["max"] += evaluation.max_points
            t_entry["n"] += 1

        question_performance = []
        for question in questions.values():
            entry = q_stats.get(question.id, {"earned": 0, "max": 0, "n": 0})
            pct = round(100 * entry["earned"] / entry["max"], 1) if entry["max"] else None
            question_performance.append({
                "question_id": question.id, "index": question.index + 1,
                "type": question.type, "topic": question.topic,
                "text": question.text[:120],
                "avg_pct": pct, "attempts": entry["n"],
                "low_performance": bool(pct is not None and pct < 50)})

        topic_performance = []
        weak_students_by_topic: dict[str, set] = defaultdict(set)
        strong_students_by_topic: dict[str, set] = defaultdict(set)
        submission_owner = {s.id: s.student_id for s in latest_by_student.values()}
        for evaluation in evaluations:
            question = questions.get(evaluation.question_id)
            topic = (question.topic if question else "") or "General"
            owner = submission_owner.get(evaluation.submission_id)
            ratio = evaluation.score / evaluation.max_points if evaluation.max_points else 0
            if ratio < 0.6:
                weak_students_by_topic[topic].add(owner)
            elif ratio >= 0.8:
                strong_students_by_topic[topic].add(owner)
        for topic, entry in t_stats.items():
            pct = round(100 * entry["earned"] / entry["max"], 1) if entry["max"] else 0
            topic_performance.append({
                "topic": topic, "avg_pct": pct, "questions": entry["n"],
                "students_struggling": len(weak_students_by_topic.get(topic, set())),
                "students_strong": len(strong_students_by_topic.get(topic, set()))})

        buckets = {"0-20%": 0, "21-40%": 0, "41-60%": 0, "61-80%": 0, "81-100%": 0}
        for pct in percentages:
            if pct <= 20: buckets["0-20%"] += 1
            elif pct <= 40: buckets["21-40%"] += 1
            elif pct <= 60: buckets["41-60%"] += 1
            elif pct <= 80: buckets["61-80%"] += 1
            else: buckets["81-100%"] += 1

        needs_review = [{"submission_id": s.id, "student_id": s.student_id,
                         "percentage": s.percentage,
                         "reason": "low AI confidence" if s.ai_confidence == "low"
                         else "flagged for review"} for s in latest_by_student.values()
                        if s.needs_review]
        _ = evaluated, scores, students_total, answers

        assessment = session.get(Assessment, assessment_id)
        common_weaknesses = sorted(
            ({"topic": topic, "students": len(students),
              "avg_pct": next((t["avg_pct"] for t in topic_performance
                               if t["topic"] == topic), None)}
             for topic, students in weak_students_by_topic.items() if len(students) > 0),
            key=lambda w: -w["students"])
        common_strengths = sorted(
            ({"topic": topic, "students": len(students)} for topic, students
             in strong_students_by_topic.items()), key=lambda w: -w["students"])

    return {
        "assessment_id": assessment_id, "title": assessment.title,
        "submissions": len(latest_by_student), "evaluated": len(evaluated),
        "average_pct": round(sum(percentages) / len(percentages), 1) if percentages else None,
        "highest_pct": max(percentages) if percentages else None,
        "lowest_pct": min(percentages) if percentages else None,
        "score_distribution": buckets,
        "completion_note": f"{len(latest_by_student)} student(s) submitted",
        "question_performance": question_performance,
        "topic_performance": topic_performance,
        "common_weaknesses": common_weaknesses,
        "common_strengths": common_strengths,
        "students_needing_review": needs_review,
    }


def heatmap(instructor: dict, course_id: str) -> dict:
    """students × topics matrix (percentage per topic from latest results)."""
    from . import assessment_service
    from ..models import TopicProgress
    from .course_service import owned_course
    owned_course(course_id, instructor)
    with db().session_scope() as session:
        course_ids = [course_id]
        assessments = session.query(Assessment).filter(
            Assessment.course_id.in_(course_ids)).all()
        if instructor["role"] == "instructor":
            assessments = [a for a in assessments
                           if a.instructor_id == instructor["user_id"]]
        assessment_ids = {a.id for a in assessments}
        submissions = session.query(Submission).filter(
            Submission.assessment_id.in_(assessment_ids),
            Submission.status.in_(["evaluated", "reviewed"])).all()
        latest = {}
        for submission in submissions:
            current = latest.get(submission.student_id)
            key = (submission.assessment_id, submission.attempt_number)
            if not current or key > (current.assessment_id, current.attempt_number):
                latest[submission.student_id] = submission
        latest_ids = [s.id for s in latest.values()]
        evaluations = session.query(Evaluation).filter(
            Evaluation.submission_id.in_(latest_ids)).all()
        questions = {q.id: q for q in session.query(Question).filter(
            Question.assessment_id.in_(assessment_ids)).all()}
        students = {u.id: u.display_name for u in session.query(User).filter(
            User.id.in_(latest.keys())).all()}

        cell: dict[tuple, list[float]] = defaultdict(list)
        topics = set()
        for evaluation in evaluations:
            question = questions.get(evaluation.question_id)
            topic = (question.topic if question else "") or "General"
            topics.add(topic)
            ratio = 100 * evaluation.score / evaluation.max_points if evaluation.max_points else 0
            cell[(evaluation.submission_id, topic)].append(ratio)
        # include practice progress too
        practice_events = session.query(TopicProgress).filter(
            TopicProgress.course_id == course_id).all()

    submission_owner = {s.id: s.student_id for s in latest.values()}
    matrix: dict[str, dict[str, float]] = defaultdict(dict)
    for (submission_id, topic), ratios in cell.items():
        owner = submission_owner.get(submission_id)
        if owner:
            matrix[owner][topic] = round(sum(ratios) / len(ratios), 1)
    for event in practice_events:
        if event.kind == "practice":
            current = matrix.get(event.student_id, {}).get(event.topic)
            if current is None or event.score_pct > current:
                matrix[event.student_id][event.topic] = event.score_pct

    return {"students": [{"student_id": sid, "name": students.get(sid, sid),
                          "scores": matrix.get(sid, {})} for sid in
                         sorted(matrix.keys(), key=lambda x: students.get(x, x))],
            "topics": sorted(topics)}
