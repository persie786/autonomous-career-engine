import json
from utils.ai_router import generate_json

KEYWORD_EXTRACTION_PROMPT = """Extract the 15-25 most important technical skills, tools, \
qualifications, and role-specific keywords from this job description — the terms an \
Applicant Tracking System would scan for. Skip generic filler ("team player", "fast-paced").

Respond with ONLY valid JSON: {{"keywords": ["keyword1", "keyword2", ...]}}

Job description:
{description}"""


def extract_keywords(job_description: str) -> list[str]:
    raw_text, _ = generate_json(
        "",
        KEYWORD_EXTRACTION_PROMPT.format(description=job_description[:3000]),
        temperature=0.1,
    )
    return json.loads(raw_text).get("keywords", [])


def _text_contains(haystack: str, keywords: list[str]) -> tuple[list, list]:
    haystack_lower = haystack.lower()
    matched = [kw for kw in keywords if kw.lower() in haystack_lower]
    missing = [kw for kw in keywords if kw.lower() not in haystack_lower]
    return matched, missing


def score_persona_against_keywords(keywords: list[str], persona: dict) -> dict:
    """The 'before' score — checks the static persona, before generation."""
    haystack = " ".join(
        [
            persona.get("summary", ""),
            " ".join(persona.get("skills", [])),
            " ".join(
                b
                for exp in persona.get("experience", [])
                for b in exp.get("bullets", [])
            ),
        ]
    )
    matched, missing = _text_contains(haystack, keywords)
    return {"total": len(keywords), "matched": len(matched), "missing": missing}


def score_text_against_keywords(keywords: list[str], text: str) -> dict:
    """The 'after' score — checks whatever text was actually generated. This
    is the one that gets saved, since it reflects what you're really submitting."""
    matched, missing = _text_contains(text, keywords)
    return {"total": len(keywords), "matched": len(matched), "missing": missing}
