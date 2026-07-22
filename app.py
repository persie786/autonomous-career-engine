import os
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from utils.settings import load_settings, save_settings
from modules.browser_agent import (
    prepare_application,
    close_browser_session,
    mark_prep_failed,
    confirm_submitted,
)
from utils.field_memory import (
    load_field_memory,
    save_field_memory_answer,
    delete_field_memory_answer,
)
from modules.email_monitor import check_inbox
from utils.logger import setup_logger
from database.db_handler import (
    add_job,
    get_weekly_reports,
    init_db,
    get_jobs,
    get_recent_activity,
    get_activity_modules,
    get_scraped_count,
    apply_ghosting_webhook,
    log_activity,
    update_job_status,
    save_generated_assets,
    approve_assets,
    get_company_history,
    job_exists,
)
from modules.report_generator import generate_weekly_report
from modules.scraper import source_jobs
from modules.ai_evaluator import run_sourcing_pipeline
from modules.persona_builder import build_persona
from modules.ai_evaluator import run_sourcing_pipeline
from modules.cv_generator import generate_for_job
import json

logger = setup_logger("app")

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

if "inbox_checked_this_session" not in st.session_state:
    st.session_state.inbox_checked_this_session = True
    try:
        st.session_state.inbox_alert = check_inbox()
    except Exception as e:
        st.session_state.inbox_alert = None
        logger.warning(f"Automatic inbox check skipped: {e}")

alert = st.session_state.get("inbox_alert")
if alert and (alert["rejections"] or alert["interviews"] or alert["flagged"]):
    st.warning(
        f"📬 New inbox activity: {alert['rejections']} rejection(s), {alert['interviews']} interview(s), "
        f"{alert['flagged']} flagged for review. See the Dashboard for details."
    )


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

    if st.button("📧 Check Inbox for Updates"):
        with st.spinner("Scanning inbox..."):
            try:
                counts = check_inbox()
                st.success(
                    f"Scanned {counts['scanned']}, matched {counts['matched']} to applications — "
                    f"{counts['rejections']} rejection(s), {counts['interviews']} interview(s), "
                    f"{counts['flagged']} flagged for review."
                )
            except Exception as e:
                st.error(f"Inbox check failed: {e}")
        st.rerun()

    jobs = get_jobs()
    df = pd.DataFrame(jobs)

    st.subheader("Application Funnel")
    if df.empty:
        st.info(
            "No jobs yet — this fills in once Week 2's scraper starts feeding jobs through."
        )
    else:
        scraped = get_scraped_count()
        evaluated = len(
            df
        )  # every saved row already passed the AI evaluator's GO decision
        still_active = len(df[~df["status"].isin(["Not Interested", "Dead"])])
        applied = len(df[df["date_applied"].notna()])
        interview = len(df[df["status"] == "Interview"])

        scraped = max(
            scraped, evaluated
        )  # see note above — pre-Week-6 history wasn't recorded

        fig = go.Figure(
            go.Funnel(
                y=["Scraped", "Evaluated (GO)", "Still Active", "Applied", "Interview"],
                x=[scraped, evaluated, still_active, applied, interview],
                textinfo="value+percent initial+percent previous",
            )
        )
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Current Outcomes")
        st.caption(
            "Not part of the funnel's narrowing path — these are where jobs currently sit or ended up."
        )
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Awaiting Your Review", len(df[df["status"] == "Manual Review"]))
        c2.metric("Needs Consultation", len(df[df["status"] == "Needs Consultation"]))
        c3.metric("Ghosted", len(df[df["status"] == "Ghosted"]))
        c4.metric("Rejected (post-app)", len(df[df["status"] == "Rejected"]))

    st.subheader("Execution Audit Trail")
    col1, col2, col3 = st.columns([3, 2, 1])
    col1.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")

    modules = get_activity_modules()
    selected_module = col2.selectbox(
        "Filter by module", ["All"] + modules, label_visibility="collapsed"
    )
    if col3.button("🔄 Refresh", use_container_width=True):
        st.rerun()

    search_term = st.text_input(
        "Search activity",
        placeholder="🔍 Search descriptions...",
        label_visibility="collapsed",
    )

    activity = get_recent_activity(
        limit=50, module=None if selected_module == "All" else selected_module
    )
    if search_term:
        activity = [
            a
            for a in activity
            if search_term.lower() in a["action_description"].lower()
        ]

    if not activity:
        st.info("No matching activity.")
    else:
        for entry in activity:
            icon = MODULE_ICONS.get(entry["module"], "•")
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"{icon} **{entry['module']}** — {entry['action_description']}")
            c2.caption(format_relative_time(entry["timestamp"]))


