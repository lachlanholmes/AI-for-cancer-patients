# Cancer Support — patient-facing AI helper

A plain-language cancer-support tool for patients and caregivers. Five AI helpers
research trusted public medical sources in parallel and put together a summary you
can bring to your next appointment, translated into your language of choice.

**This is not medical advice.** Always talk to your oncology team before making any decisions.

## The helpers

1. **Physiotherapist** — movement, exercise, fatigue, lymphedema, neuropathy
2. **Dietician** — eating during treatment, side-effect management via food, supplement safety framing
3. **Speech & Swallowing (SLP)** — *conditional*: only for head/neck, esophageal, brain, laryngectomy cases
4. **Emotional Wellbeing** — coping, anxiety, sleep, fear of recurrence, crisis routing
5. **Patient Navigator** — financial assistance, transportation, work rights, insurance, lodging — country-aware
6. **Translator** — final pass into the patient's chosen language (preserves medical terms in English)

## Safety design

- **Hard scope-of-practice rules** in `COMMON_PREFIX`: no diagnosing, no prescribing, no dose recommendations.
- **Evidence-only rule**: every clinical claim must cite a retrieved source `[N]`. Drafts with zero citations are forced to abstain.
- **Crisis handling**: the Emotional Wellbeing helper leads with crisis-line numbers if self-harm language is detected.
- **Country matching**: the Patient Navigator extracts the patient's country from their free-text location before recommending region-specific programs.
- **Translator preserves English medical terms in parentheses** so a patient can match what they read to their hospital chart.

## Architecture

Forked from the [AI Tumor Board](../AI%20tumor%20Board/) architecture (FastAPI + SSE + asyncio + Gemini/OpenAI). Reused ~80% of the scaffolding; replaced specialists, prompts, and the round structure for patient-facing scope.

Key differences from the Tumor Board:
- **Single round** of parallel specialists (no judge, no multi-round consensus).
- **Translator is a post-synthesis function**, not a "specialist" — it runs once after the synthesizer.
- **SLP is pre-filtered** by a regex on the case text + a second LLM-side SKIP marker.
- **Patient Navigator has a soft citation gate** — its value is naming real programs, not citing PubMed.
- **`patient_source_search`** is a new tool that wraps Brave with a `site:` allowlist of trusted patient-facing oncology sites.
- **`social_resource_search`** dispatches to country-specific Tier-4 directory lists.

## Run it

```bash
cp .env.example .env
# Edit .env: set GEMINI_API_KEY and Brave_API
./run.sh
```

Then open http://localhost:8000.

### Required keys

- `GEMINI_API_KEY` — get free at https://aistudio.google.com/apikey
- `Brave_API` — get free at https://brave.com/search/api/ (2k queries/mo)

### Optional keys

- `NCBI_EMAIL` + `NCBI_API_KEY` — raises PubMed rate limit
- `CANCERPATIENT_MODEL` — override default model
- `CANCERPATIENT_PROVIDER=openai` + `OPENAI_API_KEY` — switch to GPT

## Deploy

`railpack.json` and `Procfile` are configured for Railway. Same shape as the Tumor Board.

## File map

```
app/
  server.py        - FastAPI app, SSE, request validation
  board.py         - orchestrator: parallel specialists → synthesizer → translator
  specialist.py    - per-agent tool-loop + citation gate (soft for navigator)
  config.py        - 6 SPECIALIST_CONFIGS + trusted source allowlists
  prompts.py       - COMMON_PREFIX + 5 specialist prompts + synthesizer + translator
  evidence.py      - in-memory ledger, [N] labels, dedup by (kind, id)
  llm.py           - Gemini/OpenAI client wrapper with retry
  sessions.py      - in-memory session registry with TTL cleanup
  language.py      - LLM-backed location & language helpers
  tools/
    pubmed.py, europe_pmc.py, semantic_scholar.py     - reused as-is
    brave_search.py                                    - reused as-is
    patient_source_search.py                           - NEW: Brave + site: allowlist
    social_resource_search.py                          - NEW: country-keyed directories

static/
  index.html, app.js, styles.css   - single-page UI
  about.html, privacy.html
```

## License

Licensed under the [Apache License 2.0](LICENSE). Free to use, study, modify, and
build on — including commercially — provided you keep the license and attribution.

Source code: https://github.com/Roupen92/AI-for-cancer-patients
