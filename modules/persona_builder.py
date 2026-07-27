import json
import os
from pypdf import PdfReader
from google import genai
from google.genai import types
from utils.ai_router import generate_json
from utils.logger import setup_logger
from dotenv import load_dotenv
from io import BytesIO
from utils.storage import read_binary, binary_exists

load_dotenv()

logger = setup_logger("persona_builder")

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESUME_PATH = os.path.join(PROJECT_ROOT, "data", "base_resume.pdf")
PERSONAS_PATH = os.path.join(PROJECT_ROOT, "data", "personas.json")


EXTRACTION_PROMPT = """Extract this candidate's resume into structured JSON with exactly these fields:
{
  "full_name": string,
  "email": string,
  "phone": string,
  "summary": string (a 2-3 sentence professional summary),
  "skills": array of strings,
  "experience": array of objects with "title", "company", "dates", and "bullets" (array of strings),
  "education": array of objects with "degree", "institution", and "dates"
}

Use only information actually present in the resume text below. Leave a field empty
(empty string or empty array) if it genuinely isn't present — never invent details.

Resume text:
"""


def extract_resume_text(pdf_path: str = RESUME_PATH) -> str:
    content = read_binary(pdf_path, "base_resume")
    if content is None:
        raise FileNotFoundError("No base resume found.")
    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages).strip()


def build_persona(name: str = "default") -> dict:
    """
    Extracts structured resume data via Gemini and saves it into personas.json
    under the given name, overwriting any existing persona with that name.
    """
    if not binary_exists(RESUME_PATH, "base_resume"):
        raise FileNotFoundError(
            "No base resume found — upload one in the Settings tab first."
        )

    resume_text = extract_resume_text()
    if not resume_text:
        raise ValueError(
            "Resume PDF extracted to empty text — it may be a scanned image rather "
            "than actual selectable text. pypdf can't OCR scanned PDFs."
        )

    raw_text, model_used = generate_json(
        "", EXTRACTION_PROMPT + resume_text, temperature=0.1
    )
    persona = json.loads(raw_text)

    personas = load_personas()
    personas[name] = persona
    save_personas(personas)

    logger.info(
        f"Persona '{name}' built from resume ({len(resume_text)} chars extracted) via {model_used}."
    )
    return persona


def load_personas() -> dict:
    if not os.path.exists(PERSONAS_PATH):
        return {}
    with open(PERSONAS_PATH, "r") as f:
        return json.load(f)


def save_personas(personas: dict):
    os.makedirs(os.path.dirname(PERSONAS_PATH), exist_ok=True)
    with open(PERSONAS_PATH, "w") as f:
        json.dump(personas, f, indent=2)


def get_persona(name: str = "default") -> dict | None:
    return load_personas().get(name)


if __name__ == "__main__":
    persona = build_persona()
    print(json.dumps(persona, indent=2))


def delete_persona(name: str):
    personas = load_personas()
    personas.pop(name, None)
    save_personas(personas)


VARIANT_PROMPT = """You are creating a variant of a candidate's persona for a different \
job-search angle. Below is their existing, verified persona, followed by an instruction \
for how to re-emphasize or reframe it.

CRITICAL: only reorganize, re-emphasize, or rephrase what's already true in the base \
persona. Never invent new skills, employers, titles, or accomplishments not present in it.

Base persona (JSON):
{base_json}

Instruction: {instruction}

Return ONLY valid JSON in the exact same shape as the base persona above."""


def create_persona_variant(base_name: str, new_name: str, instruction: str) -> dict:
    base_persona = get_persona(base_name)
    if base_persona is None:
        raise ValueError(f"Base persona '{base_name}' not found.")

    raw_text, model_used = generate_json(
        "",
        VARIANT_PROMPT.format(
            base_json=json.dumps(base_persona, indent=2), instruction=instruction
        ),
        temperature=0.3,
    )
    variant = json.loads(raw_text)
    personas = load_personas()
    personas[new_name] = variant
    save_personas(personas)
    logger.info(
        f"Persona variant '{new_name}' created from '{base_name}' via {model_used}."
    )
    return variant


SUMMARY_POLISH_PROMPT = """Write or improve a first-person professional summary (2-4 \
sentences) for a job seeker's profile. Keep it honest — only use background actually \
given below; never invent employers, titles, or skills.

Current draft (may be empty): {current}
Known background (may be empty): {background}

Return ONLY valid JSON: {{"summary": "..."}}"""


def polish_career_summary(current_text: str, background: str = "") -> str:
    raw_text, _ = generate_json(
        "",
        SUMMARY_POLISH_PROMPT.format(
            current=current_text or "(empty)",
            background=background or "(none provided)",
        ),
        temperature=0.4,
    )
    return json.loads(raw_text).get("summary", current_text)
