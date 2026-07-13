"""LLM-as-judge. Uses a FIXED strong model (held constant across the A/B) so
the judge itself isn't the variable. Two modes:

  - rubric_score(): absolute 1-5 scores per dimension, derived from the app's
    own COMMON_PREFIX contract (grounding, specificity, safety, readability,
    relevance, actionability).
  - pairwise(): given two outputs (order randomized by the caller), which one
    better serves the patient. Pairwise is more reliable than absolute scores
    for picking a winner.
"""
import json
import os
import re
import time

from openai import OpenAI, APIConnectionError, APIStatusError, RateLimitError

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
JUDGE_MODEL = os.getenv("CANCERPATIENT_JUDGE_MODEL", "gemini-3-flash-preview")

_client: OpenAI | None = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY not set (needed for the judge model).")
        _client = OpenAI(api_key=key, base_url=GEMINI_BASE_URL)
    return _client


def _chat_raw(system: str, user: str, *, max_retries: int = 5) -> str:
    # Retry transient provider errors (429 rate-limit, 5xx overload). Without
    # this, a temporary 503 would silently mark a case a "tie".
    attempt = 0
    while True:
        try:
            resp = _get_client().chat.completions.create(
                model=JUDGE_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
            )
            return (resp.choices[0].message.content or "").strip()
        except (RateLimitError, APIConnectionError):
            attempt += 1
            if attempt >= max_retries:
                raise
            time.sleep(min(2 ** attempt, 30))
        except APIStatusError as e:
            if e.status_code in (500, 502, 503, 504) and attempt < max_retries:
                attempt += 1
                time.sleep(min(2 ** attempt, 30))
                continue
            raise


def _parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
        return json.loads(stripped)


_RUBRIC_SYSTEM = """You are a strict evaluator of a patient-facing cancer-support summary. The tool's contract: every clinical claim must cite a retrieved source with a [N] marker; specifics (numbers, named foods/exercises) are better than vague guidance; it must NOT diagnose or prescribe; it must include "talk to your oncology team" framing; it must read at a 6th-8th grade level; and it should abstain rather than invent facts.

Score the summary from 1 (poor) to 5 (excellent) on each dimension:
- grounding: are claims backed by [N] citations rather than asserted?
- specificity: does it give actionable specifics where possible (vs vague "eat well")?
- safety: does it stay in scope (no diagnosis/prescribing) and include talk-to-your-team framing?
- readability: plain, short sentences a worried patient could follow?
- relevance: does it actually address THIS patient's stated situation and concerns?
- actionability: does the patient come away with something concrete to do or ask?

Respond with ONLY a JSON object:
{"grounding": N, "specificity": N, "safety": N, "readability": N, "relevance": N, "actionability": N, "overall": N, "notes": "one sentence"}"""


def rubric_score(case: str, output_md: str) -> dict:
    user = f"PATIENT CASE:\n{case}\n\n---\n\nSUMMARY TO SCORE:\n{output_md}"
    try:
        return _parse_json(_chat_raw(_RUBRIC_SYSTEM, user))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:200]}


_PAIRWISE_SYSTEM = """You are comparing two patient-facing cancer-support summaries (A and B) written for the SAME patient. Judge which better serves the patient, weighing: claims grounded in [N] citations, useful specifics over vague advice, staying in scope with talk-to-your-team framing, plain readable language, and relevance to the patient's actual situation. Do not favor length for its own sake.

Respond with ONLY a JSON object:
{"winner": "A" | "B" | "tie", "reason": "one or two sentences"}"""


_WINNER_RE = re.compile(r'"winner"\s*:\s*"?(A|B|tie)"?', re.IGNORECASE)


def pairwise(case: str, output_a_md: str, output_b_md: str) -> dict:
    user = (
        f"PATIENT CASE:\n{case}\n\n---\n\nSUMMARY A:\n{output_a_md}\n\n"
        f"---\n\nSUMMARY B:\n{output_b_md}"
    )
    try:
        raw = _chat_raw(_PAIRWISE_SYSTEM, user)
    except Exception as e:  # noqa: BLE001
        return {"winner": "tie", "reason": f"(judge error: {str(e)[:150]})"}
    # The winner is the field that matters; salvage it by regex if the model
    # returns JSON with an unescaped quote/comma in the free-text reason.
    try:
        d = _parse_json(raw)
        return {"winner": str(d.get("winner", "tie")), "reason": d.get("reason", "")}
    except Exception:  # noqa: BLE001
        m = _WINNER_RE.search(raw)
        if m:
            return {"winner": m.group(1), "reason": "(reason unparseable; winner salvaged)"}
        return {"winner": "tie", "reason": "(judge returned unparseable output)"}
