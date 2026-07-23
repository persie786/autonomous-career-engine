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


def score_job_against_persona(job_description: str, persona: dict) -> dict:
    """
    Keyword extraction is the one AI call; whether each keyword literally
    appears in the persona is a plain substring check after that — kept
    deterministic on purpose, so the score is explainable (you can see
    exactly which words were checked) rather than another opaque judgment.
    """
    keywords = extract_keywords(job_description)
    if not keywords:
        return {"total": 0, "matched": 0, "missing": []}

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
    ).lower()

    matched = [kw for kw in keywords if kw.lower() in haystack]
    missing = [kw for kw in keywords if kw.lower() not in haystack]
    return {"total": len(keywords), "matched": len(matched), "missing": missing}
