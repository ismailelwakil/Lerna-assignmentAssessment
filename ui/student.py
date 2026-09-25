"""Student-side Streamlit pages (assessments, taking, results, progress,
practice/reassessment, self-reflection)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services import (assessment_service, progress_service,
                          remediation_service, submission_service, weakness_service)
from .common import (badge, card, confidence_badge, evidence_block, metric_row,
                     page_style, rubric_block, status_badge)


# ------------------------------------------------------------------ landing
def assessments(user):
    page_style()
    st.title("🧑‍🎓 My Assessments")
    courses = __import__("app.services.course_service", fromlist=["list_courses"]).list_courses(user)
    if not courses:
        st.info("No courses available yet."); return
    from app.db import db
    from app.models import Assessment, Submission
    for course in courses:
        published = assessment_service.list_assessments(course["course_id"])
        if not published:
            continue
        st.subheader(f"📘 {course['title']}")
        for assessment in published:
            with db().session_scope() as session:
                attempts = session.query(Submission).filter_by(
                    student_id=user["user_id"],
                    assessment_id=assessment["assessment_id"]).count()
            remaining = assessment["attempt_limit"] - attempts
            can_take = assessment["status"] == "published" and remaining > 0
            card(f"<h4>{assessment['title']}</h4>"
                 f"{badge(assessment['kind'], 'b-purple')} "
                 f"{badge(f'{assessment[chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(112)+chr(111)+chr(105)+chr(110)+chr(116)+chr(115)]:g} pts', 'b-blue')} "
                 f"{badge(f'{assessment[chr(100)+chr(117)+chr(114)+chr(97)+chr(116)+chr(105)+chr(111)+chr(110)+chr(95)+chr(109)+chr(105)+chr(110)+chr(117)+chr(116)+chr(101)+chr(115)]} min', 'b-gray')} "
                 f"{badge('attempts left: ' + str(max(0, remaining)), 'b-green' if can_take else 'b-red')}"
                 f"<div class='edu-muted'>{assessment['instructions'][:120]}</div>")
            if st.button("Take assessment", key=f"take_{assessment['assessment_id']}",
                         disabled=not can_take, type="primary"):
                st.session_state.taking = assessment["assessment_id"]
                st.session_state.answers = {}
                st.session_state.page = "take"
                st.rerun()


# ------------------------------------------------------------------- taking
def take(user):
    page_style()
    assessment_id = st.session_state.get("taking")
    if not assessment_id:
        st.warning("No assessment selected."); return
    assessment = assessment_service.get_assessment(assessment_id)
    questions = assessment_service.assessment_questions(assessment_id)
    st.title(f"📝 {assessment['title']}")
    st.caption(f"{assessment['kind']} · {assessment['total_points']:g} pts · "
               f"{assessment['duration_minutes']} min · pass {assessment['passing_score_pct']:g}%")
    if assessment["instructions"]:
        st.info(assessment["instructions"])

    answers = st.session_state.setdefault("answers", {})
    for question in questions:
        label = f"**Q{question['index'] + 1}** ({question['points']:g} pts" + \
                (f" · {question['topic']}" if question["topic"] else "") + ")"
        if question["type"] == "mcq":
            choice = st.radio(label, range(len(question["options"])),
                              format_func=lambda i, q=question: q["options"][i],
                              key=f"q_{question['question_id']}", index=None)
            if choice is not None:
                answers[question["question_id"]] = {"index": choice}
        elif question["type"] == "multi_select":
            picked = st.multiselect(label, range(len(question["options"])),
                                    format_func=lambda i, q=question: q["options"][i],
                                    key=f"q_{question['question_id']}")
            answers[question["question_id"]] = {"indices": picked}
        elif question["type"] == "true_false":
            value = st.radio(label, ["true", "false"],
                             key=f"q_{question['question_id']}", index=None)
            if value:
                answers[question["question_id"]] = {"value": value}
        else:
            height = 120 if question["type"] == "short_answer" else 220
            text = st.text_area(label, height=height, key=f"q_{question['question_id']}")
            answers[question["question_id"]] = {"text": text}

    def _answered(response) -> bool:
        if not response:
            return False
        if "text" in response:
            return bool(response.get("text", "").strip())
        if "indices" in response:        # multi-select: at least one picked
            return bool(response.get("indices"))
        return "index" in response or "value" in response

    answered = sum(1 for q in questions if _answered(answers.get(q["question_id"])))
    st.progress(answered / len(questions) if questions else 0,
                text=f"{answered}/{len(questions)} answered")
    if st.button("✅ Submit assessment", type="primary", disabled=answered < len(questions)):
        st.session_state.confirm = True
    if st.session_state.get("confirm"):
        st.warning("Submit finally? You cannot change answers afterwards.")
        col1, col2 = st.columns(2)
        if col1.button("Yes, submit"):
            result = None
            try:
                with st.spinner("🤖 AI Evaluation Engine is grading your answers "
                                "against the course materials…"):
                    result = submission_service.submit(
                        user, assessment_id,
                        [{"question_id": q["question_id"],
                          "response": answers[q["question_id"]]} for q in questions])
                    st.session_state.confirm = False
                    st.session_state.last_submission = result["submission_id"]
                    from app.services import evaluation_service
                    evaluation_service.evaluate_submission_sync(
                        result["submission_id"])
                st.session_state.page = "reflection"
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                if result:  # answers ARE saved — only the grading failed
                    st.session_state.eval_failed_submission = result["submission_id"]
                from .common import friendly_ai_error
                st.error(friendly_ai_error(exc))
        if col2.button("Keep editing"):
            st.session_state.confirm = False
            st.rerun()

    # Recovery: submission saved but AI evaluation failed (provider outage /
    # rate limit). Retry grading without re-submitting answers.
    failed = st.session_state.get("eval_failed_submission")
    if failed:
        st.warning("Your answers were submitted and saved, but the AI evaluation "
                   "could not run. Nothing was lost.")
        if st.button("🔄 Retry AI evaluation", type="primary"):
            try:
                with st.spinner("Retrying AI evaluation…"):
                    from app.services import evaluation_service
                    evaluation_service.evaluate_submission_sync(failed)
                st.session_state.eval_failed_submission = None
                st.session_state.last_submission = failed
                st.session_state.page = "reflection"
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                from .common import friendly_ai_error
                st.error(friendly_ai_error(exc))


# --------------------------------------------------------------- reflection
def reflection(user):
    page_style()
    st.title("🪞 Quick self-reflection")
    st.caption("Optional — compare how you feel with what the AI analysis found.")
    with st.form("reflect"):
        difficult = st.text_input("Which topic did you find most difficult?")
        struggled = st.text_input("Which question did you struggle with?")
        confidence = st.select_slider("How confident are you about your answers?",
                                      ["low", "medium", "high"])
        practice = st.text_input("What do you think you need to practice?")
        col1, col2 = st.columns(2)
        save = col1.form_submit_button("Save reflection")
        skip = col2.form_submit_button("Skip")
    if save:
        result = submission_service.save_reflection(
            user, st.session_state.last_submission,
            {"difficult_topic": difficult, "struggled_question": struggled,
             "confidence_level": confidence, "needs_practice": practice})
        st.success("Saved. Here is how your self-perception compares with the AI analysis:")
        st.info(result["ai_comparison"])
    if save or skip:
        st.session_state.page = "result"
        st.rerun()


# ------------------------------------------------------------------- results
def results(user):
    page_style()
    st.title("📊 My Results")
    submissions = submission_service.my_submissions(user)
    if not submissions:
        st.info("You haven't submitted any assessments yet."); return
    for item in submissions:
        card(f"<h4>{item['assessment_title']}</h4>"
             f"{badge(item['kind'], 'b-purple')} {badge(item['status'], 'b-gray')} "
             f"{badge(f'{item[chr(112)+chr(101)+chr(114)+chr(99)+chr(101)+chr(110)+chr(116)+chr(97)+chr(103)+chr(101)]}%', 'b-blue')}"
             f"<div class='edu-muted'>{item['submitted_at'][:16].replace('T',' ')}</div>")
        if st.button("View result", key=f"vr_{item['submission_id']}"):
            st.session_state.view_submission = item["submission_id"]
            st.session_state.page = "result_detail"
            st.rerun()


def result_detail(user):
    page_style()
    submission_id = st.session_state.get("view_submission")
    if not submission_id:
        st.warning("No result selected."); return
    data = submission_service.student_view(user, submission_id)
    if not data.get("released"):
        st.warning(data.get("message", "Results are not available yet."))
        if data.get("status") == "submitted":
            st.info("AI evaluation hasn't completed for this submission — open "
                    "the assessment page and press “Retry AI evaluation”.")
        return
    st.title("🎓 Your Result")
    metric_row([("Score", f"{data.get('total_score', '—')}/{data.get('max_score', '—')}"
                 if "total_score" in data else "hidden"),
                ("Percentage", f"{data.get('percentage')}%" if "percentage" in data else "hidden"),
                ("AI confidence", data.get("confidence", "—")),
                ("Passed", "✅" if data.get("passed") else "❌" if "passed" in data else "—")])
    if data.get("strengths"):
        st.success("**Strengths:** " + " · ".join(data["strengths"]))
    if data.get("weaknesses"):
        st.error("**Weaknesses:** " + " · ".join(data["weaknesses"]))
    st.divider()
    for item in data["per_question"]:
        with st.expander(f"Question · {item.get('score', 0):g}/{item['points']:g} pts"):
            st.markdown("**" + item["question_text"] + "**")
            st.markdown(item.get("correct_status", ""), unsafe_allow_html=False)
            if "correct_status" in item:
                st.markdown(status_badge(item["correct_status"]), unsafe_allow_html=True)
            if "feedback" in item:
                st.markdown(f"**Feedback:** {item['feedback']}")
            rubric_block(item.get("rubric_results", []))
            evidence_block(item.get("evidence", []))
            if "correct_answer" in item:
                st.markdown(f"**Correct answer:** {item['correct_answer']}")
            if "model_answer" in item:
                st.markdown(f"**Model answer:** {item['model_answer']}")


# ------------------------------------------------------------------ progress
def progress(user):
    page_style()
    st.title("📈 My Progress")
    overview = progress_service.student_overview(user["user_id"])
    if not overview["topic_mastery"]:
        st.info("Take an assessment to start tracking progress."); return
    metric_row([("Overall mastery", f"{overview['overall_mastery_pct']}%"),
                ("Topics", overview["topics_tracked"]),
                ("Strengths", len(overview["strengths"])),
                ("To improve", len(overview["weaknesses"]))])
    topics = overview["topic_mastery"]
    df = pd.DataFrame([{"Topic": t["topic"], "Mastery %": t["current_pct"],
                        "Status": t["status"],
                        "Improvement": t["improvement"] if t["improvement"] is not None else 0}
                       for t in topics])
    st.plotly_chart(px.bar(df, x="Topic", y="Mastery %", color="Status",
                           color_discrete_map={"Mastered": "#059669",
                                               "Progressing": "#f59e0b",
                                               "Needs Practice": "#dc2626"},
                           range_y=[0, 100]), use_container_width=True)
    for topic in topics:
        improvement = ""
        if topic["improvement"] is not None:
            sign = "+" if topic["improvement"] >= 0 else ""
            color = "#059669" if topic["improvement"] >= 0 else "#dc2626"
            improvement = f" <span style='color:{color}'>({sign}{topic['improvement']}% vs previous)</span>"
        state = {"Mastered": "b-green", "Progressing": "b-amber",
                 "Needs Practice": "b-red"}[topic["status"]]
        card(f"<h4>{topic['topic']} — {topic['current_pct']}%{improvement}</h4>"
             f"{badge(topic['status'], state)} {badge(topic['last_kind'], 'b-gray')}")
    st.subheader("🎯 Strengths & weaknesses (from your evaluations)")
    left, right = st.columns(2)
    with left:
        st.markdown("**✅ Strengths**")
        for topic in overview["strengths"]:
            st.markdown(f"- {topic['topic']} ({topic['current_pct']}%)")
    with right:
        st.markdown("**⚠️ Weaknesses**")
        for topic in overview["weaknesses"]:
            st.markdown(f"- {topic['topic']} ({topic['current_pct']}%)")


# ------------------------------------------------------------------ practice
def practice(user):
    page_style()
    st.title("🏋️ Personalized Practice & Reassessment")
    weaknesses = weakness_service.list_weaknesses(user["user_id"])
    history = remediation_service.list_practice(user)
    if history:
        st.subheader("Previous practice")
        for item in history:
            improvement = ""
            if item["improvement"] is not None:
                sign = "+" if item["improvement"] >= 0 else ""
                color = "#059669" if item["improvement"] >= 0 else "#dc2626"
                improvement = f" <span style='color:{color}'>({sign}{item['improvement']}%)</span>"
            card(f"<h4>{item['topic']}</h4><div class='edu-muted'>"
                 f"{badge(item['status'], 'b-green' if item['status'] == 'completed' else 'b-amber')}"
                 f" before {item['before_pct'] if item['before_pct'] is not None else '—'}% → "
                 f"after {item['after_pct'] if item['after_pct'] is not None else '—'}%{improvement}</div>")
            if item["status"] == "generated":
                if st.button("Continue practice", key=f"cont_{item['practice_id']}"):
                    st.session_state.practice_id = item["practice_id"]
                    st.session_state.page = "practice_take"
                    st.rerun()

    st.subheader("Generate targeted practice for a weak topic")
    if not weaknesses:
        st.info("No weaknesses detected yet — take an assessment first.")
        return
    from app.db import db
    from app.models import Course
    with db().session_scope() as session:
        course_map = {c.id: c.title for c in session.query(Course).all()}
    for weakness in weaknesses:
        course_id = weakness["course_id"]
        card(f"<h4>⚠️ {weakness['topic']}</h4>"
             f"{badge(weakness['severity'], 'b-red' if weakness['severity'] == 'high' else 'b-amber')} "
             f"{badge(course_map.get(course_id, ''), 'b-gray')}"
             f"<div class='edu-muted'>{weakness['description']}</div>")
        if st.button(f"🏋️ Practice: {weakness['topic']}", key=f"pr_{weakness['topic']}"):
            try:
                with st.spinner("Generating personalized practice from your course materials…"):
                    data = remediation_service.generate_practice(
                        user, weakness["topic"], course_id)
                st.session_state.practice_id = data["practice_id"]
                st.session_state.page = "practice_take"
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(exc.message if hasattr(exc, "message") else str(exc))


def practice_take(user):
    page_style()
    practice_id = st.session_state.get("practice_id")
    if not practice_id:
        st.warning("No practice selected."); return
    data = remediation_service.get_practice(user, practice_id)
    st.title(f"🏋️ Practice: {data['topic']}")
    if data["status"] == "completed":
        st.info("This practice session is completed.")
        if data["improvement"] is not None:
            metric_row([("Before", f"{data['before_pct']}%"),
                        ("After", f"{data['after_pct']}%"),
                        ("Improvement", f"{'+' if data['improvement'] >= 0 else ''}"
                                        f"{data['improvement']}%")])
        st.session_state.page = "practice"
        if st.button("Back to practice"):
            st.rerun()
        return
    st.markdown("**📖 Refresher explanation**")
    st.info(data["explanation"])
    if data["example"]:
        st.markdown("**Example:**")
        st.markdown(f"```\n{data['example']}\n```")
    choices = []
    for i, question in enumerate(data["questions"]):
        choice = st.radio(f"**Practice Q{i + 1}:** {question['text']}",
                          range(len(question["options"])),
                          format_func=lambda j, q=question: q["options"][j],
                          key=f"pq_{i}", index=None)
        choices.append(choice)
    if st.button("Submit practice", type="primary",
                 disabled=any(c is None for c in choices)):
        try:
            result = remediation_service.submit_practice(
                user, practice_id, [{"choice": c} for c in choices])
            st.success(f"Practice completed: {result['score_pct']}%")
            metric_row([("Before", f"{result['before_pct'] or '—'}%"),
                        ("After", f"{result['after_pct']}%"),
                        ("Improvement", f"{'+' if (result['improvement'] or 0) >= 0 else ''}"
                                        f"{result['improvement'] or 0}%")])
            if not result["topic_still_weak"]:
                st.balloons()
                st.success(f"🎉 {result['topic']} is no longer flagged as a weakness!")
            else:
                st.info(f"Still weak: {', '.join(result['remaining_weaknesses'])}")
            st.session_state.page = "practice"
            st.rerun()
        except Exception as exc:  # noqa: BLE001
            st.error(exc.message if hasattr(exc, "message") else str(exc))
