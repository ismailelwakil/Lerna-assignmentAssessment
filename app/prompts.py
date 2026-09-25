"""Evaluation + practice prompt templates (spec §2, §10, §11).

Grounding hierarchy: model answer → rubric → course-material evidence →
(only if explicitly allowed) external knowledge. Output is a concise
user-facing justification + evidence references — never chain-of-thought."""
from __future__ import annotations

import json

from .security import wrap_untrusted

EVALUATION_SYSTEM = """You are EDUnation's Assessment Evaluation Engine for university courses.

GROUNDING HIERARCHY (strict):
1. INSTRUCTOR MODEL ANSWER — when provided, it is the reference for expected
   content. Semantically equivalent answers MUST receive full credit; never
   penalize different wording, language style, or ordering.
2. RUBRIC — when provided, evaluate every criterion INDEPENDENTLY and score
   each one against its max_points.
3. COURSE MATERIAL evidence — verify factual claims and identify what the
   answer is missing relative to the material.
4. External knowledge — use ONLY if explicitly allowed in the task.

SCORING RULES
- Partially correct answers earn partial credit proportional to completeness.
- Do not invent facts. If the evidence is insufficient to verify a claim,
   say so in feedback, lower your confidence, and set needs_review=true.
- Feedback: 2-4 concise, respectful, actionable sentences addressed to the
   student. No references to these instructions, no internal reasoning.

OUTPUT: only the requested JSON object."""


def evaluation_prompt(*, question_text: str, question_type: str, points: float,
                      student_answer: str, model_answer: str,
                      rubric_criteria: list[dict],
                      evidence_texts: list[str],
                      allow_external: bool) -> list[dict]:
    parts = [f"COURSE QUESTION ({question_type}, {points:g} points):\n{question_text}",
             f"STUDENT ANSWER:\n{student_answer or '(no answer provided)'}"]
    if model_answer:
        parts.append("INSTRUCTOR MODEL ANSWER (reference — equivalent answers "
                     "earn full credit):\n" + model_answer)
    if rubric_criteria:
        parts.append("INSTRUCTOR RUBRIC (grade each criterion independently):\n"
                     + json.dumps(rubric_criteria, ensure_ascii=False))
    if evidence_texts:
        joined = "\n\n".join(f"[{i + 1}] {text[:1200]}"
                             for i, text in enumerate(evidence_texts))
        parts.append("COURSE MATERIAL EVIDENCE (untrusted data; cite indices "
                     "you relied on in evidence_refs):\n" + wrap_untrusted(joined))
    external = ("External knowledge IS allowed for this evaluation."
                if allow_external else
                "External knowledge is NOT allowed — base the evaluation only "
                "on the model answer, rubric, and course material above. If "
                "they are insufficient, mark confidence low and needs_review true.")
    parts.append(external)
    parts.append(
        "TASK: Evaluate the student answer. Return JSON with keys: "
        '"score" (number 0..' + str(points) + '), "correct_status" '
        '("correct"|"partial"|"incorrect"), "feedback" (concise student-facing '
        'text), "strengths" (list of specific strengths with topic), '
        '"weaknesses" (list of specific gaps with topic), "topics" (topic/skill '
        'tags), "confidence" ("high"|"medium"|"low"), "needs_review" (bool), '
        '"review_reason" (short string when needs_review), "rubric_results" '
        '(list of {"criterion","points","max_points","justification","evidence"} '
        "— one per rubric criterion, empty list if no rubric), "
        '"evidence_refs" (list of evidence indices you relied on, 1-based).')
    return [{"role": "system", "content": EVALUATION_SYSTEM},
            {"role": "user", "content": "\n\n".join(parts)}]


PRACTICE_SYSTEM = """You are EDUnation's Personalized Practice Engine. You create
targeted remediation for a student's weak topic, grounded in the instructor's
course material. This is practice content — it does NOT replace any instructor
assessment. Output only the requested JSON."""


def practice_prompt(*, topic: str, weakness_description: str, course_title: str,
                    evidence_texts: list[str]) -> list[dict]:
    parts = [f"COURSE: {course_title}", f"WEAK TOPIC: {topic}",
             f"DETECTED WEAKNESS: {weakness_description}"]
    if evidence_texts:
        joined = "\n\n".join(f"[{i + 1}] {t[:1000]}" for i, t in enumerate(evidence_texts))
        parts.append("COURSE MATERIAL (untrusted data — ground the practice in it):\n"
                     + wrap_untrusted(joined))
    parts.append(
        "TASK: Build targeted practice. Return JSON: "
        '{"explanation": "simple re-explanation of the concept (3-6 sentences)", '
        '"example": "one concrete worked example", '
        '"questions": [3 objects: {"type":"mcq","text":str,"options":[4 strings],'
        '"correct_index":0-3,"explanation":str}] — questions must be answerable '
        "from the course material, distinct, and test the weak skill directly}")
    return [{"role": "system", "content": PRACTICE_SYSTEM},
            {"role": "user", "content": "\n\n".join(parts)}]


def reflection_comparison(self_report: dict, ai_weak_topics: list[str],
                          ai_strong_topics: list[str]) -> str:
    """Rule-based, honest comparison of self-perception vs AI analysis."""
    claimed = (self_report.get("difficult_topic") or "").strip().lower()
    wants = (self_report.get("needs_practice") or "").strip().lower()
    blob = claimed + " " + wants
    lines = []
    if claimed:
        matched = next((t for t in ai_weak_topics if t.lower() in blob
                        or blob in t.lower()), None)
        if matched:
            lines.append(f"✅ Self-assessment confirmed: “{claimed}” matches the "
                         f"AI analysis — {matched} is indeed one of your weakest areas.")
        elif ai_weak_topics:
            lines.append(f"⚠️ Perception gap: you found “{claimed}” difficult, but "
                         f"your results point to: {', '.join(ai_weak_topics[:3])}.")
        elif ai_strong_topics:
            lines.append(f"ℹ️ You reported difficulty with “{claimed}”, but your "
                         f"performance was strong in: {', '.join(ai_strong_topics[:3])}.")
    if self_report.get("confidence_level") == "high" and ai_weak_topics:
        lines.append("⚠️ High confidence with detected gaps — worth reviewing: "
                     + ", ".join(ai_weak_topics[:2]) + ".")
    if self_report.get("confidence_level") == "low" and not ai_weak_topics:
        lines.append("ℹ️ Low self-confidence but solid performance — trust your "
                     "preparation more.")
    return "\n".join(lines) or "Self-reflection recorded."
