import os
import json
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import datetime
from dotenv import load_dotenv
from cryptography.fernet import Fernet
from utils.storage import read_binary, write_binary, binary_exists
from utils.auth import create_user, verify_login
from utils.user_context import set_current_user, clear_current_user
from utils.blob_store import init_blob_store
from database.db_handler import (
    update_user_email_credentials,
    get_user_email_credentials,
)
from database.db_handler import (
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
    update_job_notes,
    get_company_history,
    add_job,
    job_exists,
    get_weekly_reports,
    find_similar_jobs,
    get_referral_contact_for_company,
    update_referral_contact,
    get_skill_gap_summary,
    get_company_summary,
    get_profile_performance_summary,
    delete_job,
    get_all_generated_assets,
    clear_generated_assets,
)
from modules.scraper import source_jobs
from modules.ai_evaluator import run_sourcing_pipeline
from modules.persona_builder import (
    build_persona,
    get_persona,
    load_personas,
    delete_persona,
    polish_career_summary,
)
from modules.cv_generator import generate_for_job
from database.db_handler import get_job_by_id
from modules.email_monitor import (
    check_inbox,
    list_recent_emails,
    draft_reply,
    match_job_for_email,
)
from modules.email_monitor import check_inbox
from modules.report_generator import generate_weekly_report
from utils.settings import load_settings, save_settings
from utils.search_profiles import (
    load_profiles,
    add_profile,
    update_profile,
    delete_profile,
    get_active_profiles,
)
from utils.logger import setup_logger
from utils.theme import CUSTOM_CSS, render_kpi_row, status_badge, job_card_header
from utils.user_profile import (
    save_user_profile,
    load_user_profile,
)
from modules.persona_builder import create_persona_variant
from utils.blob_store import init_blob_store

load_dotenv()
logger = setup_logger("app")

st.set_page_config(
    page_title="Autonomous Career Engine",
    page_icon="⚙️",
    layout="wide",
    initial_sidebar_state="expanded",
)
st.html(CUSTOM_CSS)


@st.cache_data(ttl=8, show_spinner=False)
def _cached_get_jobs(user_id: int, status: str = None) -> list:
    return get_jobs(status=status)


@st.cache_data(ttl=8, show_spinner=False)
def _cached_get_recent_activity(
    user_id: int, limit: int = 30, module: str = None
) -> list:
    return get_recent_activity(limit=limit, module=module)


@st.cache_data(ttl=20, show_spinner=False)
def _cached_get_activity_modules(user_id: int) -> list:
    return get_activity_modules()


def render_auth_page():
    st.title("⚙️ Career Engine")
    tab_login, tab_signup = st.tabs(["Log In", "Sign Up"])

    with tab_login:
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Log In", type="primary"):
                user = verify_login(email.strip().lower(), password)
                if user:
                    st.session_state.logged_in_user_id = user["id"]
                    st.session_state.logged_in_email = user["email"]
                    st.rerun()
                else:
                    st.error("Invalid email or password.")

    with tab_signup:
        with st.form("signup_form"):
            new_email = st.text_input("Email", key="signup_email")
            new_password = st.text_input("Password", type="password", key="signup_pw")
            confirm_password = st.text_input(
                "Confirm Password", type="password", key="signup_pw2"
            )
            if st.form_submit_button("Create Account", type="primary"):
                if not new_email.strip() or "@" not in new_email:
                    st.error("Enter a valid email.")
                elif len(new_password) < 8:
                    st.error("Password must be at least 8 characters.")
                elif new_password != confirm_password:
                    st.error("Passwords don't match.")
                else:
                    try:
                        set_current_user(
                            0
                        )  # temporary, only to satisfy log_activity() during account creation
                        user_id = create_user(new_email.strip().lower(), new_password)
                        st.session_state.logged_in_user_id = user_id
                        st.session_state.logged_in_email = new_email.strip().lower()
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))


init_db()
init_blob_store()

if "logged_in_user_id" not in st.session_state:
    render_auth_page()
    st.stop()

set_current_user(st.session_state.logged_in_user_id)

if "app_initialized" not in st.session_state:
    apply_ghosting_webhook()
    st.session_state.app_initialized = True

MODULE_ICONS = {
    "db_handler": "🗄️",
    "settings": "⚙️",
    "scraper": "🔍",
    "ai_evaluator": "🤖",
    "browser_agent": "🌐",
    "email_monitor": "📧",
    "manual_entry": "✍️",
    "report_generator": "📅",
}

# ---------- Startup tasks — unchanged logic, still runs exactly once per session ----------
init_db()
init_blob_store()

if "logged_in_user_id" not in st.session_state:
    render_auth_page()
    st.stop()

set_current_user(st.session_state.logged_in_user_id)

if "app_initialized" not in st.session_state:
    apply_ghosting_webhook()
    st.session_state.app_initialized = True

if "inbox_checked_this_session" not in st.session_state:
    st.session_state.inbox_checked_this_session = True
    try:
        st.session_state.inbox_alert = check_inbox()
    except Exception as e:
        st.session_state.inbox_alert = None
        logger.warning(f"Automatic inbox check skipped: {e}")


def format_relative_time(timestamp_str: str) -> str:
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


def render_notes_field(job: dict, key_prefix: str):
    with st.expander("📝 Notes" + (" (saved)" if job.get("notes") else "")):
        note_text = st.text_area(
            "Notes",
            value=job.get("notes") or "",
            key=f"{key_prefix}_notes_{job['id']}",
            label_visibility="collapsed",
        )
        if st.button("💾 Save Note", key=f"{key_prefix}_save_note_{job['id']}"):
            update_job_notes(job["id"], note_text)
            st.toast("Note saved.")
            st.rerun()


