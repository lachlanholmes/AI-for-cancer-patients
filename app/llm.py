"""Thin LLM wrapper used by every model call in the tumor board.

Uses Google's OpenAI-compatible Gemini endpoint so the rest of the codebase keeps
using the familiar OpenAI SDK shape (tool calls, response_format, etc.) — only the
base URL and API key change. Set MEDBOARD_PROVIDER=openai to fall back to OpenAI.
"""
import json
import os
import re
import time
import logging
from typing import Any

from openai import OpenAI, APIConnectionError, RateLimitError, APIStatusError

from app.config import MODEL_NAME, PROVIDER

# Sentinel exception so callers (specialist.py / board.py) can render a clean
# user-facing message instead of dumping the raw OpenAI JSON.
class QuotaExceeded(Exception):
    """Raised when the LLM provider returns a rate-limit error we couldn't retry past."""

log = logging.getLogger(__name__)

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

_client: OpenAI | None = None


def get_client() -> OpenAI:
    """Return a singleton client for the provider resolved in config (explicit
    *_PROVIDER, else inferred from whichever API key is present)."""
    global _client
    if _client is not None:
        return _client

    provider = PROVIDER
    if provider == "openai":
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY is not set. Paste your key into .env and restart "
                "(or unset CANCERPATIENT_PROVIDER to use Gemini)."
            )
        _client = OpenAI(api_key=api_key)
        log.info("LLM client: OpenAI, model=%s", MODEL_NAME)
    else:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. Paste your Google AI Studio key into .env "
                "and restart (or set MEDBOARD_PROVIDER=openai to use OpenAI)."
            )
        _client = OpenAI(api_key=api_key, base_url=GEMINI_BASE_URL)
        log.info("LLM client: Gemini via OpenAI-compat endpoint, model=%s", MODEL_NAME)

    return _client


_RETRY_DELAY_RE = re.compile(r"['\"]retryDelay['\"]\s*:\s*['\"](\d+(?:\.\d+)?)s['\"]")


def _parse_retry_delay(err: Exception) -> float | None:
    """Try to extract Google's suggested retryDelay (seconds) from an error message."""
    m = _RETRY_DELAY_RE.search(str(err))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _quota_reason(err: Exception) -> str:
    """Classify a 429 from its message so the logs name the actual cause.

    All three funnel into the same RateLimitError, but the body distinguishes
    them — a spend cap is a project ceiling (not a lack of credit), a rate limit
    usually means the free tier, and otherwise it's a generic quota/billing issue.
    """
    m = str(err).lower()
    if "spend" in m:  # "monthly spending cap"
        return (
            "Gemini project spend cap reached — raise it at https://ai.studio/spend "
            "(this is a per-project cap, NOT your credit balance)."
        )
    if "rate" in m:
        return (
            "Gemini rate limit hit — confirm the key's project has billing enabled "
            "(the free tier is too tight for this app's fan-out)."
        )
    return (
        "Gemini quota exhausted — check billing/quota at "
        "https://console.cloud.google.com/billing."
    )


def chat(
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    model: str | None = None,
    max_retries: int = 5,
) -> Any:
    """Call the configured LLM with retry on transient errors.

    On rate-limit errors, prefer the provider's suggested retryDelay over
    blind exponential backoff (Google's Gemini API includes this in 429s).
    """
    client = get_client()
    kwargs: dict[str, Any] = {
        "model": model or MODEL_NAME,
        "messages": messages,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format

    # Reasoning ("thinking") budget. Defaults to MAX ("high"); override with
    # MEDBOARD_REASONING_EFFORT (none | low | medium | high, or "default" to omit).
    # Both Google's Gemini OpenAI-compat endpoint and OpenAI's reasoning models
    # accept reasoning_effort.
    #   - Gemini `-pro` models REQUIRE thinking ("none" → 'Budget 0 is invalid').
    #   - In thinking mode Gemini attaches a thought_signature to each tool call;
    #     specialist.py echoes it back (via the tool_call's extra_content) so the
    #     next turn doesn't 400 with 'Function call is missing a thought_signature'.
    effort = (
        os.getenv("CANCERPATIENT_REASONING_EFFORT")
        or os.getenv("MEDBOARD_REASONING_EFFORT")
        or "high"
    ).strip().lower()
    if effort and effort != "default":
        kwargs["reasoning_effort"] = effort

    attempt = 0
    while True:
        try:
            return client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            attempt += 1
            if attempt >= max_retries:
                # Raise a clean sentinel so callers can render a user-facing message
                # instead of the raw JSON blob. The message names the specific 429
                # cause (spend cap / rate limit / quota) so operator logs are actionable.
                raise QuotaExceeded(_quota_reason(e)) from e
            suggested = _parse_retry_delay(e)
            backoff = max(suggested or 0, min(2**attempt, 30))
            backoff = min(backoff, 60)   # cap at 60s
            log.warning(
                "LLM rate-limited (attempt %d/%d); waiting %.1fs%s",
                attempt, max_retries, backoff,
                f" (server suggested {suggested}s)" if suggested else "",
            )
            time.sleep(backoff)
        except APIConnectionError as e:
            attempt += 1
            if attempt >= max_retries:
                raise
            backoff = min(2**attempt, 16)
            log.warning("LLM connection error; retrying in %ds", backoff)
            time.sleep(backoff)
        except APIStatusError as e:
            if e.status_code in (500, 502, 503, 504) and attempt < max_retries:
                attempt += 1
                backoff = min(2**attempt, 16)
                log.warning("LLM %d error; retrying in %ds", e.status_code, backoff)
                time.sleep(backoff)
                continue
            raise


def chat_json(messages, *, model=None, max_retries=5) -> dict:
    """Like chat() but enforces JSON output and parses defensively.

    Tries response_format=json_object first; on parse failure, strips common
    markdown fencing and tries again. Raises ValueError if all attempts fail.
    """
    resp = chat(messages, response_format={"type": "json_object"}, model=model, max_retries=max_retries)
    raw = (resp.choices[0].message.content or "").strip()
    # Try direct parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # Strip markdown fences and try again
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"LLM returned unparseable JSON: {raw[:200]}...") from e
