"""Instructor-side Streamlit pages (dashboard, materials, builder, submissions,
analytics, heatmap, student progress)."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from app.services import (analytics_service, assessment_service, audit_service,
                          course_service, submission_service)
from .common import (badge, card, confidence_badge, evidence_block, metric_row,
                     page_style, rubric_block, status_badge)

QTYPES = {"mcq": "Multiple choice", "multi_select": "Multiple select",
          "true_false": "True / False", "short_answer": "Short answer",
          "long_answer": "Long answer", "essay": "Essay", "problem": "Problem-solving"}
OPEN = {"short_answer", "long_answer", "essay", "problem"}


# ------------------------------------------------------------------ dashboard
def dashboard(user):
    page_style()
    st.title("👩‍🏫 Instructor Dashboard")
    courses = course_service.list_courses(user)
    if not courses:
        st.info("Create your first course to get started.")
    for course in courses:
        col1, col2 = st.columns([5, 1])
        with col1:
            card(f"<h4>📘 {course['title']}</h4>"
                 f"<div class='edu-muted'>{course['subject'] or 'General'} "
                 f"{('· ' + course['code']) if course['code'] else ''} · "
                 f"{course['materials']} material(s) · {course['assessments']} assessment(s)</div>")
        with col2:
            if st.button("Open", key=f"open_{course['course_id']}"):
                st.session_state.current_course = course["course_id"]
                st.session_state.page = "course"
                st.rerun()
    st.divider()
    _create_course_form(user)


def _create_course_form(user):
    with st.expander("➕ Create new course"):
        with st.form("new_course"):
            title = st.text_input("Course title *")
            subject = st.text_input("Subject")
            code = st.text_input("Course code")
            description = st.text_area("Description")
            if st.form_submit_button("Create course", use_container_width=True):
                try:
                    course_service.create_course(user, title, subject, code, description)
                    st.success("Course created."); st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(exc.message if hasattr(exc, "message") else str(exc))


# --------------------------------------------------------------- course detail
def course_detail(user):
    page_style()
    courses = {c["course_id"]: c for c in course_service.list_courses(user)}
    course_id = st.session_state.get("current_course") or (
        st.selectbox("Course", list(courses), format_func=lambda i: courses[i]["title"])
        if courses else None)
    if not course_id:
        st.warning("No course selected."); return
    course = courses[course_id]
    st.title(f"📘 {course['title']}")
    st.caption(f"{course['subject'] or 'General'}{(' · ' + course['code']) if course['code'] else ''}")

    tab_materials, tab_assess = st.tabs(["📁 Course materials", "📝 Assessments"])

    with tab_materials:
        materials = course_service.list_materials(course_id, user)
        for material in materials:
            color = {"processed": "b-green", "uploaded": "b-amber",
                     "failed": "b-red"}.get(material["status"], "b-gray")
            card(f"<h4>📄 {material['filename']}</h4>"
                 f"{badge(material['status'], color)} "
                 f"{badge(f'{material[chr(99)+chr(104)+chr(117)+chr(110)+chr(107)+chr(115)]} chunks', 'b-blue') if material['chunks'] else ''}"
                 f"<div class='edu-muted'>{material['size_bytes']//1024} KB"
                 f"{' · ' + material['error'][:80] if material['error'] else ''}</div>")
        upload = st.file_uploader(
            "Upload course material (PDF, DOCX, PPTX, TXT)", type=["pdf", "docx", "pptx", "txt", "md", "csv"])
        if upload is not None:
            if st.button("Upload & process", use_container_width=True):
                try:
                    with st.spinner("Extracting, chunking and embedding into the vector store…"):
                        result = course_service.upload_material(
                            user, course_id, upload.name, upload.getvalue())
                        result.update(course_service.process_material(result["material_id"]))
                    st.success(f"Processed: {result['chunks']} chunks "
                               f"({result.get('indexed', 0)} indexed).")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(exc.message if hasattr(exc, "message") else str(exc))

    with tab_assess:
        assessments = assessment_service.list_assessments(course_id, user)
        for assessment in assessments:
            color = {"published": "b-green", "draft": "b-amber",
                     "closed": "b-gray"}.get(assessment["status"], "b-gray")
            card(f"<h4>{assessment['title']}</h4>"
                 f"{badge(assessment['kind'], 'b-purple')} {badge(assessment['status'], color)} "
                 f"{badge(f'{assessment[chr(116)+chr(111)+chr(116)+chr(97)+chr(108)+chr(95)+chr(112)+chr(111)+chr(105)+chr(110)+chr(116)+chr(115)]:g} pts', 'b-blue')}"
                 f"<div class='edu-muted'>{assessment['instructions'][:100]}</div>")
            if st.button("Open", key=f"as_{assessment['assessment_id']}"):
                st.session_state.current_assessment = assessment["assessment_id"]
                st.session_state.page = "assessment_detail"
                st.rerun()
        if st.button("➕ Build new assessment", use_container_width=True):
            st.session_state.draft_questions = []
            st.session_state.builder_meta = None
            st.session_state.last_created = None
            st.session_state.page = "builder"
            st.rerun()


# ------------------------------------------------------------ assessment builder
_QUESTION_FORM_KEYS = ("bq_text", "bq_topic", "bq_points", "bq_model", "bq_expl",
                       "bq_correct", "bq_correct_ms", "bq_tf",
                       "opt0", "opt1", "opt2", "opt3",
                       "mopt0", "mopt1", "mopt2", "mopt3", "rubric_count") \
                      + tuple(f"rc{i}" for i in range(6)) \
                      + tuple(f"re{i}" for i in range(6)) \
                      + tuple(f"rm{i}" for i in range(6))


def builder(user):
    page_style()
    st.title("🛠️ Assessment Builder")
    courses = course_service.list_courses(user)
    if not courses:
        st.warning("Create a course first."); return
    course = st.selectbox("Course *", courses, format_func=lambda c: c["title"])
    st.session_state.setdefault("draft_questions", [])
    st.session_state.setdefault("builder_meta", None)
    meta = st.session_state.builder_meta

    # ---- Step 1: assessment details (captured on save; survives reruns) ----
    with st.form("assessment_meta"):
        c1, c2 = st.columns(2)
        kind = c1.selectbox("Type *", ["quiz", "exam", "assignment"],
                            index=["quiz", "exam", "assignment"].index(meta["kind"]) if meta else 0)
        title = c2.text_input("Title *", value=meta["title"] if meta else "")
        topic = st.text_input("Overall topic", value=meta["topic"] if meta else "")
        description = st.text_area("Description", value=meta["description"] if meta else "")
        instructions = st.text_area("Instructions for students",
                                    value=meta["instructions"] if meta else "")
        c3, c4, c5, c6 = st.columns(4)
        duration = c3.number_input("Duration (min)", 5, 300,
                                   meta["duration_minutes"] if meta else 30)
        passing = c4.number_input("Passing score %", 0, 100,
                                  int(meta["passing_score_pct"]) if meta else 50)
        attempts = c5.number_input("Attempt limit", 1, 5,
                                   meta["attempt_limit"] if meta else 1)
        release_mode = c6.selectbox(
            "Release results", ["immediate", "after_end", "manual"],
            index=["immediate", "after_end", "manual"].index(meta["release_mode"]) if meta else 0)
        meta_ok = st.form_submit_button("Save details & continue to questions")
    if meta_ok:
        if not title.strip():
            st.error("Title is required — fill it in and save again.")
            meta = None
        else:
            st.session_state.builder_meta = {
                "kind": kind, "title": title.strip()[:200], "topic": topic.strip()[:200],
                "description": description, "instructions": instructions,
                "duration_minutes": int(duration),
                "passing_score_pct": float(passing),
                "attempt_limit": int(attempts), "release_mode": release_mode}
            st.rerun()
            return
    if not meta:
        st.info("Fill in the details above and press **Save details & continue "
                "to questions** — the question editor will appear below.")
        return

    st.success(f"Building **{meta['title']}** ({meta['kind']}) — "
               f"{len(st.session_state.draft_questions)} question(s) drafted so far")

    # ---- Step 2: questions ----
    for i, q in enumerate(st.session_state.draft_questions):
        with st.expander(f"Q{i + 1} · {QTYPES.get(q['type'], q['type'])} · "
                         f"{q['points']:g} pts · {q['topic'] or '—'}"):
            st.markdown(f"**{q['text']}**")
            if q.get("options"):
                st.markdown(" · ".join(q["options"]))
            if q.get("model_answer"):
                st.caption(f"Model answer: {q['model_answer'][:150]}")
            if q.get("rubric"):
                st.caption(f"Rubric: {len(q['rubric'])} criteria")
            if st.button("Remove", key=f"rm_{i}"):
                st.session_state.draft_questions.pop(i)
                st.rerun()

    # clear the add-question form BEFORE its widgets render (post-add rerun)
    if st.session_state.pop("bq_clear_flag", False):
        for key in _QUESTION_FORM_KEYS:
            st.session_state.pop(key, None)

    with st.expander("➕ Add question", expanded=not st.session_state.draft_questions):
        qtype = st.selectbox("Type", list(QTYPES), format_func=QTYPES.get, key="bq_type")
        text = st.text_area("Question text *", key="bq_text")
        colA, colB = st.columns(2)
        points = colA.number_input("Points", 0.5, 100.0, 1.0, 0.5, key="bq_points")
        topic_q = colB.text_input("Topic / skill", value=meta["topic"], key="bq_topic")

        raw_options: list[str] = []
        correct = {}
        correct_pos: int | list[int] = []
        if qtype in {"mcq", "multi_select"}:
            prefix = "opt" if qtype == "mcq" else "mopt"
            raw_options = [st.text_input(f"Option {j + 1}", key=f"{prefix}{j}") or ""
                           for j in range(4)]
            if qtype == "mcq":
                correct_pos = int(st.number_input("Correct option (1-4)", 1, 4, 1,
                                                  key="bq_correct")) - 1
            else:
                correct_pos = st.multiselect(
                    "Correct options (pick 2+)", [0, 1, 2, 3],
                    format_func=lambda i: f"Option {i + 1}", key="bq_correct_ms")
        elif qtype == "true_false":
            correct = {"value": st.selectbox("Correct answer", ["true", "false"],
                                             key="bq_tf")}

        model_answer, explanation, rubric = "", "", []
        if qtype in OPEN:
            model_answer = st.text_area("Model answer (optional but recommended)",
                                        key="bq_model")
            st.caption("Leave empty to rely on course materials/rubric only.")
            with st.expander("Rubric (optional)"):
                rubric = _rubric_editor(points)
        explanation = st.text_area("Explanation shown to students (optional)",
                                   key="bq_expl")

        if st.button("Add question", key="bq_add", type="primary"):
            error = None
            question = {"type": qtype, "text": text, "options": [],
                        "correct_answer": correct, "points": float(points),
                        "topic": topic_q, "model_answer": model_answer,
                        "explanation": explanation, "rubric": rubric}
            if qtype in {"mcq", "multi_select"}:
                options, remap = assessment_service.remap_choice_options(raw_options)
                question["options"] = options
                if qtype == "mcq":
                    if correct_pos not in remap:
                        error = (f"Option {correct_pos + 1} is blank — the correct "
                                 "option must be filled in.")
                    else:
                        question["correct_answer"] = {"index": remap[correct_pos]}
                else:
                    missing = [p + 1 for p in correct_pos if p not in remap]
                    if missing:
                        error = (f"Selected correct option(s) {missing} are blank — "
                                 "fill them in or deselect them.")
                    else:
                        question["correct_answer"] = {
                            "indices": sorted(remap[p] for p in correct_pos)}
            if error:
                st.error(error)
            else:
                try:
                    assessment_service._validate_question(
                        question, len(st.session_state.draft_questions))
                    st.session_state.draft_questions.append(question)
                    st.session_state.bq_clear_flag = True
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(exc.message if hasattr(exc, "message") else str(exc))

    # ---- Step 3: visibility + create ----
    if st.session_state.draft_questions:
        st.subheader("👁️ Result visibility for students")
        with st.form("visibility"):
            c = st.columns(4)
            flags = {
                "show_score": c[0].checkbox("Score", True),
                "show_percentage": c[1].checkbox("Percentage", True),
                "show_correct_incorrect": c[2].checkbox("Correct/incorrect", True),
                "show_feedback": c[3].checkbox("Feedback", True),
                "show_detailed_feedback": c[0].checkbox("Detailed feedback + rubric", True),
                "show_evidence": c[1].checkbox("Evidence", True),
                "show_correct_answers": c[2].checkbox("Correct answers", False),
                "show_model_answers": c[3].checkbox("Model answers", False),
                "contributes_to_progress": c[0].checkbox("Counts toward progress", True),
            }
            publish = st.form_submit_button("💾 Create assessment",
                                            use_container_width=True)
        if publish:
            try:
                result = assessment_service.create_assessment(
                    user, course["course_id"],
                    {**meta, "questions": st.session_state.draft_questions, **flags})
                st.session_state.draft_questions = []
                st.session_state.builder_meta = None
                st.session_state.last_created = result["assessment_id"]
                st.success(f"Created “{meta['title']}” ({result['questions']} questions, "
                           f"{result['total_points']:g} pts). Publish it below.")
            except Exception as exc:  # noqa: BLE001
                st.error(exc.message if hasattr(exc, "message") else str(exc))

    if st.session_state.get("last_created"):
        if st.button("🚀 Publish now", type="primary"):
            assessment_service.set_status(user, st.session_state.last_created,
                                          "published")
            st.session_state.current_assessment = st.session_state.pop("last_created")
            st.session_state.page = "assessment_detail"
            st.rerun()
        if st.button("✏️ Keep editing / add more later"):
            st.session_state.current_assessment = st.session_state.pop("last_created")
            st.session_state.page = "assessment_detail"
            st.rerun()


def _rubric_editor(points) -> list[dict]:
    count = st.number_input("Number of criteria", 1, 6, 2, key="rubric_count")
    criteria = []
    for i in range(int(count)):
        col1, col2 = st.columns([3, 1])
        name = col1.text_input(f"Criterion {i + 1} *", key=f"rc{i}")
        expected = col1.text_area("Expected performance", key=f"re{i}", height=70)
        maxp = col2.number_input("Max pts", 0.5, 100.0,
                                 round(points / max(1, int(count)), 2), 0.5,
                                 key=f"rm{i}")
        criteria.append({"criterion": name.strip(),
                         "description": expected.strip()[:500],
                         "max_points": float(maxp),
                         "expected": expected.strip()[:500]})
    # An untouched/blank rubric means NO rubric — the instructor simply
    # didn't open or fill the optional section. Never block adding the
    # question because of unnamed default criteria.
    if not any(c["criterion"] for c in criteria):
        return []
    criteria = [c for c in criteria if c["criterion"]]  # drop unnamed entries
    total = sum(c["max_points"] for c in criteria)
    if abs(total - points) > 0.01:
        st.warning(f"Criteria points ({total:g}) must sum to the question "
                   f"points ({points:g}) — adjust Max pts or add/remove criteria.")
    return criteria


# ------------------------------------------------------- assessment management
def assessment_detail(user):
    page_style()
    assessment_id = st.session_state.get("current_assessment")
    if not assessment_id:
        st.warning("Select an assessment first."); return
    try:
        assessment = assessment_service.get_assessment(assessment_id, user)
    except Exception:  # noqa: BLE001
        st.warning("Assessment not found."); return
    color = {"published": "b-green", "draft": "b-amber", "closed": "b-gray"}[assessment["status"]]
    st.title(f"📝 {assessment['title']}")
    st.markdown(badge(assessment["kind"], "b-purple") + badge(assessment["status"], color) +
                badge(f"{assessment['total_points']:g} pts", "b-blue") +
                badge(f"pass {assessment['passing_score_pct']:g}%", "b-gray"), unsafe_allow_html=True)
    st.caption(assessment["instructions"] or assessment["description"])

    c1, c2 = st.columns([1, 4])
    if assessment["status"] == "draft":
        if c1.button("🚀 Publish"):
            assessment_service.set_status(user, assessment_id, "published"); st.rerun()
    elif assessment["status"] == "published":
        if c1.button("🔒 Close"):
            assessment_service.set_status(user, assessment_id, "closed"); st.rerun()

    tab_q, tab_v, tab_s, tab_a = st.tabs(["Questions", "Visibility", "Submissions", "Analytics"])

    with tab_q:
        for q in assessment_service.assessment_questions(assessment_id, include_hidden=True):
            with st.expander(f"Q{q['index'] + 1} · {QTYPES.get(q['type'])} · "
                             f"{q['points']:g} pts · {q['topic'] or '—'}"):
                st.markdown(q["text"])
                if q["options"]:
                    st.markdown(" · ".join(q["options"]))
                if q["correct_answer"]:
                    st.caption(f"Correct: {q['correct_answer']}")
                if q["model_answer"]:
                    st.caption(f"Model answer: {q['model_answer'][:300]}")
                if q["rubric"]:
                    st.caption(f"Rubric: {len(q['rubric'])} criteria")

    with tab_v:
        v = assessment["visibility"]
        with st.form("vis_edit"):
            cols = st.columns(3)
            flags = {
                "show_score": cols[0].checkbox("Score", v["show_score"]),
                "show_percentage": cols[1].checkbox("Percentage", v["show_percentage"]),
                "show_correct_incorrect": cols[2].checkbox("Correct/incorrect", v["show_correct_incorrect"]),
                "show_feedback": cols[0].checkbox("Feedback", v["show_feedback"]),
                "show_detailed_feedback": cols[1].checkbox("Detailed + rubric", v["show_detailed_feedback"]),
                "show_evidence": cols[2].checkbox("Evidence", v["show_evidence"]),
                "show_correct_answers": cols[0].checkbox("Correct answers", v["show_correct_answers"]),
                "show_model_answers": cols[1].checkbox("Model answers", v["show_model_answers"]),
                "contributes_to_progress": cols[2].checkbox("Counts to progress", v["contributes_to_progress"]),
            }
            if st.form_submit_button("Save visibility"):
                assessment_service.update_visibility(user, assessment_id, flags)
                st.success("Saved."); st.rerun()

    with tab_s:
        _submissions_tab(user, assessment_id)

    with tab_a:
        _analytics_tab(user, assessment_id)


def _submissions_tab(user, assessment_id):
    submissions = submission_service.list_submissions(user, assessment_id)
    if not submissions:
        st.info("No submissions yet."); return
    df = pd.DataFrame([{"Student": s["student_name"], "Attempt": s["attempt"],
                        "Status": s["status"], "%": s["percentage"],
                        "AI confidence": s["confidence"],
                        "Review": "⚠️ yes" if s["needs_review"] else "—"} for s in submissions])
    st.dataframe(df, use_container_width=True, hide_index=True)
    selected = st.selectbox("Inspect student submission",
                            submissions, format_func=lambda s: (
                                f"{s['student_name']} — {s['percentage']}% "
                                f"({s['status']})"))
    if st.button("Open detailed result"):
        st.session_state.view_submission = selected["submission_id"]
        st.session_state.page = "submission_detail"
        st.rerun()
    if selected and st.session_state.get("manual_release_mode"):
        if st.button("Release results to student"):
            submission_service.release(user, selected["submission_id"])
            st.success("Released.")


def submission_detail(user):
    page_style()
    submission_id = st.session_state.get("view_submission")
    if not submission_id:
        st.warning("No submission selected."); return
    data = submission_service.instructor_view(user, submission_id)
    st.title(f"🧑‍🎓 {data['student']['name']} — submission result")
    metric_row([("Score", f"{data['total_score']:g}/{data['max_score']:g}"),
                ("Percentage", f"{data['percentage']}%"),
                ("AI confidence", data["confidence"]),
                ("Needs review", "yes ⚠️" if data["needs_review"] else "no")])
    st.divider()
    for item in data["per_question"]:
        header = status_badge(item["correct_status"]) + " " + \
            confidence_badge(item["confidence"]) + \
            (badge("instructor-overridden", "b-purple") if item["overridden"] else "")
        with st.expander(f"Q · {item['question_type']} · "
                         f"{item['score']:g}/{item['max_points']:g} pts"):
            st.markdown("**Question:** " + item["question_text"])
            st.markdown("**Student answer:**")
            answer = item["student_answer"]
            st.info(str(answer.get("text") or answer.get("value") or
                        (answer.get("options_picked") if isinstance(answer, dict) else answer) or
                        ([answer.get("options", [])[i] for i in answer.get("indices", [])]
                         if isinstance(answer, dict) and answer.get("indices") else answer)))
            st.markdown(header, unsafe_allow_html=True)
            st.markdown(f"**AI evaluation:** {item['feedback']}")
            rubric_block(item["rubric_results"])
            evidence_block(item["evidence"])
            if item["needs_review"]:
                st.warning(f"⚠️ {item['review_reason'] or 'Flagged for instructor review.'}")
            with st.form(f"override_{item['evaluation_id']}"):
                new_score = st.number_input("Official score", 0.0,
                                            float(item["max_points"]),
                                            float(item["score"]), 0.5)
                comment = st.text_input("Comment (audit trail)")
                if st.form_submit_button("Override score"):
                    try:
                        submission_service.override_score(
                            user, item["evaluation_id"], new_score, comment)
                        st.success(f"Official score set to {new_score:g}.")
                        st.rerun()
                    except Exception as exc:  # noqa: BLE001
                        st.error(exc.message if hasattr(exc, "message") else str(exc))


def _analytics_tab(user, assessment_id):
    data = analytics_service.assessment_analytics(user, assessment_id)
    if data["average_pct"] is None:
        st.info("No evaluated submissions yet."); return
    metric_row([("Submissions", data["submissions"]),
                ("Average", f"{data['average_pct']}%"),
                ("Highest", f"{data['highest_pct']}%"),
                ("Lowest", f"{data['lowest_pct']}%")])
    left, right = st.columns(2)
    with left:
        st.subheader("Score distribution")
        dist = pd.DataFrame({"bucket": list(data["score_distribution"].keys()),
                             "students": list(data["score_distribution"].values())})
        st.plotly_chart(px.bar(dist, x="bucket", y="students", color="bucket",
                               color_discrete_sequence=["#2563eb"] * 5), use_container_width=True)
    with right:
        st.subheader("Topic performance")
        topics = pd.DataFrame(data["topic_performance"])
        if not topics.empty:
            st.plotly_chart(px.bar(topics, x="topic", y="avg_pct",
                                   color="avg_pct", range_y=[0, 100],
                                   color_continuous_scale=["#dc2626", "#f59e0b", "#059669"]),
                            use_container_width=True)
    st.subheader("⚠️ Common weaknesses (class-wide)")
    for weakness in data["common_weaknesses"]:
        avg = f" · class avg {weakness['avg_pct']}%" if weakness.get("avg_pct") is not None else ""
        card(f"<h4>{weakness['topic']}</h4><div class='edu-muted'>"
             f"{weakness['students']} student(s) below 60%{avg}</div>")
    st.subheader("✅ Common strengths")
    for strength in data["common_strengths"]:
        card(f"<h4>{strength['topic']}</h4><div class='edu-muted'>"
             f"{strength['students']} student(s) at 80%+</div>")
    st.subheader("📋 Question performance")
    qdf = pd.DataFrame([{"Q": q["index"], "type": q["type"], "topic": q["topic"],
                         "avg %": q["avg_pct"], "low": "⚠️" if q["low_performance"] else ""}
                        for q in data["question_performance"]])
    st.dataframe(qdf, use_container_width=True, hide_index=True)
    if data["students_needing_review"]:
        st.subheader("👀 Students needing attention")
        for row in data["students_needing_review"]:
            card(f"<h4>{row['student_id'][:10]}…</h4>"
                 f"<div class='edu-muted'>{row['reason']} · {row['percentage']}%</div>")


def heatmap_page(user):
    page_style()
    st.title("🔥 Class Performance Heatmap")
    courses = course_service.list_courses(user)
    if not courses:
        st.warning("Create a course first."); return
    course = st.selectbox("Course", courses, format_func=lambda c: c["title"])
    data = analytics_service.heatmap(user, course["course_id"])
    if not data["students"] or not data["topics"]:
        st.info("No evaluated submissions in this course yet."); return
    matrix = []
    for student in data["students"]:
        matrix.append({"Student": student["name"],
                       **{topic: student["scores"].get(topic) for topic in data["topics"]}})
    df = pd.DataFrame(matrix).set_index("Student")
    st.plotly_chart(px.imshow(df, text_auto=True, aspect="auto",
                              color_continuous_scale=["#dc2626", "#f59e0b", "#059669"],
                              range_color=[0, 100], title="Students × Topics (%)"),
                    use_container_width=True)
    st.dataframe(df, use_container_width=True)


def student_progress_page(user):
    page_style()
    st.title("📈 Student Progress")
    from app.db import db
    from app.models import User
    with db().session_scope() as session:
        students = session.query(User).filter_by(role="student").all()
        options = {s.id: s.display_name or s.username for s in students}
    if not options:
        st.info("No students registered yet."); return
    selected = st.selectbox("Student", list(options), format_func=options.get)
    from app.services import progress_service, submission_service
    overview = progress_service.student_overview(selected)
    metric_row([("Overall mastery", f"{overview['overall_mastery_pct']}%"),
                ("Topics tracked", overview["topics_tracked"]),
                ("Strengths", len(overview["strengths"])),
                ("Weaknesses", len(overview["weaknesses"]))])
    st.subheader("Topic mastery")
    for topic in overview["topic_mastery"]:
        improvement = ""
        if topic["improvement"] is not None:
            sign = "+" if topic["improvement"] >= 0 else ""
            color = "#059669" if topic["improvement"] >= 0 else "#dc2626"
            improvement = f" <span style='color:{color}'>({sign}{topic['improvement']}%)</span>"
        state = {"Mastered": "b-green", "Progressing": "b-amber",
                 "Needs Practice": "b-red"}[topic["status"]]
        card(f"<h4>{topic['topic']} — {topic['current_pct']}%{improvement}</h4>"
             f"{badge(topic['status'], state)} "
             f"{badge(topic['last_kind'], 'b-gray')} "
             f"{badge(f'{topic[chr(97)+chr(116)+chr(116)+chr(101)+chr(109)+chr(112)+chr(116)+chr(115)]} attempt(s)', 'b-blue')}")
    history = submission_service.my_submissions(
        {"user_id": selected, "role": "student"})
    st.subheader("Assessment history")
    for item in history:
        card(f"<h4>{item['assessment_title']}</h4><div class='edu-muted'>"
             f"{item['status']} · {item['percentage']}% · {item['submitted_at'][:16].replace('T', ' ')}</div>")