def render_referral_field(job: dict, key_prefix: str):
    existing = job.get("referral_contact")
    suggested = None if existing else get_referral_contact_for_company(job["company"])
    label = "🤝 Referral Contact" + (" (saved)" if existing else "")
    with st.expander(label):
        if suggested:
            st.caption(
                f"You've recorded a contact at {job['company']} before: **{suggested}**"
            )
        contact_text = st.text_input(
            "Contact name / note",
            value=existing or "",
            key=f"{key_prefix}_referral_{job['id']}",
            label_visibility="collapsed",
            placeholder=suggested or "e.g. Jane Doe, Senior Engineer — met at PyCon",
        )
        if st.button("💾 Save Contact", key=f"{key_prefix}_save_referral_{job['id']}"):
            update_referral_contact(job["id"], contact_text)
            st.toast("Referral contact saved.")
            st.rerun()


def _ensure_persona_for_profile(profile_name: str):
    resume_path = os.path.join("data", "base_resume.pdf")
    if get_persona(profile_name) is None and os.path.exists(resume_path):
        try:
            build_persona(name=profile_name)
            st.toast(f"Auto-built persona '{profile_name}' from your resume.")
        except Exception as e:
            st.warning(f"Couldn't auto-build a persona for '{profile_name}': {e}")


if "personas_ensured_this_session" not in st.session_state:
    st.session_state.personas_ensured_this_session = True
    for _profile in load_profiles():
        _ensure_persona_for_profile(_profile["name"])
# =========================================================================
# SIDEBAR — wordmark, custom nav (session_state router), live status footer
# =========================================================================
PAGES = [
    # 1. Visibility & Urgency (What do I need to know right now?)
    ("dashboard", "Dashboard", "📊"),
    ("action", "Action Required", "⚠️"),
    # 2. The Core Workflow Funnel (The actual engine pipeline)
    ("sourcing", "Sourcing Queue", "🔍"),
    ("studio", "Live Asset Studio", "📝"),
    ("applied_inbox", "Applied & Inbox", "📬"),
    # 3. Analytics & Tracking (How is the system performing?)
    ("overview", "Overview & Data", "🗂️"),
    ("reports", "Reports", "📅"),
    # 4. Configuration & Admin (Set it and forget it)
    ("profile", "Profile", "👤"),
    ("settings", "Settings & Guardrails", "⚙️"),
]

if "active_page" not in st.session_state:
    st.session_state.active_page = "dashboard"

with st.sidebar:
    st.html(
        '<div class="sidebar-wordmark">CAREER<span>.ENGINE</span></div>'
        '<div class="sidebar-subtitle">Autonomous Pipeline</div>'
    )
    if st.button("🚪 Log Out", key="logout_btn"):
        clear_current_user()
        for k in ("logged_in_user_id", "logged_in_email", "app_initialized"):
            st.session_state.pop(k, None)
        st.rerun()

    for page_id, label, icon in PAGES:
        with st.container(key=f"nav_{page_id}"):
            is_active = st.session_state.active_page == page_id
            if st.button(
                f"{icon}  {label}",
                key=f"navbtn_{page_id}",
                type="primary" if is_active else "secondary",
                use_container_width=True,
            ):
                st.session_state.active_page = page_id
                st.rerun()

    st.divider()
    _all_jobs_sidebar = _cached_get_jobs(st.session_state.logged_in_user_id)
    _needs_you = len(
        [
            j
            for j in _all_jobs_sidebar
            if j["status"] in ("Manual Review", "Needs Consultation")
        ]
    )
    with st.container(key="glass_sidebar_status"):
        st.html(
            f'<div class="kpi-label">Needs Your Attention</div>'
            f'<div class="kpi-value" style="font-size:1.5rem;">{_needs_you}</div>'
        )

    alert = st.session_state.get("inbox_alert")
    if alert and (alert["rejections"] or alert["interviews"] or alert["flagged"]):
        st.warning(
            f"📬 {alert['rejections']} rejection(s), {alert['interviews']} interview(s), "
            f"{alert['flagged']} flagged — see Dashboard."
        )


