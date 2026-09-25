"""Assessment Module data model (spec §26).

Module-owned tables; learner identity is self-contained for the prototype and
maps 1:1 to EDUnation user ids on integration (see INTEGRATION.md)."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index,
                        Integer, String, Text)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def sid() -> str:
    return uuid.uuid4().hex


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16))  # instructor | student
    display_name: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Course(Base):
    __tablename__ = "courses"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    instructor_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(120), default="")
    code: Mapped[str] = mapped_column(String(40), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CourseMaterial(Base):
    __tablename__ = "course_materials"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    ext: Mapped[str] = mapped_column(String(16), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(500), default="")
    status: Mapped[str] = mapped_column(String(16), default="uploaded")  # uploaded|processed|failed
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MaterialChunk(Base):
    __tablename__ = "material_chunks"
    __table_args__ = (Index("ix_chunks_course", "course_id", "material_id", "chunk_index"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    material_id: Mapped[str] = mapped_column(ForeignKey("course_materials.id", ondelete="CASCADE"))
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(300), nullable=True)
    indexed: Mapped[bool] = mapped_column(Boolean, default=False)


class Assessment(Base):
    __tablename__ = "assessments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    instructor_id: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(16))          # quiz | exam | assignment
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions: Mapped[str] = mapped_column(Text, default="")
    topic: Mapped[str] = mapped_column(String(200), default="")
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    total_points: Mapped[float] = mapped_column(Float, default=0)
    passing_score_pct: Mapped[float] = mapped_column(Float, default=50)
    attempt_limit: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="draft")  # draft|published|closed
    availability_start: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    availability_end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # result-visibility controls (spec §14)
    release_mode: Mapped[str] = mapped_column(String(16), default="immediate")  # immediate|after_end|manual
    show_score: Mapped[bool] = mapped_column(Boolean, default=True)
    show_percentage: Mapped[bool] = mapped_column(Boolean, default=True)
    show_correct_incorrect: Mapped[bool] = mapped_column(Boolean, default=True)
    show_feedback: Mapped[bool] = mapped_column(Boolean, default=True)
    show_detailed_feedback: Mapped[bool] = mapped_column(Boolean, default=True)
    show_evidence: Mapped[bool] = mapped_column(Boolean, default=True)
    show_correct_answers: Mapped[bool] = mapped_column(Boolean, default=False)
    show_model_answers: Mapped[bool] = mapped_column(Boolean, default=False)
    contributes_to_progress: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (Index("ix_questions_assessment", "assessment_id", "index"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(20))  # mcq|multi_select|true_false|short_answer|long_answer|essay|problem
    text: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON, default=list)       # for choice types
    correct_answer: Mapped[dict] = mapped_column(JSON, default=dict)  # {"index":0} / {"indices":[0,2]} / {"value":"true"} / {"text": "..."}
    model_answer: Mapped[str] = mapped_column(Text, default="")
    explanation: Mapped[str] = mapped_column(Text, default="")
    points: Mapped[float] = mapped_column(Float, default=1.0)
    topic: Mapped[str] = mapped_column(String(200), default="")
    difficulty: Mapped[str] = mapped_column(String(16), default="medium")
    notes: Mapped[str] = mapped_column(Text, default="")            # instructor-only
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Rubric(Base):
    """1:1 with an open-ended question. criteria: [{criterion, description,
    max_points, expected}]"""
    __tablename__ = "rubrics"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), unique=True)
    criteria: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (Index("ix_submissions_assessment_student",
                            "assessment_id", "student_id", "attempt_number"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    assessment_id: Mapped[str] = mapped_column(ForeignKey("assessments.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="submitted")  # submitted|evaluated|reviewed
    submitted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_score: Mapped[float] = mapped_column(Float, default=0.0)
    max_score: Mapped[float] = mapped_column(Float, default=0.0)
    percentage: Mapped[float] = mapped_column(Float, default=0.0)
    ai_confidence: Mapped[str] = mapped_column(String(10), default="")   # high|medium|low
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    released: Mapped[bool] = mapped_column(Boolean, default=False)       # manual release flag
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    weaknesses: Mapped[list] = mapped_column(JSON, default=list)
    topics: Mapped[list] = mapped_column(JSON, default=list)


class Answer(Base):
    __tablename__ = "answers"
    __table_args__ = (Index("ix_answers_submission", "submission_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"))
    question_id: Mapped[str] = mapped_column(String(32), index=True)
    response: Mapped[dict] = mapped_column(JSON, default=dict)  # {"index":0}|{"indices":[..]}|{"text": "..."}|{"value":"true"}


class Evaluation(Base):
    __tablename__ = "evaluations"
    __table_args__ = (Index("ix_evaluations_answer", "answer_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    answer_id: Mapped[str] = mapped_column(ForeignKey("answers.id", ondelete="CASCADE"))
    submission_id: Mapped[str] = mapped_column(String(32), index=True)
    question_id: Mapped[str] = mapped_column(String(32), index=True)
    evaluator: Mapped[str] = mapped_column(String(12), default="ai")     # ai | instructor
    score: Mapped[float] = mapped_column(Float, default=0.0)
    max_points: Mapped[float] = mapped_column(Float, default=0.0)
    correct_status: Mapped[str] = mapped_column(String(12), default="n/a")  # correct|partial|incorrect|n/a
    feedback: Mapped[str] = mapped_column(Text, default="")
    strengths: Mapped[list] = mapped_column(JSON, default=list)
    weaknesses: Mapped[list] = mapped_column(JSON, default=list)
    topics: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[str] = mapped_column(String(10), default="medium")
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reason: Mapped[str] = mapped_column(Text, default="")
    rubric_results: Mapped[list] = mapped_column(JSON, default=list)
    model_answer_used: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class EvidenceItem(Base):
    __tablename__ = "evidence_items"
    __table_args__ = (Index("ix_evidence_evaluation", "evaluation_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    material_id: Mapped[str] = mapped_column(String(32), default="")
    material_title: Mapped[str] = mapped_column(String(255), default="")
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    slide: Mapped[int | None] = mapped_column(Integer, nullable=True)
    section: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    relevance: Mapped[float] = mapped_column(Float, default=0.0)
    used: Mapped[bool] = mapped_column(Boolean, default=True)  # referenced by the grader


class InstructorReview(Base):
    """Manual override — original AI evaluation is preserved for audit."""
    __tablename__ = "instructor_reviews"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    evaluation_id: Mapped[str] = mapped_column(ForeignKey("evaluations.id", ondelete="CASCADE"))
    instructor_id: Mapped[str] = mapped_column(String(32), index=True)
    original_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class TopicProgress(Base):
    """Rolling per (student, course, topic) history — progress is computed
    from these real events, never fabricated."""
    __tablename__ = "topic_progress"
    __table_args__ = (Index("ix_progress_student_topic", "student_id", "course_id", "topic"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(12), default="assessment")  # assessment|practice
    assessment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score_pct: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Weakness(Base):
    __tablename__ = "weaknesses"
    __table_args__ = (Index("ix_weakness_student_topic", "student_id", "course_id", "topic"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(10), default="medium")  # low|medium|high
    status: Mapped[str] = mapped_column(String(12), default="open")      # open|improving|resolved
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Strength(Base):
    __tablename__ = "strengths"
    __table_args__ = (Index("ix_strength_student_topic", "student_id", "course_id", "topic"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SelfReflection(Base):
    __tablename__ = "self_reflections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    submission_id: Mapped[str] = mapped_column(ForeignKey("submissions.id", ondelete="CASCADE"), unique=True)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    difficult_topic: Mapped[str] = mapped_column(String(200), default="")
    struggled_question: Mapped[str] = mapped_column(Text, default="")
    confidence_level: Mapped[str] = mapped_column(String(16), default="medium")  # low|medium|high
    needs_practice: Mapped[str] = mapped_column(Text, default="")
    ai_comparison: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class PracticeSession(Base):
    """Personalized remediation / reassessment (NOT instructor assessments)."""
    __tablename__ = "practice_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    student_id: Mapped[str] = mapped_column(String(32), index=True)
    course_id: Mapped[str] = mapped_column(String(32), index=True)
    topic: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16), default="remediation")  # remediation|reassessment
    explanation: Mapped[str] = mapped_column(Text, default="")
    example: Mapped[str] = mapped_column(Text, default="")
    questions: Mapped[list] = mapped_column(JSON, default=list)   # [{type,text,options,correct_index,explanation}]
    materials_used: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(14), default="generated")  # generated|completed
    score_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    before_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    after_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    improvement: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class AuditEvent(Base):
    """Append-only history (spec §25) — nothing overwrites past evaluations."""
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_entity", "entity_type", "entity_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=sid)
    actor_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    event_type: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(30), default="")
    entity_id: Mapped[str] = mapped_column(String(32), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
