# Eval harness — cost vs quality A/B testing

Run the same set of patient cases through the board under two different configs,
then compare **cost** (real token usage → estimated $) against **quality**
(deterministic metrics + an LLM judge + your own blinded side-by-side review).

This is for picking the cheapest config that still honors the app's contract
(cited claims, safe scope, plain language). It is **not** run in production.

## Setup

```bash
# from the repo root, with the app's venv active
pip install -r evals/requirements.txt
```

Uses the same `GEMINI_API_KEY` / `Brave_API` from your `.env`.

## 1. Run each config

Config comes from environment variables, so run this once per config you want to
compare. Each run writes `evals/results/<label>.json`.

```bash
# Baseline
CANCERPATIENT_MODEL=gemini-3.5-flash CANCERPATIENT_REASONING_EFFORT=high \
    python -m evals.run_eval --label flash-high

# Cheaper candidate
CANCERPATIENT_MODEL=gemini-flash-lite-latest CANCERPATIENT_REASONING_EFFORT=low \
    python -m evals.run_eval --label lite-low
```

Tunable axes (all env vars): `CANCERPATIENT_MODEL`, `CANCERPATIENT_REASONING_EFFORT`,
`CANCERPATIENT_MAX_TOOL_ITERATIONS`, `CANCERPATIENT_DISABLED_SPECIALISTS`
(e.g. `stories,slp`).

Flags: `--runs N` (repeats per case, default 2, averaged to smooth web-search
noise), `--limit N` (first N cases only, for a quick smoke test).

> ⚠️ Each run executes the full pipeline (~30 LLM + ~20 search calls per case),
> so a 6-case × 2-run pass costs real API money. Use `--limit 1 --runs 1` to smoke-test.

## 2. Compare

```bash
python -m evals.compare evals/results/flash-high.json evals/results/lite-low.json
# → writes evals/report.html
```

Open `evals/report.html` in your browser. You get:
- A **metrics table**: est. $/query, tokens, citations, reading grade, safety-framing
  rate, abstentions, latency — per config.
- **Blinded side-by-side**: each case shows Output 1 vs Output 2 (order randomized,
  labels hidden). Pick a winner, then **Reveal** to see which config it was, the
  per-output metrics, and what the LLM judge decided.
- A running tally of **your picks vs the judge's**, so you can see how often you
  agree — i.e. whether you trust the automated judge.

Add `--no-judge` to skip the LLM judge (free; still builds the side-by-side).

## Knobs

- **Judge model**: `CANCERPATIENT_JUDGE_MODEL` (default `gemini-3-pro-preview`).
  Held constant across the A/B so the judge isn't the variable.
- **Prices**: `evals/prices.json` — approximate USD/1M tokens. **Verify against
  current Gemini pricing** before trusting the `$` estimates.

## Files

| File | Purpose |
|------|---------|
| `cases.jsonl` | Seed patient cases (edit/add your own) |
| `run_eval.py` | Runs cases under the current env config → results JSON |
| `metrics.py` | Deterministic, no-LLM quality signals |
| `judge.py` | LLM-as-judge (rubric + blinded pairwise) |
| `compare.py` | Builds the HTML A/B report |
| `prices.json` | Token prices for the $ estimate |
