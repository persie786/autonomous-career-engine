# Autonomous Career Engine

A human-in-the-loop AI job application pipeline — sources listings, filters and scores them, drafts tailored application materials, and prepares (but never submits) applications for review. Built entirely on free-tier tools: Groq, Gemini, and local SQLite, with no paid infrastructure anywhere in the stack.

## What it does

1. **Sources** job listings via JobSpy, filtered by user-defined red flags and deduplicated against everything already seen.
2. **Evaluates** each listing with an LLM (Groq, with automatic fallback across models and providers on rate limits) for GO/NO-GO fit and a match score, routed by a configurable confidence threshold into auto-approved or manual review.
3. **Generates** a tailored CV and cover letter per job from a structured persona extracted from a base resume, injected into a Word template.
4. **Stages** the application in a real browser window — autofilling whatever fields it can confidently identify — for the user to review and submit personally.
5. **Tracks** outcomes automatically: a ghosting webhook flags stale applications, and an inbox monitor reads incoming email to detect rejections and interview invites.

## Stack

Python · Streamlit · SQLite · Playwright · Groq (Llama 3.3/3.1) · Google Gemini · `cryptography` (Fernet) · pytest

## Architecture
app.py # Streamlit UI — five tabs, one per pipeline stage
database/db_handler.py # Schema, CRUD, encryption, ghosting webhook
modules/
scraper.py # Sourcing + red-flag filtering + dedup
ai_evaluator.py # GO/NO-GO scoring + threshold routing
persona_builder.py # Resume -> structured persona (Gemini)
cv_generator.py # Tailored CV/cover letter + docx injection
browser_agent.py # Human-reviewed application staging
email_monitor.py # Inbox scanning + outcome classification
utils/
ai_router.py # Cross-model/provider fallback on rate limits
settings.py, field_memory.py, logger.py
tests/ # pytest suite for db_handler and ai_evaluator

## Setup

1. `python -m venv venv` → activate it → `pip install -r requirements.txt`
2. Copy `.env.example` to `.env` and fill in `GROQ_API_KEY`, `GEMINI_API_KEY`, `IMAP_EMAIL`/`IMAP_PASS` (Gmail app password), and a generated Fernet `ENCRYPTION_KEY`.
3. `streamlit run app.py`

## Testing
pytest -v

Runs entirely against a disposable temp database and mocked AI responses — no real data, quota, or credentials touched.

## Notable design decisions

- **Nothing submits itself.** The browser agent autofills recognized fields in a real, visible window and stops — the person reviews and clicks Submit. This was a deliberate pivot away from an earlier design that used stealth browser automation to evade platform bot-detection; that approach risks account bans and misrepresents what actually happened to an employer's ATS. What exists instead keeps almost all the time savings without the risk.
- **Company Consistency Guardrail.** Once a persona's been used for a company, every future application to that company reuses it — so a candidate's self-presentation stays coherent across repeat applications, rather than looking inconsistent to a recruiter comparing them.
- **Cross-model fallback.** AI calls fall back across two Groq models and then Gemini on rate limits specifically (not on other errors, which surface immediately as real bugs) — a long sourcing batch degrades gracefully instead of dying mid-run.
- **SQLite in WAL mode**, since the browser agent, email monitor, and UI can all touch the database from separate processes concurrently.