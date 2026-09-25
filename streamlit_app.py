"""EDUnation Assessment Module — Streamlit TESTING application.

NOT the final EDUnation frontend: this drives the complete workflow
(instructor + student) over the same service layer the REST API exposes.
Run:  streamlit run streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

from app.config import get_settings
from app.services import auth_service
from ui.common import page_style
from ui import instructor as instr
from ui import student as stud

st.set_page_config(page_title="EDUnation Assessments", page_icon="🎓",
                   layout="wide", initial_sidebar_state="expanded")

INSTRUCTOR_PAGES = {
    "dash": ("👩‍🏫 Dashboard", instr.dashboard),
    "course": ("📘 Course & Materials", instr.course_detail),
    "builder": ("🛠️ Assessment Builder", instr.builder),
    "assessment_detail": ("📝 Assessment Details", instr.assessment_detail),
    "submission_detail": ("🧑‍🎓 Student Result", instr.submission_detail),
    "heatmap": ("🔥 Class Heatmap", instr.heatmap_page),
    "student_progress": ("📈 Student Progress", instr.student_progress_page),
}
STUDENT_PAGES = {
    "assessments": ("📝 My Assessments", stud.assessments),
    "take": ("✍️ Taking Assessment", stud.take),
    "reflection": ("🪞 Self-Reflection", stud.reflection),
    "results": ("📊 My Results", stud.results),
    "result_detail": ("🎓 Result Detail", stud.result_detail),
    "progress": ("📈 My Progress", stud.progress),
    "practice": ("🏋️ Practice", stud.practice),
    "practice_take": ("🏋️ Practice Session", stud.practice_take),
}


def auth_gate():
    page_style()
    settings = get_settings()
    st.markdown("""
    <div style="text-align:center;padding:1.5rem 0 .5rem">
      <h1 style="margin:0;color:#2563eb">🎓 EDUnation Assessments</h1>
      <p style="color:#64748b">AI-powered, evidence-based assessment — testing interface</p>
    </div>""", unsafe_allow_html=True)
    if not settings.llm_available:
        st.warning("⚠️ No LLM provider configured — AI evaluation is disabled. "
                   "Copy .env.example to .env and add your existing EDUnation "
                   "provider keys (OPENROUTER_API_KEY or GROQ_API_KEY). "
                   "Everything else still works.")
    tab_login, tab_register = st.tabs(["Log in", "Register"])
    with tab_login:
        with st.form("login"):
            username = st.text_input("Username")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log in", use_container_width=True, type="primary"):
                try:
                    user = auth_service.login(username, password)
                    st.session_state.user = user
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(exc.message if hasattr(exc, "message") else str(exc))
    with tab_register:
        st.caption("Register manually as an instructor or a student — the system "
                   "never creates fake accounts.")
        with st.form("register"):
            c1, c2 = st.columns(2)
            role = c1.selectbox("I am a", ["student", "instructor"])
            display = c2.text_input("Display name")
            username = st.text_input("Username *")
            password = st.text_input("Password * (min 6 chars)", type="password")
            if st.form_submit_button("Create account", use_container_width=True):
                try:
                    user = auth_service.register(username, password, role, display)
                    st.session_state.user = user
                    st.success(f"Welcome, {user['display_name']}!")
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(exc.message if hasattr(exc, "message") else str(exc))


def main() -> None:
    user = st.session_state.get("user")
    if not user:
        auth_gate()
        return

    with st.sidebar:
        st.markdown(f"### 🎓 {user['display_name']}")
        st.caption(f"@{user['username']} · "
                   f"{'Instructor' if user['role'] == 'instructor' else 'Student'}")
        st.divider()
        pages = INSTRUCTOR_PAGES if user["role"] == "instructor" else STUDENT_PAGES
        for key, (label, _) in pages.items():
            if st.button(label, use_container_width=True,
                         type="primary" if st.session_state.get("page", "") == key else "secondary"):
                st.session_state.page = key
                st.rerun()
        st.divider()
        if st.button("🚪 Log out", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    page_key = st.session_state.get("page") or (
        "dash" if user["role"] == "instructor" else "assessments")
    page = pages.get(page_key)
    if page:
        try:
            page[1](user)
        except Exception as exc:  # noqa: BLE001 — never kill the whole app
            st.error(f"Something went wrong: "
                     f"{exc.message if hasattr(exc, 'message') else str(exc)[:200]}")


if __name__ == "__main__":
    main()
