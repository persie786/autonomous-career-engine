CUSTOM_CSS = """
<style>
/* ---------- Strip default Streamlit chrome, keep sidebar toggle functional ---------- */
#MainMenu, footer { visibility: hidden; height: 0; }
header[data-testid="stHeader"] { background: transparent; }
.block-container { padding-top: 1.75rem; padding-bottom: 2.5rem; max-width: 1180px; }
div[data-testid="stVerticalBlock"] { gap: 0.6rem; }

/* ---------- Typography rhythm ---------- */
h1, h2, h3 { letter-spacing: -0.02em; }
[data-testid="stCaptionContainer"] { font-family: 'JetBrains Mono', monospace; }

/* ---------- Sidebar shell ---------- */
section[data-testid="stSidebar"] .block-container { padding-top: 1.6rem; }
.sidebar-wordmark {
  font-family: 'Space Grotesk', sans-serif; font-weight: 700; font-size: 1.15rem;
  color: #E8ECF1; letter-spacing: -0.01em; margin-bottom: 0.1rem;
}
.sidebar-wordmark span { color: #F2A93B; }
.sidebar-subtitle {
  font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; color: #57616F;
  text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 1.4rem;
}

/* Sidebar nav — each real st.button lives in st.container(key=f"nav_{id}").
   Active vs inactive is Streamlit's own button `type` (primary/secondary),
   not a hand-rolled CSS class — native state, not faked state. */
div[class*="st-key-nav_"] button {
  justify-content: flex-start !important;
  font-family: 'Space Grotesk', sans-serif !important;
  font-weight: 500 !important;
  font-size: 0.9rem !important;
  padding: 0.55rem 0.8rem !important;
  border: 1px solid transparent !important;
  width: 100%;
  transition: all 150ms ease-out;
}
div[class*="st-key-nav_"] button:hover {
  border-color: #232B35 !important;
  transform: translateX(2px);
}
div[class*="st-key-nav_"] button[kind="primary"] {
  box-shadow: 0 0 0 1px rgba(242,169,59,0.35), 0 4px 18px -6px rgba(242,169,59,0.4);
}
div[class*="st-key-nav_"] { margin-bottom: 0.2rem; }

/* ---------- Generic glass panel (wrap any st.container(key="glass_...")) ---------- */
div[class*="st-key-glass_"] {
  background: linear-gradient(180deg, rgba(255,255,255,0.035), rgba(255,255,255,0.01));
  backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 1rem;
  padding: 1.2rem 1.35rem;
}

/* ---------- Job / entity cards (st.container(key="card_...")) ---------- */
div[class*="st-key-card_"] {
  background: linear-gradient(180deg, rgba(255,255,255,0.032), rgba(255,255,255,0.008));
  border: 1px solid #1D242D;
  border-radius: 1rem;
  padding: 1.05rem 1.3rem 0.85rem;
  margin-bottom: 0.8rem;
  transition: border-color 180ms ease-out, transform 180ms ease-out, box-shadow 180ms ease-out;
}
div[class*="st-key-card_"]:hover {
  border-color: #2E3947;
  transform: translateY(-2px);
  box-shadow: 0 14px 34px -14px rgba(0,0,0,0.55);
}

/* ---------- KPI instrument strip (pure HTML — own classes, no Streamlit targeting needed) ---------- */
.kpi-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 0.8rem; margin: 0.2rem 0 1.5rem; }
.kpi-tile {
  background: linear-gradient(165deg, rgba(255,255,255,0.045), rgba(255,255,255,0.01));
  border: 1px solid rgba(255,255,255,0.07);
  border-radius: 0.875rem;
  padding: 0.95rem 1.1rem;
  backdrop-filter: blur(10px); -webkit-backdrop-filter: blur(10px);
}
.kpi-tile.is-primary { border-color: rgba(242,169,59,0.4); box-shadow: 0 0 26px -8px rgba(242,169,59,0.3); }
.kpi-label { font-family: 'Inter', sans-serif; font-size: 0.7rem; font-weight: 600; letter-spacing: 0.08em; text-transform: uppercase; color: #7C8797; margin-bottom: 0.3rem; }
.kpi-value { font-family: 'JetBrains Mono', monospace; font-size: 1.85rem; font-weight: 600; color: #E8ECF1; line-height: 1; }
.kpi-tile.is-primary .kpi-value { color: #F2A93B; }
.kpi-delta { font-family: 'JetBrains Mono', monospace; font-size: 0.76rem; margin-top: 0.35rem; color: #7C8797; }

/* ---------- Status badges ---------- */
.status-badge {
  display: inline-flex; align-items: center; gap: 0.35rem;
  font-family: 'JetBrains Mono', monospace; font-size: 0.68rem; font-weight: 600;
  letter-spacing: 0.03em; text-transform: uppercase;
  padding: 0.2rem 0.55rem; border-radius: 999px; border: 1px solid transparent;
  white-space: nowrap;
}
.status-badge .dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; }
.status-badge.st-applied { background: rgba(52,211,153,0.12); color: #34D399; border-color: rgba(52,211,153,0.3); }
.status-badge.st-applied .dot { background: #34D399; box-shadow: 0 0 8px #34D399; }
.status-badge.st-pending { background: rgba(242,169,59,0.12); color: #F2A93B; border-color: rgba(242,169,59,0.3); }
.status-badge.st-pending .dot { background: #F2A93B; box-shadow: 0 0 8px #F2A93B; }
.status-badge.st-interview { background: rgba(56,189,248,0.12); color: #38BDF8; border-color: rgba(56,189,248,0.3); }
.status-badge.st-interview .dot { background: #38BDF8; box-shadow: 0 0 8px #38BDF8; }
.status-badge.st-rejected { background: rgba(251,113,133,0.12); color: #FB7185; border-color: rgba(251,113,133,0.3); }
.status-badge.st-rejected .dot { background: #FB7185; }
.status-badge.st-neutral { background: rgba(100,116,139,0.16); color: #94A3B3; border-color: rgba(100,116,139,0.3); }
.status-badge.st-neutral .dot { background: #94A3B3; }

/* ---------- Card internals ---------- */
.jc-top { display:flex; justify-content: space-between; align-items:flex-start; gap: 0.6rem; margin-bottom:0.45rem; }
.jc-role { font-family:'Space Grotesk',sans-serif; font-weight:600; font-size:1.02rem; color:#E8ECF1; }
.jc-company { font-family:'Inter',sans-serif; font-size:0.85rem; color:#7C8797; margin-top:0.05rem; }
.jc-meta { display:flex; gap:1rem; margin-top:0.5rem; font-family:'JetBrains Mono',monospace; font-size:0.74rem; color:#7C8797; flex-wrap: wrap; }
.jc-meta b { color:#C7CFDA; }

/* ---------- Buttons ---------- */
.stButton button, .stDownloadButton button, .stFormSubmitButton button {
  transition: transform 120ms ease-out, box-shadow 120ms ease-out;
}
.stButton button:hover, .stFormSubmitButton button:hover { transform: translateY(-1px); }

/* ---------- Scrollbar ---------- */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-thumb { background: #232B35; border-radius: 999px; }
::-webkit-scrollbar-track { background: transparent; }

@media (prefers-reduced-motion: reduce) {
  * { transition: none !important; }
}
</style>
"""


