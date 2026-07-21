import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv
from cryptography.fernet import Fernet

from database.db_handler import (
    init_db,
    get_jobs,
    get_recent_activity,
    apply_ghosting_webhook,
    log_activity,
)
from utils.settings import load_settings, save_settings

load_dotenv()

st.set_page_config(page_title="Autonomous Career Engine", page_icon="🎯", layout="wide")

# Startup tasks — idempotent, safe to run on every rerun
if "app_initialized" not in st.session_state:
    init_db()
    apply_ghosting_webhook()
    st.session_state.app_initialized = True

st.title("Autonomous Career Engine")

MODULE_ICONS = {
    "db_handler": "🗄️",
    "settings": "⚙️",
    "scraper": "🔍",
    "ai_evaluator": "🤖",
    "browser_agent": "🌐",
    "email_monitor": "📧",
}


def format_relative_time(timestamp_str: str) -> str:
    """Converts a 'YYYY-MM-DD HH:MM:SS' string into a short, human-readable label."""
    try:
        then = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return timestamp_str

    seconds = (datetime.now() - then).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hr ago"
    return f"{int(seconds // 86400)} day(s) ago"


def render_dashboard():
    st.header("Dashboard & Analytics")

    jobs = get_jobs()
    df = pd.DataFrame(jobs)

    st.subheader("Application Funnel")
    if df.empty:
        st.info("No jobs yet — this fills in once Week 2's scraper starts feeding jobs through.")
    else:
        scraped = len(df)
        approved = len(df[~df["status"].isin(["Rejected", "Dead"])])
        applied = len(df[df["status"].isin(["Applied", "Interview", "Ghosted"])])
        interview = len(df[df["status"] == "Interview"])

        fig = go.Figure(go.Funnel(
            y=["Scraped", "Approved", "Applied", "Interview"],
            x=[scraped, approved, applied, interview],
            textinfo="value+percent initial",
        ))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Execution Audit Trail")
    col1, col2 = st.columns([5, 1])
    col1.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    if col2.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    activity = get_recent_activity(limit=30)
    if not activity:
        st.info("No activity logged yet.")
    else:
        for entry in activity:
            icon = MODULE_ICONS.get(entry["module"], "•")
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{icon} **{entry['module']}** — {entry['action_description']}")
            c2.caption(format_relative_time(entry["timestamp"]))


def render_settings():
    st.header("Settings & Guardrails")
    settings = load_settings()

    st.subheader("Base Resume")
    resume_path = os.path.join("data", "base_resume.pdf")
    uploaded_pdf = st.file_uploader("Upload your base resume (PDF)", type=["pdf"])
    if uploaded_pdf is not None:
        os.makedirs("data", exist_ok=True)
        with open(resume_path, "wb") as f:
            f.write(uploaded_pdf.getbuffer())
        log_activity("settings", "Base resume uploaded/replaced.")
        st.toast("Resume saved.")

    if os.path.exists(resume_path):
        uploaded_at = format_relative_time(
            datetime.fromtimestamp(os.path.getmtime(resume_path)).strftime("%Y-%m-%d %H:%M:%S")
        )
        st.success(f"✅ Resume on file — last updated {uploaded_at}.")
    else:
        st.caption("No base resume uploaded yet.")

    st.divider()

    st.subheader("Global Red Flags")
    st.caption("Jobs matching any of these (case-insensitive) are dropped before they ever reach the AI evaluator.")

    new_flag = st.text_input("Add a red flag", placeholder="e.g. unpaid, web3, commission-only")
    if st.button("Add Red Flag", type="primary"):
        cleaned = new_flag.strip()
        if not cleaned:
            st.warning("Type a red flag first.")
        elif cleaned.lower() in [f.lower() for f in settings["red_flags"]]:
            st.warning(f"'{cleaned}' is already on the list.")
        else:
            settings["red_flags"].append(cleaned)
            save_settings(settings)
            log_activity("settings", f"Red flag added: '{cleaned}'")
            st.toast(f"Added red flag: {cleaned}")
            st.rerun()

    for flag in settings["red_flags"]:
        with st.container(border=True):
            col1, col2 = st.columns([5, 1])
            col1.write(f"🚩 {flag}")
            if col2.button("🗑️ Remove", key=f"remove_{flag}", use_container_width=True):
                settings["red_flags"].remove(flag)
                save_settings(settings)
                log_activity("settings", f"Red flag removed: '{flag}'")
                st.toast(f"Removed red flag: {flag}")
                st.rerun()

    st.divider()

    st.subheader("Auto-Apply Confidence Threshold")
    st.caption("Jobs scoring at or above this route straight to the approved queue; below it, they wait for manual review.")
    threshold = st.slider("Confidence threshold", 0.0, 1.0, settings["confidence_threshold"], 0.05)
    if threshold != settings["confidence_threshold"]:
        settings["confidence_threshold"] = threshold
        save_settings(settings)
        log_activity("settings", f"Confidence threshold changed to {threshold}")
        st.toast(f"Confidence threshold set to {threshold:.0%}")

    st.divider()

st.subheader("Encrypted PII Vault")
key = os.getenv("ENCRYPTION_KEY")
if not key:
    st.error("🔓 Vault inactive — ENCRYPTION_KEY missing from .env.")
else:
    try:
        Fernet(key.encode())
        st.success("🔒 Vault active — ENCRYPTION_KEY is configured and valid.")
    except Exception:
        st.error("🔓 Vault inactive — ENCRYPTION_KEY is present but not a valid Fernet key. Regenerate it.")


tab_dashboard, tab_settings, tab_sourcing, tab_studio, tab_anomalies = st.tabs([
    "📊 Dashboard & Analytics",
    "⚙️ Settings & Guardrails",
    "🔍 Sourcing Queue",
    "📝 Live Asset Studio",
    "⚠️ Action Required",
])

with tab_dashboard:
    render_dashboard()

with tab_settings:
    render_settings()

with tab_sourcing:
    st.info("Coming in Week 2: JobSpy sourcing and the manual approval queue.")

with tab_studio:
    st.info("Coming in Week 3: dynamic CV/cover letter generation and approval.")

with tab_anomalies:
    st.info("Coming in Week 4: browser agent anomalies flagged for manual review.")