def render_sourcing_queue():
    st.header("Sourcing Queue")

    st.subheader("Run a Sourcing Pass")
    st.caption(
        "Scrapes fresh listings, drops red flags and duplicates, then sends survivors to the AI evaluator."
    )
    if st.button("🚀 Trigger JobSpy", type="primary"):
        with st.spinner(
            "Scraping and evaluating — can take a minute or two for a full batch..."
        ):
            candidates = source_jobs()
            if not candidates:
                st.info(
                    "No new candidates this run — all duplicates, red-flagged, or the scrape came back empty."
                )
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
    st.subheader("Add a Job Manually")
    st.caption(
        "Found something yourself outside JobSpy? Add it directly — skips red-flag filtering and AI evaluation since you've already decided it's worth pursuing."
    )

    with st.form("manual_add_job_form", clear_on_submit=True):
        m_company = st.text_input("Company")
        m_role = st.text_input("Role / Job Title")
        m_url = st.text_input("Job posting URL")
        m_description = st.text_area("Job description", height=150)
        submitted = st.form_submit_button("➕ Add to Pipeline")

        if submitted:
            if not (m_company.strip() and m_role.strip() and m_url.strip()):
                st.warning("Company, role, and URL are all required.")
            elif job_exists(m_url.strip()):
                st.warning("This job URL is already in your pipeline.")
            else:
                add_job(
                    company=m_company.strip(),
                    role=m_role.strip(),
                    job_url=m_url.strip(),
                    job_description=m_description.strip(),
                )
                log_activity(
                    "manual_entry",
                    f"Manually added: {m_role.strip()} at {m_company.strip()}",
                )
                st.toast("Added to pipeline.")
                st.rerun()

    st.divider()

    st.subheader("Awaiting Your Review")
    st.caption(
        "Passed red-flag filtering and got a GO from the evaluator, but scored below your confidence threshold."
    )

    pending = get_jobs(status="Manual Review")
    if not pending:
        st.info("Nothing waiting on you right now.")
    else:
        for job in pending:
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                with col1:
                    st.markdown(f"**{job['role']}** at **{job['company']}**")
                    st.caption(
                        f"Match score: {job['match_score']:.0%} — {job.get('evaluator_reason', '')}"
                    )
                    st.markdown(f"[View listing]({job['job_url']})")
                with col2:
                    if st.button(
                        "✅ Approve",
                        key=f"approve_{job['id']}",
                        use_container_width=True,
                    ):
                        update_job_status(job["id"], "Pending")
                        st.toast(f"Approved: {job['role']}")
                        st.rerun()
                    if st.button(
                        "❌ Reject", key=f"reject_{job['id']}", use_container_width=True
                    ):
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
            datetime.fromtimestamp(os.path.getmtime(resume_path)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
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
    st.caption(
        "Jobs matching any of these (case-insensitive) are dropped before they ever reach the AI evaluator."
    )

    new_flag = st.text_input(
        "Add a red flag", placeholder="e.g. unpaid, web3, commission-only"
    )
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
    st.caption(
        "Jobs scoring at or above this route straight to the approved queue; below it, they wait for manual review."
    )
    threshold = st.slider(
        "Confidence threshold", 0.0, 1.0, settings["confidence_threshold"], 0.05
    )
    if threshold != settings["confidence_threshold"]:
        settings["confidence_threshold"] = threshold
        save_settings(settings)
        log_activity("settings", f"Confidence threshold changed to {threshold}")
        st.toast(f"Confidence threshold set to {threshold:.0%}")

    st.divider()
    st.subheader("Field Memory Cache")
    st.caption(
        "Custom application questions and your saved answer — reused automatically when the browser agent finds a matching label."
    )

    fm = load_field_memory()
    new_q = st.text_input("Question (match this label text)", key="new_fm_q")
    new_a = st.text_area("Your answer", key="new_fm_a")
    if st.button("Save Answer") and new_q.strip() and new_a.strip():
        save_field_memory_answer(new_q.strip(), new_a.strip())
        st.toast("Saved.")
        st.rerun()

    for question, answer in fm.items():
        with st.container(border=True):
            st.markdown(f"**{question}**")
            st.caption(answer[:150] + ("..." if len(answer) > 150 else ""))
            if st.button("🗑️ Remove", key=f"remove_fm_{question}"):
                delete_field_memory_answer(question)
                st.rerun()

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
        st.error(
            "🔓 Vault inactive — ENCRYPTION_KEY is present but not a valid Fernet key. Regenerate it."
        )


def render_asset_studio():
    st.header("Live Asset Studio")
    st.caption(
        "Review and approve the tailored CV and cover letter before anything gets submitted."
    )

    jobs = get_jobs(status="Pending")
    needs_generation = [j for j in jobs if not j.get("generated_cv")]
    needs_review = [
        j for j in jobs if j.get("generated_cv") and not j.get("cv_approved_at")
    ]
    approved = [j for j in jobs if j.get("cv_approved_at")]

    if not jobs:
        st.info(
            "No jobs ready for the Studio yet — approve some in the Sourcing Queue first."
        )
        return

    if needs_generation:
        st.subheader("Ready to Generate")
        for job in needs_generation:
            with st.container(border=True):
                col1, col2 = st.columns([4, 1])
                col1.markdown(f"**{job['role']}** at **{job['company']}**")

                history = get_company_history(job["company"], exclude_job_id=job["id"])
                if history:
                    prior_persona = next(
                        (h["persona_used"] for h in history if h["persona_used"]), None
                    )
                    with col1.expander(
                        f"⚠️ {len(history)} prior application(s) at this company"
                    ):
                        for h in history:
                            st.caption(
                                f"{h['role']} — {h['status']} — persona: {h.get('persona_used') or 'none yet'} — {h['date_added'][:10]}"
                            )
                    if prior_persona:
                        col1.warning(
                            f"Guardrail will force persona **'{prior_persona}'** — same one used for this company before."
                        )

                if col2.button(
                    "✨ Generate", key=f"gen_{job['id']}", use_container_width=True
                ):
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
                st.markdown(
                    f"**{job['role']}** at **{job['company']}** — persona: `{job['persona_used']}`"
                )

                cv_text = st.text_area(
                    "Tailored CV content",
                    value=job.get("generated_cv") or "",
                    key=f"cv_text_{job['id']}",
                    height=200,
                )
                cl_text = st.text_area(
                    "Cover letter",
                    value=job.get("generated_cover_letter") or "",
                    key=f"cl_text_{job['id']}",
                    height=250,
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
                            "⬇️ Download tailored CV (.docx)",
                            data=f.read(),
                            file_name=os.path.basename(docx_path),
                            key=f"dl_{job['id']}",
                        )

                col1, col2 = st.columns(2)
                if col1.button(
                    "💾 Save Edits", key=f"save_{job['id']}", use_container_width=True
                ):
                    save_generated_assets(
                        job["id"], job["persona_used"], cv_text, cl_text, docx_path
                    )
                    st.toast("Edits saved.")
                    st.rerun()

                if col2.button(
                    "✅ Approve",
                    key=f"approve_studio_{job['id']}",
                    type="primary",
                    use_container_width=True,
                ):
                    save_generated_assets(
                        job["id"], job["persona_used"], cv_text, cl_text, docx_path
                    )
                    approve_assets(job["id"])
                    st.toast(f"Approved: {job['role']} at {job['company']}")
                    st.rerun()

    if approved:
        st.subheader("Approved — Awaiting Submission")
        st.caption(
            "Signed off and ready. The agent fills what it can identify in a real browser window — you review and click Submit yourself."
        )

        if "browser_sessions" not in st.session_state:
            st.session_state.browser_sessions = {}

        for job in approved:
            with st.container(border=True):
                st.markdown(
                    f"**{job['role']}** at **{job['company']}** — approved {format_relative_time(job['cv_approved_at'])}"
                )

                session = st.session_state.browser_sessions.get(job["id"])

                if session is None:
                    if st.button(
                        "🌐 Open & Autofill Application", key=f"prep_{job['id']}"
                    ):
                        with st.spinner(
                            "Opening browser and filling what it can find..."
                        ):
                            try:
                                result = prepare_application(job)
                                st.session_state.browser_sessions[job["id"]] = result
                                st.toast(
                                    f"Filled: {', '.join(result['filled']) or 'nothing recognized'}"
                                )
                            except Exception as e:
                                mark_prep_failed(job["id"], job.get("retry_count", 0))
                                st.error(f"Prep failed: {e}")
                        st.rerun()
                else:
                    st.info(
                        f"Browser window open. Filled: {', '.join(session['filled']) or 'nothing'}. "
                        f"Needs your attention: {', '.join(session['skipped']) or 'nothing'}. "
                        "Review everything in the window, then click Submit there yourself."
                    )
                    if st.button(
                        "✅ I Submitted This — Close Browser",
                        key=f"confirm_{job['id']}",
                        type="primary",
                    ):
                        close_browser_session(session)
                        confirm_submitted(job["id"])
                        del st.session_state.browser_sessions[job["id"]]
                        st.toast(f"Marked as Applied: {job['role']}")
                        st.rerun()


def render_reports():
    st.header("Weekly Reports")
    st.caption(
        "Generate anytime — every report stays saved below so you can compare over time."
    )

    days = st.number_input("Report period (days)", min_value=1, max_value=90, value=7)
    if st.button("📊 Generate Report", type="primary"):
        with st.spinner("Crunching numbers..."):
            generate_weekly_report(days=days)
            st.toast("Report generated.")
            st.rerun()

    st.divider()
    st.subheader("Report History")

    reports = get_weekly_reports(limit=20)
    if not reports:
        st.info("No reports generated yet.")
        return

    for report in reports:
        stats = json.loads(report["stats_json"])
        with st.expander(
            f"📅 {report['period_start'][:10]} → {report['period_end'][:10]} (generated {format_relative_time(report['generated_at'])})"
        ):
            st.write(report["summary_text"])
            c1, c2 = st.columns(2)
            c1.metric("New jobs added", stats["new_jobs_added"])
            c2.metric("Applications submitted", stats["applications_submitted"])
            st.write("**Status breakdown (as of generation):**")
            st.json(stats["status_counts"])
            if stats["top_companies"]:
                st.write("**Top companies applied to this period:**")
                for company, count in stats["top_companies"]:
                    st.write(f"- {company}: {count}")

            report_md = (
                f"# Weekly Report: {report['period_start'][:10]} to {report['period_end'][:10]}\n\n"
                f"{report['summary_text']}\n\n- New jobs added: {stats['new_jobs_added']}\n"
                f"- Applications submitted: {stats['applications_submitted']}\n"
            )
            st.download_button(
                "⬇️ Download as Markdown",
                data=report_md,
                file_name=f"report_{report['period_start'][:10]}.md",
                key=f"dl_report_{report['id']}",
            )


def render_action_required():
    st.header("Action Required")
    st.caption(
        "Jobs the pipeline couldn't handle automatically — an evaluator hiccup, or a form the browser agent couldn't confidently fill after repeated tries."
    )

    flagged = get_jobs(status="Needs Consultation")
    if not flagged:
        st.info("Nothing needs your attention right now.")
        return

    for job in flagged:
        with st.container(border=True):
            st.markdown(f"**{job['role']}** at **{job['company']}**")
            st.caption(f"Retry count: {job['retry_count']}")
            st.markdown(f"[View listing]({job['job_url']})")
            col1, col2 = st.columns(2)
            if col1.button(
                "🔁 Retry", key=f"retry_{job['id']}", use_container_width=True
            ):
                update_job_status(job["id"], "Pending")
                st.toast("Reset to Pending — will reappear in the Studio.")
                st.rerun()
            if col2.button(
                "🚫 Give Up On This One",
                key=f"giveup_{job['id']}",
                use_container_width=True,
            ):
                update_job_status(job["id"], "Dead")
                st.toast("Marked as Dead.")
                st.rerun()


tab_dashboard, tab_settings, tab_sourcing, tab_studio, tab_anomalies, tab_reports = (
    st.tabs(
        [
            "📊 Dashboard & Analytics",
            "⚙️ Settings & Guardrails",
            "🔍 Sourcing Queue",
            "📝 Live Asset Studio",
            "⚠️ Action Required",
            "📅 Reports",
        ]
    )
)

with tab_dashboard:
    render_dashboard()

with tab_settings:
    render_settings()

with tab_sourcing:
    render_sourcing_queue()
with tab_studio:
    render_asset_studio()

with tab_anomalies:
    render_action_required()

with tab_reports:
    render_reports()