def render_kpi_row(kpis: list[dict]) -> str:
    """kpis: [{'label': str, 'value': str, 'delta': str|None, 'primary': bool}]"""
    tiles = ""
    for k in kpis:
        cls = "kpi-tile is-primary" if k.get("primary") else "kpi-tile"
        delta_html = (
            f'<div class="kpi-delta">{k["delta"]}</div>' if k.get("delta") else ""
        )
        tiles += f"""<div class="{cls}">
            <div class="kpi-label">{k['label']}</div>
            <div class="kpi-value">{k['value']}</div>
            {delta_html}
        </div>"""
    return f'<div class="kpi-row">{tiles}</div>'


# Maps your real db_handler status strings to a badge color-family and a
# shorter, human-facing label — the CHECK-constrained statuses stay exactly
# as-is in the database, this is presentation only.
STATUS_MAP = {
    "Applied": ("st-applied", "Applied"),
    "Interview": ("st-interview", "Interview"),
    "Pending": ("st-pending", "Pending"),
    "Manual Review": ("st-pending", "Needs Review"),
    "Rejected": ("st-rejected", "Rejected"),
    "Dead": ("st-rejected", "Dead"),
    "Not Interested": ("st-neutral", "Passed"),
    "Ghosted": ("st-neutral", "Ghosted"),
    "Needs Consultation": ("st-neutral", "Needs You"),
    "Failed - Retry": ("st-neutral", "Retrying"),
}


def status_badge(status: str) -> str:
    cls, label = STATUS_MAP.get(status, ("st-neutral", status))
    return f'<span class="status-badge {cls}"><span class="dot"></span>{label}</span>'


def job_card_header(
    role: str, company: str, status: str, score=None, source: str = None
) -> str:
    """The rich-text top of a job card — role, company, badge, meta line.
    Real interactive elements (buttons, expanders) render as normal Streamlit
    calls right after this, inside the same st.container(key="card_...")."""
    score_html = f"<b>{score:.0%}</b> match" if score is not None else "unscored"
    source_html = f"<span>via <b>{source}</b></span>" if source else ""
    return f"""
    <div class="jc-top">
        <div>
            <div class="jc-role">{role}</div>
            <div class="jc-company">{company}</div>
        </div>
        {status_badge(status)}
    </div>
    <div class="jc-meta"><span>{score_html}</span>{source_html}</div>
    """
