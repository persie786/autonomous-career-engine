import os
from openai import OpenAI, RateLimitError as GroqRateLimitError
from google import genai
from google.genai import types
from google.genai.errors import ClientError as GeminiClientError

from utils.logger import setup_logger

logger = setup_logger("ai_router")

_groq_client = None
_gemini_client = None


def _get_groq_client() -> OpenAI:
    global _groq_client
    if _groq_client is None:
        _groq_client = OpenAI(
            api_key=os.getenv("GROQ_API_KEY"), base_url="https://api.groq.com/openai/v1"
        )
    return _groq_client


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        _gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
    return _gemini_client


# Ordered by quality first, then by how independent each entry's quota is
# from the one before it — two different Groq models before falling all
# the way to a different provider.
FALLBACK_CHAIN = [
    {"provider": "groq", "model": "llama-3.3-70b-versatile"},
    {"provider": "groq", "model": "llama-3.1-8b-instant"},
    {"provider": "gemini", "model": "gemini-3.5-flash"},
]


def _is_rate_limit_error(exc: Exception) -> bool:
    if isinstance(exc, GroqRateLimitError):
        return True
    if isinstance(exc, GeminiClientError) and getattr(exc, "code", None) == 429:
        return True
    # Fallback check in case either SDK's exception shape changes — the
    # 429 / RESOURCE_EXHAUSTED text is stable even across SDK versions.
    text = str(exc).upper()
    return "429" in text or "RESOURCE_EXHAUSTED" in text


def _call_groq(
    model: str, system_prompt: str, user_prompt: str, temperature: float
) -> str:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": user_prompt})

    response = _get_groq_client().chat.completions.create(
        model=model,
        messages=messages,
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    return response.choices[0].message.content


def _call_gemini(
    model: str, system_prompt: str, user_prompt: str, temperature: float
) -> str:
    contents = f"{system_prompt}\n\n{user_prompt}" if system_prompt else user_prompt
    response = _get_gemini_client().models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json", temperature=temperature
        ),
    )
    return response.text


def generate_json(
    system_prompt: str, user_prompt: str, temperature: float = 0.2
) -> tuple[str, str]:
    """
    Tries each backend in FALLBACK_CHAIN in order until one succeeds.
    Returns (raw_json_text, model_used_label) — callers pass model_used_label
    into their own logging so it's visible which model actually served a
    given evaluation or generation.

    Only rate-limit errors advance to the next backend. Any other exception
    (malformed JSON, network failure, bad prompt) propagates immediately —
    that's a real problem worth seeing, not something to mask by silently
    switching models.
    """
    last_error = None

    for backend in FALLBACK_CHAIN:
        label = f"{backend['provider']}:{backend['model']}"
        try:
            if backend["provider"] == "groq":
                text = _call_groq(
                    backend["model"], system_prompt, user_prompt, temperature
                )
            else:
                text = _call_gemini(
                    backend["model"], system_prompt, user_prompt, temperature
                )
            return text, label

        except Exception as e:
            if _is_rate_limit_error(e):
                logger.warning(
                    f"{label} rate-limited — falling back to next model in the chain."
                )
                last_error = e
                continue
            raise

    raise RuntimeError(
        f"All models in the fallback chain are rate-limited. Last error: {last_error}"
    )
