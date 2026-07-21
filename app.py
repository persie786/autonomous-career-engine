import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from utils.settings import load_settings, save_settings

from database.db_handler import (
    init_db,
    get_jobs,
    get_recent_activity,
    apply_ghosting_webhook,
    log_activity,
    update_job_status,
    save_generated_assets,
    approve_assets,
)
from modules.scraper import source_jobs
from modules.ai_evaluator import run_sourcing_pipeline
from modules.persona_builder import build_persona
from modules.ai_evaluator import run_sourcing_pipeline
from modules.cv_generator import generate_for_job

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

def render_sourcing_queue():
    st.header("Sourcing Queue")

    st.subheader("Run a Sourcing Pass")
    st.caption("Scrapes fresh listings, drops red flags and duplicates, then sends survivors to the AI evaluator.")
    if st.button("🚀 Trigger JobSpy", type="primary"):
        with st.spinner("Scraping and evaluating — can take a minute or two for a full batch..."):
            candidates = source_jobs()
            if not candidates:
                st.info("No new candidates this run — all duplicates, red-flagged, or the scrape came back empty.")
            else:
                counts = run_sourcing_pipeline(candidates)
                st.success(
                    f"Done — {counts['auto_approved']} auto-approved, "
                    f"{counts['manual_review']} need your review, "
                    f"{counts['rejected']} rejected by the evaluator, "
                    f"{counts['needs_consultation']} need a look (evaluator hiccup)."
                )
        st.rerun()

    st.divider()

    st.subheader("Awaiting Your Review")
    st.caption("Passed red-flag filtering and got a GO from the evaluator, but scored below your confidence threshold.")

    pending = get_jobs(status="Manual Review")
    if not pending:
        st.info("Nothing waiting on you right now.")
    else:
        for job in pending:
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"**{job['role']}** at **{job['company']}**")
                    st.caption(f"Match score: {job['match_score']:.0%} — {job.get('evaluator_reason', '')}")
                    st.markdown(f"[View listing]({job['job_url']})")
                with col2:
                    if st.button("✅ Approve", key=f"approve_{job['id']}", use_container_width=True):
                        update_job_status(job["id"], "Pending")
                        st.toast(f"Approved: {job['role']}")
                        st.rerun()
                    if st.button("❌ Reject", key=f"reject_{job['id']}", use_container_width=True):
                        update_job_status(job["id"], "Not Interested")
                        st.toast(f"Passed on: {job['role']}")
                        st.rerun()


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

        if st.button("🧠 Build Persona from Resume"):
            with st.spinner("Extracting resume with Gemini..."):
                try:
                    persona = build_persona()
                    st.toast("Persona built successfully.")
                    with st.expander("View extracted persona"):
                        st.json(persona)
                except Exception as e:
                    st.error(f"Persona extraction failed: {e}")
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
    
def render_asset_studio():
    st.header("Live Asset Studio")
    st.caption("Review and approve the tailored CV and cover letter before anything gets submitted.")

    jobs = get_jobs(status="Pending")
    needs_generation = [j for j in jobs if not j.get("generated_cv")]
    needs_review = [j for j in jobs if j.get("generated_cv") and not j.get("cv_approved_at")]
    approved = [j for j in jobs if j.get("cv_approved_at")]

    if not jobs:
        st.info("No jobs ready for the Studio yet — approve some in the Sourcing Queue first.")
        return

    if needs_generation:
        st.subheader("Ready to Generate")
        for job in needs_generation:
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                col1.markdown(f"**{job['role']}** at **{job['company']}**")
                if col2.button("✨ Generate", key=f"gen_{job['id']}", use_container_width=True):
                    with st.spinner(f"Generating tailored CV for {job['company']}..."):
                        try:
                            generate_for_job(job)
                            st.toast(f"Generated assets for {job['role']}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Generation failed: {e}")

    if needs_review:
        st.subheader("Awaiting Your Review")
        for job in needs_review:
            with st.container(border=True):
                st.markdown(f"**{job['role']}** at **{job['company']}** — persona: `{job['persona_used']}`")

                cv_text = st.text_area(
                    "Tailored CV content", value=job.get("generated_cv") or "",
                    key=f"cv_text_{job['id']}", height=200,
                )
                cl_text = st.text_area(
                    "Cover letter", value=job.get("generated_cover_letter") or "",
                    key=f"cl_text_{job['id']}", height=250,
                )
                st.caption(
                    "Editing here updates the saved text for your records — the downloadable "
                    ".docx below is Gemini's original generation. Polish the actual file in "
                    "Word before it goes anywhere."
                )

                docx_path = job.get("docx_path")
                if docx_path and os.path.exists(docx_path):
                    with open(docx_path, "rb") as f:
                        st.download_button(
                            "⬇️ Download tailored CV (.docx)", data=f.read(),
                            file_name=os.path.basename(docx_path), key=f"dl_{job['id']}",
                        )

                col1, col2 = st.columns(2)
                if col1.button("💾 Save Edits", key=f"save_{job['id']}", use_container_width=True):
                    save_generated_assets(job["id"], job["persona_used"], cv_text, cl_text, docx_path)
                    st.toast("Edits saved.")
                    st.rerun()

                if col2.button("✅ Approve", key=f"approve_studio_{job['id']}", type="primary", use_container_width=True):
                    save_generated_assets(job["id"], job["persona_used"], cv_text, cl_text, docx_path)
                    approve_assets(job["id"])
                    st.toast(f"Approved: {job['role']} at {job['company']}")
                    st.rerun()

    if approved:
        st.subheader("Approved — Awaiting Submission")
        st.caption("Signed off and ready. Actually submitting them is Week 4's browser agent.")
        for job in approved:
            with st.container(border=True):
                st.markdown(
                    f"**{job['role']}** at **{job['company']}** — "
                    f"approved {format_relative_time(job['cv_approved_at'])}"
                )


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
    render_sourcing_queue()
with tab_studio:
    render_asset_studio()

with tab_anomalies:
    st.info("Coming in Week 4: browser agent anomalies flagged for manual review.")