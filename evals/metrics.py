"""Deterministic, free (no-LLM) quality signals for a board result.

These check the app's own contract from COMMON_PREFIX: claims should be
[N]-cited, prose should read at a low grade level, and every summary should
carry "talk to your team" safety framing. They are objective and cost nothing,
so they run on every eval output alongside the LLM judge.
"""
import re


_CITE_RE = re.compile(r"\[(\d{1,3})\]")
_SAFETY_RE = re.compile(
    r"oncology team|care team|your doctor|talk to your|ask your (?:team|doctor|oncolog)",
    re.IGNORECASE,
)


def distinct_citations(md: str) -> set[str]:
    return set(_CITE_RE.findall(md or ""))


def _count_syllables(word: str) -> int:
    word = re.sub(r"[^a-z]", "", word.lower())
    if not word:
        return 0
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups)
    if word.endswith("e") and n > 1:
        n -= 1
    return max(n, 1)


def flesch_kincaid_grade(text: str) -> float:
    """Approximate US grade level. Good enough for relative A/B comparison."""
    text = _CITE_RE.sub("", text or "")
    text = re.sub(r"[#*_`>\-|]", " ", text)
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sentences or not words:
        return 0.0
    syll = sum(_count_syllables(w) for w in words)
    W, S = len(words), len(sentences)
    return 0.39 * (W / S) + 11.8 * (syll / W) - 15.59


def has_safety_framing(md: str) -> bool:
    return bool(_SAFETY_RE.search(md or ""))


def compute(result: dict, statuses: dict) -> dict:
    """result = board.run_board(...) output; statuses = {specialist_id: status}."""
    md = result.get("english_markdown", "") or ""
    refs = result.get("references") or []
    vals = list(statuses.values())
    return {
        "citation_count": len(distinct_citations(md)),
        "reference_count": len(refs),
        "reading_grade": round(flesch_kincaid_grade(md), 1),
        "has_safety_framing": has_safety_framing(md),
        "word_count": len(md.split()),
        "section_count": md.count("\n## ") + (1 if md.startswith("## ") else 0),
        "abstentions": sum(1 for s in vals if s == "no_evidence"),
        "skipped": sum(1 for s in vals if s == "skipped"),
        "errors": sum(1 for s in vals if s == "error"),
    }
