"""Shared Streamlit UI components — EDUnation visual identity
(blue education / purple AI), cards, metrics, badges."""
from __future__ import annotations

import streamlit as st

BRAND = "#2563eb"
ACCENT = "#7c3aed"


def page_style():
    st.markdown("""
    <style>
      .edu-card {background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;
        padding:1.1rem 1.3rem;margin:0.35rem 0;box-shadow:0 1px 3px rgba(15,23,42,.06)}
      .edu-card h4 {margin:0 0 .3rem;color:#0f172a;font-size:1.02rem}
      .edu-muted {color:#64748b;font-size:.86rem}
      .edu-badge {display:inline-block;padding:.12rem .65rem;border-radius:99px;
        font-size:.74rem;font-weight:700;margin-inline-end:.3rem}
      .b-blue {background:#e0eaff;color:#274690}.b-purple {background:#efe7ff;color:#5b21b6}
      .b-green {background:#dcfce7;color:#166534}.b-red {background:#fee2e2;color:#991b1b}
      .b-amber {background:#fef3c7;color:#92400e}.b-gray {background:#f1f5f9;color:#475569}
      .edu-metric {border:1px solid #e2e8f0;border-radius:12px;padding:.7rem .9rem;
        text-align:center;background:#fff}
      .edu-metric .v {font-size:1.45rem;font-weight:800;color:#0f172a}
      .edu-metric .l {font-size:.75rem;color:#64748b}
      section[data-testid="stSidebar"] {background:#f8fafc}
      .stButton>button {border-radius:9px;font-weight:600}
    </style>""", unsafe_allow_html=True)


def badge(text: str, color: str = "b-blue") -> str:
    return f'<span class="edu-badge {color}">{text}</span>'


def card(html: str) -> None:
    st.markdown(f'<div class="edu-card">{html}</div>', unsafe_allow_html=True)


def metric_row(items: list[tuple[str, str]]) -> None:
    cols = st.columns(len(items))
    for col, (label, value) in zip(cols, items):
        col.markdown(f'<div class="edu-metric"><div class="v">{value}</div>'
                     f'<div class="l">{label}</div></div>', unsafe_allow_html=True)


def confidence_badge(confidence: str | None) -> str:
    return {"high": badge("AI confidence: high", "b-green"),
            "medium": badge("AI confidence: medium", "b-amber"),
            "low": badge("AI confidence: low", "b-red")}.get(confidence or "",
                                                             badge("confidence n/a", "b-gray"))


def status_badge(status: str) -> str:
    return {"correct": badge("✓ correct", "b-green"),
            "partial": badge("◐ partial", "b-amber"),
            "incorrect": badge("✗ incorrect", "b-red")}.get(status, badge(status, "b-gray"))


def evidence_block(evidence: list[dict]) -> None:
    if not evidence:
        st.info("No course-material evidence was retrieved for this answer.")
        return
    st.markdown("**📚 Evidence this evaluation was based on:**")
    for item in evidence:
        where = []
        if item.get("page"):
            where.append(f"page {item['page']}")
        if item.get("slide"):
            where.append(f"slide {item['slide']}")
        if item.get("section"):
            where.append(str(item["section"])[:60])
        loc = " · ".join(where)
        st.markdown(f"""
        <div class="edu-card" style="background:#f8fafc;padding:.6rem .9rem">
          <div class="edu-muted">📖 {item.get('material') or 'Course material'}
          {(' — ' + loc) if loc else ''} · relevance {item.get('relevance', 0):.2f}</div>
          <div style="font-size:.86rem;margin-top:.25rem">{item.get('text', '')[:500]}</div>
        </div>""", unsafe_allow_html=True)


def rubric_block(rubric_results: list[dict]) -> None:
    if not rubric_results:
        return
    st.markdown("**🧭 Rubric evaluation (per criterion):**")
    for item in rubric_results:
        points, maximum = item.get("points", 0), item.get("max_points", 0)
        st.markdown(f"""
        <div class="edu-card" style="padding:.6rem .9rem">
          <b>{item.get('criterion', 'criterion')}</b> —
          <b style="color:{'#059669' if points == maximum else '#d97706' if points > 0 else '#dc2626'}">
          {points}/{maximum} pts</b>
          <div class="edu-muted">{item.get('justification', '')}</div>
        </div>""", unsafe_allow_html=True)


def friendly_ai_error(exc) -> str:
    """User-safe AI failure message with recovery guidance."""
    msg = exc.message if hasattr(exc, "message") else str(exc)
    if getattr(exc, "code", "") == "LLM_UNAVAILABLE":
        return (msg + " Your answers are saved and safe — use the retry button "
                "below once the provider is available again. (Running locally? "
                "Copy .env.example to .env and add your EDUnation provider keys.)")
    return msg