# =========================================================================
# PAGE: Dashboard
# =========================================================================
def render_dashboard():
    st.title("Dashboard & Analytics")

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

    jobs = _cached_get_jobs(st.session_state.logged_in_user_id)
    df = pd.DataFrame(jobs)

    if df.empty:
        st.info(
            "No jobs yet — this fills in once the Sourcing Queue starts feeding jobs through."
        )
        return

    scraped = get_scraped_count()
    evaluated = len(df)
    applied = len(df[df["date_applied"].notna()])
    interview = len(df[df["status"] == "Interview"])
    scraped = max(
        scraped, evaluated
    )  # true pre-tracking history isn't recoverable — see Week 6 note

    st.html(
        render_kpi_row(
            [
                {
                    "label": "Scraped",
                    "value": str(scraped),
                    "delta": None,
                    "primary": False,
                },
                {
                    "label": "Evaluated (GO)",
                    "value": str(evaluated),
                    "delta": f"{evaluated/scraped:.0%} of scraped" if scraped else None,
                    "primary": False,
                },
                {
                    "label": "Applied",
                    "value": str(applied),
                    "delta": (
                        f"{applied/evaluated:.0%} of evaluated" if evaluated else None
                    ),
                    "primary": True,
                },
                {
                    "label": "Interviews",
                    "value": str(interview),
                    "delta": None,
                    "primary": False,
                },
            ]
        )
    )

    still_active = len(df[~df["status"].isin(["Not Interested", "Dead"])])
    fig = go.Figure(
        go.Funnel(
            y=["Scraped", "Evaluated (GO)", "Still Active", "Applied", "Interview"],
            x=[scraped, evaluated, still_active, applied, interview],
            textinfo="value+percent initial+percent previous",
            marker={"color": ["#232B35", "#2E3947", "#3A4753", "#F2A93B", "#34D399"]},
        )
    )
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font_color="#C7CFDA",
        font_family="Inter",
        margin=dict(l=10, r=10, t=10, b=10),
        height=320,
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Current Outcomes")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Awaiting Your Review", len(df[df["status"] == "Manual Review"]))
    c2.metric("Needs Consultation", len(df[df["status"] == "Needs Consultation"]))
    c3.metric("Ghosted", len(df[df["status"] == "Ghosted"]))
    c4.metric("Rejected (post-app)", len(df[df["status"] == "Rejected"]))

    st.subheader("Execution Audit Trail")
    col1, col2, col3 = st.columns([3, 2, 1])
    col1.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    modules = _cached_get_activity_modules(st.session_state.logged_in_user_id)
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
    activity = _cached_get_recent_activity(
        st.session_state.logged_in_user_id,
        limit=50,
        module=None if selected_module == "All" else selected_module,
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
        with st.container(key="glass_audit_trail"):
            for entry in activity:
                icon = MODULE_ICONS.get(entry["module"], "•")
                c1, c2 = st.columns([5, 1])
                c1.markdown(
                    f"{icon} **{entry['module']}** — {entry['action_description']}"
                )
                c2.caption(format_relative_time(entry["timestamp"]))


# =========================================================================
# PAGE: Settings & Guardrails
# =========================================================================
def render_settings():
    st.title("Settings & Guardrails")
    settings = load_settings()
    resume_path = os.path.join("data", "base_resume.pdf")

    with st.container(key="glass_resume"):
        st.subheader("Base Resume")
        uploaded_pdf = st.file_uploader("Upload your base resume (PDF)", type=["pdf"])
        if uploaded_pdf is not None:
            write_binary(resume_path, "base_resume", uploaded_pdf.getbuffer().tobytes())
            log_activity("settings", "Base resume uploaded/replaced.")
            st.toast("Resume saved.")

        if binary_exists(resume_path, "base_resume"):
            uploaded_at = format_relative_time(
                datetime.fromtimestamp(os.path.getmtime(resume_path)).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            )
            st.success(f"✅ Resume on file — last updated {uploaded_at}.")
            if st.button("🧠 Build 'default' Persona from Resume"):
                with st.spinner("Extracting resume with Gemini..."):
                    try:
                        build_persona(name="default")
                        st.toast("Persona built.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Persona extraction failed: {e}")
        else:
            st.caption("No base resume uploaded yet.")

    st.subheader("Personas")
    st.caption(
        "Auto-created per search profile. 'default' covers manually-added jobs and any profile-less fallback."
    )
    personas = load_personas()
    if not personas:
        st.info(
            "None yet — upload a resume above, then add a search profile below to auto-create one."
        )
    else:
        for name, persona in personas.items():
            with st.expander(f"👤 {name} — {persona.get('full_name', 'unnamed')}"):
                st.write(f"**Summary:** {persona.get('summary', '')}")
                st.write(f"**Skills:** {', '.join(persona.get('skills', []))}")
                st.write("**Experience:**")
                for exp in persona.get("experience", []):
                    st.caption(
                        f"{exp.get('title', '')} at {exp.get('company', '')} ({exp.get('dates', '')})"
                    )
                col1, col2 = st.columns(2)
                if col1.button(
                    "🔄 Regenerate from resume",
                    key=f"regen_{name}",
                    use_container_width=True,
                ):
                    if not os.path.exists(resume_path):
                        st.error("No base resume on file to regenerate from.")
                    else:
                        with st.spinner(f"Regenerating '{name}'..."):
                            build_persona(name=name)
                            st.toast(f"Persona '{name}' regenerated.")
                            st.rerun()
                if col2.button(
                    "🗑️ Delete", key=f"delpersona_{name}", use_container_width=True
                ):
                    delete_persona(name)
                    st.toast(f"Deleted persona '{name}'.")
                    st.rerun()

    with st.expander("➕ Add / Create Persona"):
        tab_fresh, tab_ai = st.tabs(["From Resume", "AI Variant"])
        with tab_fresh:
            with st.form("new_persona_fresh", clear_on_submit=True):
                np_name = st.text_input("Persona name")
                if st.form_submit_button("Build from Resume"):
                    if not np_name.strip():
                        st.warning("Name it first.")
                    elif not os.path.exists(resume_path):
                        st.error("No resume uploaded.")
                    else:
                        build_persona(name=np_name.strip())
                        st.toast(f"Persona '{np_name.strip()}' built.")
                        st.rerun()
        with tab_ai:
            with st.form("new_persona_variant", clear_on_submit=True):
                base_choice = st.selectbox(
                    "Base persona to build from",
                    list(personas.keys()) if personas else [],
                )
                variant_name = st.text_input("New persona name")
                instruction = st.text_area(
                    "Describe the angle",
                    placeholder="e.g. Emphasize backend and distributed systems work, downplay frontend",
                )
                if st.form_submit_button("✨ Generate Variant"):
                    if not (
                        base_choice and variant_name.strip() and instruction.strip()
                    ):
                        st.warning("Fill in all three fields.")
                    else:
                        with st.spinner("Generating persona variant..."):
                            try:
                                create_persona_variant(
                                    base_choice,
                                    variant_name.strip(),
                                    instruction.strip(),
                                )
                                st.toast(f"Variant '{variant_name.strip()}' created.")
                                st.rerun()
                            except Exception as e:
                                st.error(f"Failed: {e}")
    st.subheader("Search Profiles")
    st.caption(
        "Fully independent scrape configs. 'Trigger JobSpy' in the Sourcing Queue runs every active one below, in one click."
    )
    site_options = ["indeed", "linkedin", "zip_recruiter", "glassdoor", "google"]

    for profile in load_profiles():
        status_label = "🟢 active" if profile.get("active", True) else "⚪ paused"
        with st.expander(
            f"{profile['name']} — {status_label} — \"{profile['search_term']}\" in {profile['location']}"
        ):
            with st.form(f"edit_profile_{profile['name']}"):
                e_term = st.text_input("Search term", value=profile["search_term"])
                e_loc = st.text_input("Location", value=profile["location"])
                e_country = st.text_input(
                    "Country (for Indeed/Glassdoor)",
                    value=profile.get("country_indeed", ""),
                )
                e_sites = st.multiselect(
                    "Sites to search", site_options, default=profile["site_names"]
                )
                e_results = st.number_input(
                    "Results wanted per run",
                    min_value=1,
                    max_value=200,
                    value=profile["results_wanted"],
                )
                e_hours = st.number_input(
                    "Only show listings posted in the last (hours)",
                    min_value=1,
                    max_value=720,
                    value=profile["hours_old"],
                )
                e_active = st.checkbox(
                    "Active — include in 'Trigger JobSpy' runs",
                    value=profile.get("active", True),
                )
                if st.form_submit_button("💾 Save Changes"):
                    update_profile(
                        profile["name"],
                        {
                            "name": profile["name"],
                            "search_term": e_term,
                            "location": e_loc,
                            "country_indeed": e_country,
                            "site_names": e_sites,
                            "results_wanted": e_results,
                            "hours_old": e_hours,
                            "active": e_active,
                        },
                    )
                    st.toast(f"'{profile['name']}' updated.")
                    st.rerun()
            if st.button(
                f"🗑️ Delete '{profile['name']}'", key=f"delete_{profile['name']}"
            ):
                delete_profile(profile["name"])
                st.toast(
                    f"Deleted '{profile['name']}'. Its persona is kept — it may still be tied to real applications."
                )
                st.rerun()

    with st.expander("➕ Add New Search Profile"):
        with st.form("add_profile_form", clear_on_submit=True):
            n_name = st.text_input("Profile name (e.g. 'Backend, Lahore')")
            n_term = st.text_input("Search term", value="software engineer")
            n_loc = st.text_input("Location", value="Lahore, Pakistan")
            n_country = st.text_input(
                "Country (for Indeed/Glassdoor)", value="Pakistan"
            )
            n_sites = st.multiselect(
                "Sites to search",
                site_options,
                default=["indeed", "linkedin", "zip_recruiter"],
            )
            n_results = st.number_input(
                "Results wanted per run", min_value=1, max_value=200, value=20
            )
            n_hours = st.number_input(
                "Only show listings posted in the last (hours)",
                min_value=1,
                max_value=720,
                value=48,
            )
            n_active = st.checkbox("Active", value=True)
            if st.form_submit_button("➕ Create Profile"):
                if not n_name.strip():
                    st.warning("Give the profile a name.")
                elif not n_sites:
                    st.warning("Pick at least one site.")
                else:
                    try:
                        add_profile(
                            {
                                "name": n_name.strip(),
                                "search_term": n_term,
                                "location": n_loc,
                                "country_indeed": n_country,
                                "site_names": n_sites,
                                "results_wanted": n_results,
                                "hours_old": n_hours,
                                "active": n_active,
                            }
                        )
                        _ensure_persona_for_profile(n_name.strip())
                        st.toast(f"Profile '{n_name.strip()}' created.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))

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
        with st.container(key=f"card_flag_{flag}"):
            col1, col2 = st.columns([5, 1])
            col1.write(f"🚩 {flag}")
            if col2.button("🗑️ Remove", key=f"remove_{flag}", use_container_width=True):
                settings["red_flags"].remove(flag)
                save_settings(settings)
                log_activity("settings", f"Red flag removed: '{flag}'")
                st.toast(f"Removed red flag: {flag}")
                st.rerun()

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
    st.subheader("Email Connection")
    st.caption(
        "Your own email, used only to scan for replies about your own applications. Stored encrypted — no one else can read it."
    )
    current_email, current_server, _ = get_user_email_credentials(
        st.session_state.logged_in_user_id
    )
    with st.form("email_creds_form"):
        e_email = st.text_input("Email address", value=current_email or "")
        e_server = st.text_input(
            "IMAP Server", value=current_server or "imap.gmail.com"
        )
        e_password = st.text_input(
            "App Password",
            type="password",
            placeholder="Leave blank to keep your current saved password",
        )
        if st.form_submit_button("💾 Save Email Credentials"):
            if not e_email.strip():
                st.warning("Enter an email address.")
            else:
                update_user_email_credentials(
                    st.session_state.logged_in_user_id,
                    e_email.strip(),
                    e_server.strip(),
                    e_password or None,
                )
                st.toast("Email credentials saved.")
                st.rerun()

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


# =========================================================================
# PAGE: Sourcing Queue
# =========================================================================
def render_sourcing_queue():
    st.title("Sourcing Queue")

    if st.button("🚀 Trigger JobSpy", type="primary"):
        active_profiles = get_active_profiles()
        if not active_profiles:
            st.warning("No active search profiles — add one in Settings first.")
        else:
            total = {
                "auto_approved": 0,
                "manual_review": 0,
                "rejected": 0,
                "needs_consultation": 0,
            }
            summaries = []
            with st.spinner(
                f"Running {len(active_profiles)} profile(s) — can take a few minutes..."
            ):
                for profile in active_profiles:
                    candidates = source_jobs(profile)
                    if candidates:
                        counts = run_sourcing_pipeline(candidates)
                        for k in total:
                            total[k] += counts[k]
                        summaries.append(
                            f"**{profile['name']}**: {len(candidates)} candidate(s)"
                        )
                    else:
                        summaries.append(f"**{profile['name']}**: 0 candidates")
            st.success(
                f"Across {len(active_profiles)} profile(s) — {total['auto_approved']} auto-approved, "
                f"{total['manual_review']} need your review, {total['rejected']} rejected, "
                f"{total['needs_consultation']} need a look."
            )
            for s in summaries:
                st.caption(s)
        st.rerun()

    st.subheader("Awaiting Your Review")
    st.caption(
        "Passed red-flag filtering and got a GO from the evaluator, but scored below your confidence threshold."
    )
    pending = _cached_get_jobs(
        st.session_state.logged_in_user_id, status="Manual Review"
    )
    if not pending:
        st.info("Nothing waiting on you right now.")
    else:
        for job in pending:
            with st.container(key=f"card_{job['id']}"):
                st.html(
                    job_card_header(
                        job["role"],
                        job["company"],
                        job["status"],
                        job["match_score"],
                        job.get("job_source", "unknown"),
                    )
                )
                st.caption(job.get("evaluator_reason", ""))
                st.markdown(f"[View listing]({job['job_url']})")
                col1, col2 = st.columns(2)
                if col1.button(
                    "✅ Approve", key=f"approve_{job['id']}", use_container_width=True
                ):
                    update_job_status(job["id"], "Pending")
                    with st.spinner("Generating tailored CV..."):
                        try:
                            generate_for_job(get_job_by_id(job["id"]))
                            st.toast(f"Approved and generated CV for: {job['role']}")
                        except Exception as e:
                            st.toast(
                                f"Approved — auto-generation failed ({e}). Generate manually in the Studio.",
                                icon="⚠️",
                            )
                    st.rerun()
                if col2.button(
                    "❌ Reject", key=f"reject_{job['id']}", use_container_width=True
                ):
                    update_job_status(job["id"], "Not Interested")
                    st.toast(f"Passed on: {job['role']}")
                    st.rerun()
                render_notes_field(job, "manual_review")
                render_referral_field(job, "manual_review")

    with st.container(key="glass_manual_add"):
        st.subheader("Add a Job Manually")
        st.caption(
            "Found something yourself outside JobSpy? Skips red-flag filtering and AI evaluation since you've already decided it's worth pursuing."
        )
        with st.form("manual_add_job_form", clear_on_submit=True):
            m_company = st.text_input("Company")
            m_role = st.text_input("Role / Job Title")
            m_url = st.text_input("Job posting URL")
            m_description = st.text_area("Job description", height=150)
            if st.form_submit_button("➕ Add to Pipeline"):
                if not (m_company.strip() and m_role.strip() and m_url.strip()):
                    st.warning("Company, role, and URL are all required.")
                elif job_exists(m_url.strip()):
                    st.warning("This job URL is already in your pipeline.")
                else:
                    similar = find_similar_jobs(m_company.strip(), m_role.strip())
                    new_job_id = add_job(
                        company=m_company.strip(),
                        role=m_role.strip(),
                        job_url=m_url.strip(),
                        job_description=m_description.strip(),
                        job_source="Manual",
                    )
                    log_activity(
                        "manual_entry",
                        f"Manually added: {m_role.strip()} at {m_company.strip()}",
                    )
                    with st.spinner("Generating tailored CV..."):
                        try:
                            generate_for_job(get_job_by_id(new_job_id))
                        except Exception:
                            pass  # falls through to 'Ready to Generate' in the Studio
                    if similar:
                        st.toast(
                            f"Added — heads up, similar to '{similar[0]['role']}' already on file ({similar[0]['status']}).",
                            icon="⚠️",
                        )
                    else:
                        st.toast("Added and CV generated.")
                    st.rerun()


# =========================================================================
# PAGE: Live Asset Studio
# =========================================================================
def render_asset_studio():
    st.title("Live Asset Studio")
    st.caption(
        "Review and approve the tailored CV and cover letter before anything gets submitted."
    )
    all_generated = get_all_generated_assets()
    ats_scored = [j for j in all_generated if j.get("ats_keywords_total")]
    avg_match = None
    scored_matches = [
        j["match_score"] for j in all_generated if j.get("match_score") is not None
    ]
    if scored_matches:
        avg_match = sum(scored_matches) / len(scored_matches)
    avg_ats = None
    if ats_scored:
        avg_ats = sum(
            j["ats_keywords_matched"] / j["ats_keywords_total"] for j in ats_scored
        ) / len(ats_scored)

    jobs = _cached_get_jobs(st.session_state.logged_in_user_id, status="Pending")
    needs_generation = [j for j in jobs if not j.get("generated_cv")]
    needs_review = [
        j for j in jobs if j.get("generated_cv") and not j.get("cv_approved_at")
    ]
    approved = [j for j in jobs if j.get("cv_approved_at")]

    st.html(
        render_kpi_row(
            [
                {
                    "label": "Ready to Generate",
                    "value": str(len(needs_generation)),
                    "delta": None,
                    "primary": False,
                },
                {
                    "label": "Awaiting Review",
                    "value": str(len(needs_review)),
                    "delta": None,
                    "primary": True,
                },
                {
                    "label": "Avg Match Score",
                    "value": f"{avg_match:.0%}" if avg_match is not None else "—",
                    "delta": None,
                    "primary": False,
                },
                {
                    "label": "Avg ATS Keywords",
                    "value": f"{avg_ats:.0%}" if avg_ats is not None else "—",
                    "delta": None,
                    "primary": False,
                },
            ]
        )
    )

    if not jobs:
        st.info(
            "No jobs ready for the Studio yet — approve some in the Sourcing Queue first."
        )
        return

    if needs_generation:
        st.subheader("Ready to Generate")
        for job in needs_generation:
            with st.container(key=f"card_{job['id']}"):
                st.html(
                    job_card_header(
                        job["role"],
                        job["company"],
                        job["status"],
                        job.get("match_score"),
                        job.get("job_source", "unknown"),
                    )
                )

                history = get_company_history(job["company"], exclude_job_id=job["id"])
                if history:
                    similar_ids = {
                        s["id"]
                        for s in find_similar_jobs(
                            job["company"], job["role"], exclude_job_id=job["id"]
                        )
                    }
                    prior_persona = next(
                        (h["persona_used"] for h in history if h["persona_used"]), None
                    )
                    with st.expander(
                        f"⚠️ {len(history)} prior application(s) at this company"
                    ):
                        for h in history:
                            flag = (
                                " 🔁 **nearly identical title**"
                                if h["id"] in similar_ids
                                else ""
                            )
                            st.caption(
                                f"{h['role']} — {h['status']} — persona: {h.get('persona_used') or 'none yet'} — {h['date_added'][:10]}{flag}"
                            )
                    if prior_persona:
                        st.warning(
                            f"Guardrail will force persona **'{prior_persona}'** — same one used for this company before."
                        )

                if st.button("✨ Generate", key=f"gen_{job['id']}"):
                    with st.spinner(f"Generating tailored CV for {job['company']}..."):
                        try:
                            generate_for_job(job)
                            st.toast(f"Generated assets for {job['role']}")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Generation failed: {e}")
                render_notes_field(job, "studio_gen")
                render_referral_field(job, "studio_gen")

    if needs_review:
        st.subheader("Awaiting Your Review")
        for job in needs_review:
            with st.container(key=f"card_{job['id']}"):
                st.html(
                    job_card_header(
                        job["role"],
                        job["company"],
                        job["status"],
                        job.get("match_score"),
                        job.get("job_source", "unknown"),
                    )
                )
                st.caption(f"Persona: `{job['persona_used']}`")
                if job.get("ats_keywords_total"):
                    pct = job["ats_keywords_matched"] / job["ats_keywords_total"]
                    missing = json.loads(job.get("ats_missing_keywords") or "[]")
                    st.caption(
                        f"ATS keyword match: **{job['ats_keywords_matched']}/{job['ats_keywords_total']}** ({pct:.0%})"
                        + (f" — missing: {', '.join(missing[:6])}" if missing else "")
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
                    "Editing here updates the saved text for your records — the downloadable .docx is Gemini's original generation. Polish the actual file in Word before it goes anywhere."
                )

                cv_key = (
                    f"cv_docx::{os.path.basename(docx_path)}" if docx_path else None
                )
                if docx_path and (os.path.exists(docx_path) or True):
                    cv_bytes = read_binary(docx_path, cv_key)
                    if cv_bytes:
                        st.download_button(
                            "⬇️ Download tailored CV (.docx)",
                            data=cv_bytes,
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
                render_notes_field(job, "studio_review")
                render_referral_field(job, "studio_review")

    if approved:
        st.subheader("Approved — Ready to Apply")
        st.caption("Open the posting yourself and apply — confirm here once you have.")
        for job in approved:
            with st.container(key=f"card_{job['id']}"):
                st.html(
                    job_card_header(
                        job["role"],
                        job["company"],
                        job["status"],
                        job.get("match_score"),
                        job.get("job_source", "unknown"),
                    )
                )
                st.caption(f"Approved {format_relative_time(job['cv_approved_at'])}")
                st.markdown(f"[🔗 Open Job Posting]({job['job_url']})")

                docx_path = job.get("docx_path")
                cv_key = (
                    f"cv_docx::{os.path.basename(docx_path)}" if docx_path else None
                )
                if docx_path and (os.path.exists(docx_path) or True):
                    cv_bytes = read_binary(docx_path, cv_key)
                    if cv_bytes:
                        st.download_button(
                            "⬇️ Download tailored CV",
                            data=cv_bytes,
                            file_name=os.path.basename(docx_path),
                            key=f"finaldl_{job['id']}",
                        )

                if st.button(
                    "✅ Mark as Applied", key=f"markapplied_{job['id']}", type="primary"
                ):
                    update_job_status(job["id"], "Applied")
                    st.toast(f"Marked as Applied: {job['role']}")
                    st.rerun()
                render_notes_field(job, "studio_approved")
                render_referral_field(job, "studio_approved")

    st.subheader("📁 All Generated CVs")
    st.caption(
        "Every CV ever generated, regardless of current status — download, or clear to regenerate from scratch."
    )
    if not all_generated:
        st.info("None generated yet.")
    else:
        for job in all_generated:
            with st.container(key=f"card_cvlib_{job['id']}"):
                st.html(
                    job_card_header(
                        job["role"],
                        job["company"],
                        job["status"],
                        job.get("match_score"),
                        job.get("job_source", "unknown"),
                    )
                )
                col1, col2 = st.columns(2)
                docx_path = job.get("docx_path")
                cv_key = (
                    f"cv_docx::{os.path.basename(docx_path)}" if docx_path else None
                )
                if docx_path and (os.path.exists(docx_path) or True):
                    cv_bytes = read_binary(docx_path, cv_key)
                    if cv_bytes:
                        col1.download_button(
                            "⬇️ Download",
                            data=cv_bytes,
                            file_name=os.path.basename(docx_path),
                            key=f"dl_lib_{job['id']}",
                            use_container_width=True,
                        )
                if col2.button(
                    "🗑️ Delete & Reset",
                    key=f"clear_{job['id']}",
                    use_container_width=True,
                ):
                    clear_generated_assets(job["id"])
                    st.toast("Cleared — will reappear under 'Ready to Generate'.")
                    st.rerun()


# =========================================================================
# PAGE: Action Required
# =========================================================================
def render_action_required():
    st.title("Action Required")
    st.caption(
        "Jobs the pipeline couldn't handle automatically — an evaluator hiccup, or a form the browser agent couldn't confidently fill after repeated tries."
    )

    flagged = _cached_get_jobs(
        st.session_state.logged_in_user_id, status="Needs Consultation"
    )
    if not flagged:
        st.info("Nothing needs your attention right now.")
        return

    for job in flagged:
        with st.container(key=f"card_{job['id']}"):
            st.html(
                job_card_header(
                    job["role"],
                    job["company"],
                    job["status"],
                    job.get("match_score"),
                    job.get("job_source", "unknown"),
                )
            )
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
            render_notes_field(job, "anomaly")
            render_referral_field(job, "anomaly")


# =========================================================================
# PAGE: Reports
# =========================================================================
def render_reports():
    st.title("Weekly Reports")
    st.caption(
        "Generate anytime — every report stays saved below so you can compare over time."
    )

    days = st.number_input("Report period (days)", min_value=1, max_value=90, value=7)
    if st.button("📊 Generate Report", type="primary"):
        with st.spinner("Crunching numbers..."):
            generate_weekly_report(days=days)
            st.toast("Report generated.")
            st.rerun()

    st.subheader("Report History")
    reports = get_weekly_reports(limit=20)
    if not reports:
        st.info("No reports generated yet.")
        return

    for report in reports:
        stats = json.loads(report["stats_json"])
        with st.container(key=f"card_report_{report['id']}"):
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


def render_overview():
    st.title("Overview & Data")
    st.caption(
        "Every job in one place — search, sort, and act directly from the table."
    )

    jobs = _cached_get_jobs(st.session_state.logged_in_user_id)
    if not jobs:
        st.info("No jobs yet.")
        return

    df = pd.DataFrame(jobs)

    with st.container(key="glass_overview_filters"):
        c1, c2, c3 = st.columns([2, 2, 2])
        search = c1.text_input("🔍 Search role or company", "")
        status_filter = c2.multiselect(
            "Filter by status", sorted(df["status"].unique().tolist())
        )
        source_options = (
            sorted(df["job_source"].dropna().unique().tolist())
            if "job_source" in df
            else []
        )
        source_filter = c3.multiselect("Filter by source", source_options)

    filtered = df.copy()
    if search:
        mask = filtered["role"].str.contains(search, case=False, na=False) | filtered[
            "company"
        ].str.contains(search, case=False, na=False)
        filtered = filtered[mask]
    if status_filter:
        filtered = filtered[filtered["status"].isin(status_filter)]
    if source_filter:
        filtered = filtered[filtered["job_source"].isin(source_filter)]

    st.caption(f"Showing {len(filtered)} of {len(df)} jobs")

    display_df = filtered[
        [
            "id",
            "role",
            "company",
            "status",
            "match_score",
            "ats_keywords_matched",
            "ats_keywords_total",
            "job_source",
            "search_profile",
            "persona_used",
            "referral_contact",
            "date_added",
        ]
    ].copy()
    display_df["ats_score_display"] = display_df.apply(
        lambda r: (
            (r["ats_keywords_matched"] / r["ats_keywords_total"])
            if pd.notna(r["ats_keywords_total"]) and r["ats_keywords_total"]
            else None
        ),
        axis=1,
    )

    event = st.dataframe(
        display_df.drop(columns=["ats_keywords_matched", "ats_keywords_total"]),
        key="overview_table",
        on_select="rerun",
        selection_mode="single-row",
        hide_index=True,
        use_container_width=True,
        height=420,
        column_config={
            "id": None,
            "role": st.column_config.TextColumn("Role", width="medium"),
            "company": st.column_config.TextColumn("Company"),
            "status": st.column_config.TextColumn("Status"),
            "match_score": st.column_config.ProgressColumn(
                "Match", min_value=0, max_value=1, format="%.0f%%"
            ),
            "ats_score_display": st.column_config.ProgressColumn(
                "ATS Keywords", min_value=0, max_value=1, format="%.0f%%"
            ),
            "job_source": st.column_config.TextColumn("Source"),
            "search_profile": st.column_config.TextColumn("Profile"),
            "persona_used": st.column_config.TextColumn("Persona"),
            "referral_contact": st.column_config.TextColumn("Referral"),
            "date_added": st.column_config.TextColumn("Added"),
        },
    )

    selected_rows = event.selection.rows if event and event.selection else []
    if selected_rows:
        selected_id = int(display_df.iloc[selected_rows[0]]["id"])
        selected_job = next(j for j in jobs if j["id"] == selected_id)

        with st.container(key="glass_overview_detail"):
            st.subheader(f"{selected_job['role']} at {selected_job['company']}")
            st.html(status_badge(selected_job["status"]))
            st.markdown(f"[View listing]({selected_job['job_url']})")
            st.caption((selected_job.get("job_description") or "")[:400] + "...")

            col1, col2, col3 = st.columns([2, 1, 1])
            status_options = [
                "Pending",
                "Manual Review",
                "Not Interested",
                "Needs Consultation",
                "Applied",
                "Rejected",
                "Interview",
                "Ghosted",
                "Failed - Retry",
                "Dead",
            ]
            current_idx = (
                status_options.index(selected_job["status"])
                if selected_job["status"] in status_options
                else 0
            )
            new_status = col1.selectbox(
                "Change status",
                status_options,
                index=current_idx,
                key=f"overview_status_{selected_id}",
            )
            if col2.button("Apply", key=f"overview_apply_status_{selected_id}"):
                update_job_status(selected_id, new_status)
                st.toast(f"Status updated to {new_status}")
                st.rerun()
            if col3.button("🗑️ Delete", key=f"overview_delete_{selected_id}"):
                delete_job(selected_id)
                st.toast("Job deleted.")
                st.rerun()

            render_notes_field(selected_job, "overview")
            render_referral_field(selected_job, "overview")

    st.subheader("By Company")
    company_summary = get_company_summary()
    if company_summary:
        st.dataframe(
            pd.DataFrame(company_summary),
            hide_index=True,
            use_container_width=True,
            column_config={
                "company": "Company",
                "total_jobs": "Total Jobs",
                "applied_count": "Applied",
                "interview_count": "Interviews",
                "persona_used": "Persona",
                "referral_contact": "Referral Contact",
                "latest_activity": "Latest Activity",
            },
        )

    st.subheader("By Search Profile")
    profile_summary = get_profile_performance_summary()
    if profile_summary:
        st.dataframe(
            pd.DataFrame(profile_summary),
            hide_index=True,
            use_container_width=True,
            column_config={
                "search_profile": "Profile",
                "total_jobs": "Sourced",
                "applied_count": "Applied",
                "interview_count": "Interviews",
                "avg_match_score": st.column_config.ProgressColumn(
                    "Avg Match", min_value=0, max_value=1, format="%.0f%%"
                ),
            },
        )

    st.subheader("Skill Gaps — Most Frequently Missing")
    st.caption(
        "Aggregated across every CV you've generated. Higher count = shows up in postings more often but isn't in your current persona."
    )
    gaps = get_skill_gap_summary(top_n=15)
    if not gaps:
        st.info("No data yet — this fills in as you generate CVs in the Studio.")
    else:
        gap_df = pd.DataFrame(gaps, columns=["Skill / Keyword", "Times Missing"])
        st.dataframe(
            gap_df,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Times Missing": st.column_config.ProgressColumn(
                    "Times Missing",
                    min_value=0,
                    max_value=max(c for _, c in gaps),
                    format="%d",
                )
            },
        )


def render_applied_and_inbox():
    st.title("Applied Jobs & Inbox")

    st.subheader("Applied Jobs")
    jobs = _cached_get_jobs(st.session_state.logged_in_user_id)
    applied_jobs = [j for j in jobs if j.get("date_applied")]

    if not applied_jobs:
        st.info("Nothing applied to yet.")
    else:
        adf = pd.DataFrame(applied_jobs)
        display_cols = [
            c
            for c in [
                "role",
                "company",
                "status",
                "persona_used",
                "job_url",
                "referral_contact",
                "search_profile",
                "date_applied",
            ]
            if c in adf.columns
        ]
        st.dataframe(
            adf[display_cols],
            hide_index=True,
            use_container_width=True,
            column_config={
                "role": "Role",
                "company": "Company",
                "status": "Status",
                "persona_used": "Persona Used",
                "job_url": st.column_config.LinkColumn("Posting"),
                "referral_contact": "Referral",
                "search_profile": "Profile",
                "date_applied": "Applied On",
            },
        )
        st.caption(
            "Need to change a status, add a note, or delete one? That's in Overview & Data — this view is for a clean at-a-glance list."
        )

        with st.expander("Download CVs for applied jobs"):
            for job in applied_jobs:
                if job.get("docx_path") and os.path.exists(job["docx_path"]):
                    with open(job["docx_path"], "rb") as f:
                        st.download_button(
                            f"⬇️ {job['role']} @ {job['company']}",
                            data=f.read(),
                            file_name=os.path.basename(job["docx_path"]),
                            key=f"appdl_{job['id']}",
                        )

    st.divider()
    st.subheader("📧 Inbox")

    col1, col2 = st.columns([1, 5])
    if col1.button("🔄 Refresh"):
        try:
            st.session_state.recent_emails = list_recent_emails(limit=20)
            st.toast("Inbox refreshed.")
        except Exception as e:
            st.error(f"Couldn't fetch inbox: {e}")
        st.rerun()

    if "recent_emails" not in st.session_state:
        try:
            st.session_state.recent_emails = list_recent_emails(limit=20)
        except Exception as e:
            st.session_state.recent_emails = []
            st.warning(f"Couldn't load inbox: {e}")

    if not st.session_state.recent_emails:
        st.info("No messages loaded — hit Refresh.")
    else:
        for mail in st.session_state.recent_emails:
            with st.container(key=f"card_mail_{mail['uid']}"):
                st.markdown(f"**{mail['subject']}**")
                st.caption(f"From: {mail['sender']} — {mail['date']}")
                with st.expander("View message"):
                    st.write(mail["body"][:2000])

                matched_job = match_job_for_email(mail["subject"], mail["sender"], jobs)
                if matched_job:
                    st.caption(
                        f"🔗 Looks related to: **{matched_job['role']}** at **{matched_job['company']}**"
                    )

                draft_key = f"draft_{mail['uid']}"
                if st.button("✨ Draft AI Reply", key=f"draftbtn_{mail['uid']}"):
                    with st.spinner("Drafting..."):
                        try:
                            st.session_state[draft_key] = draft_reply(
                                mail["sender"],
                                mail["subject"],
                                mail["body"],
                                job=matched_job,
                            )
                        except Exception as e:
                            st.error(f"Draft failed: {e}")

                if draft_key in st.session_state:
                    st.text_area(
                        "Drafted reply — copy and send from your own email client",
                        value=st.session_state[draft_key],
                        key=f"draftarea_{mail['uid']}",
                        height=150,
                    )


def render_profile():
    st.title("Profile")
    st.caption(
        "Everything the pipeline knows about you outside your resume-based personas — feeds into cover letters and generated documents."
    )

    profile = load_user_profile()

    with st.container(key="glass_profile_form"):
        with st.form("user_profile_form"):
            col1, col2 = st.columns(2)
            full_name = col1.text_input("Full Name", value=profile["full_name"])
            email = col2.text_input("Email", value=profile["email"])
            phone = col1.text_input("Phone", value=profile["phone"])
            location = col2.text_input("Location", value=profile["location"])
            linkedin_url = col1.text_input(
                "LinkedIn URL", value=profile["linkedin_url"]
            )
            portfolio_url = col2.text_input(
                "Portfolio URL", value=profile["portfolio_url"]
            )
            github_url = col1.text_input("GitHub URL", value=profile["github_url"])
            work_authorization = col2.text_input(
                "Work Authorization",
                value=profile["work_authorization"],
                placeholder="e.g. Authorized to work in Pakistan",
            )
            notice_period = col1.text_input(
                "Notice Period",
                value=profile["notice_period"],
                placeholder="e.g. 2 weeks",
            )
            desired_salary_range = col2.text_input(
                "Desired Salary Range", value=profile["desired_salary_range"]
            )
            career_summary = st.text_area(
                "Career Summary", value=profile["career_summary"], height=120
            )

            if st.form_submit_button("💾 Save Profile"):
                save_user_profile(
                    {
                        "full_name": full_name,
                        "email": email,
                        "phone": phone,
                        "location": location,
                        "linkedin_url": linkedin_url,
                        "portfolio_url": portfolio_url,
                        "github_url": github_url,
                        "work_authorization": work_authorization,
                        "notice_period": notice_period,
                        "desired_salary_range": desired_salary_range,
                        "career_summary": career_summary,
                    }
                )
                st.toast("Profile saved.")
                st.rerun()

    if st.button("✨ Improve Career Summary with AI"):
        default_persona = get_persona("default")
        background = ""
        if default_persona:
            background = f"{default_persona.get('summary', '')} Skills: {', '.join(default_persona.get('skills', []))}"
        with st.spinner("Polishing..."):
            try:
                improved = polish_career_summary(profile["career_summary"], background)
                profile["career_summary"] = improved
                save_user_profile(profile)
                st.toast("Summary improved.")
                st.rerun()
            except Exception as e:
                st.error(f"AI polish failed: {e}")


# ---------- Router ----------
PAGE_RENDERERS = {
    "dashboard": render_dashboard,
    "overview": render_overview,
    "profile": render_profile,
    "settings": render_settings,
    "sourcing": render_sourcing_queue,
    "studio": render_asset_studio,
    "applied_inbox": render_applied_and_inbox,
    "action": render_action_required,
    "reports": render_reports,
}
PAGE_RENDERERS[st.session_state.active_page]()
