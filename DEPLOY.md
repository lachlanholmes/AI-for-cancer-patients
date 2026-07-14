# Deploying to Railway

This app is a stateless FastAPI service — no database, no volumes, sessions live
in memory with a 30-minute TTL. That makes it cheap and simple to self-host. This
guide targets [Railway](https://railway.com), but any host that runs a Python ASGI
app works the same way.

## What it costs

| Item | Cost | Notes |
|------|------|-------|
| Railway Hobby plan | **$5/mo** | The subscription includes $5 of resource-usage credit. A small always-on service with no DB should stay within it. |
| Gemini API | **a few $/mo** | A **paid (billing-enabled) Google Cloud project is required** — the free tier is effectively 0 requests/min for this app's fan-out. The default `gemini-flash-lite-latest` is cheap. |
| Brave Search API | **Deferred** | Brave no longer has a free tier ($20 minimum prepayment). The app runs **without** it — see [Running without Brave](#running-without-brave). |
| PubMed / Europe PMC / Semantic Scholar | **$0** | No key required. |

At light personal volume the realistic total is **~$5/mo (Railway) + $0–5/mo (Gemini)**,
with Brave deferred until you decide you need richer web search. Public traffic is
what would blow past this — use the cost guards below.

## Prerequisites

1. A fork of this repo on your own GitHub account.
2. A **Gemini API key** — free at https://aistudio.google.com/apikey.
3. A Railway account on the **Hobby** plan.
4. *(Optional, deferred)* A **Brave Search API key** — https://brave.com/search/api/.

## Environment variables

Set these in Railway under your service's **Variables** tab. Only `GEMINI_API_KEY`
is strictly required.

| Variable | Required? | Value |
|----------|-----------|-------|
| `GEMINI_API_KEY` | **Yes** | Your Google AI Studio key, from a **billing-enabled** project. The provider auto-resolves to Gemini when this is set. |
| `CANCERPATIENT_ALLOWED_ORIGINS` | Recommended | Your Railway public URL, e.g. `https://your-app.up.railway.app`. Enables CORS; leave unset to disable cross-origin requests. |
| `CANCERPATIENT_MAX_ACTIVE_SESSIONS` | Recommended | Caps concurrent runs to bound spend. Default `20`; `10` is a safe starting point. |
| `CANCERPATIENT_REASONING_EFFORT` | Recommended | `none` \| `low` \| `medium` \| `high`. **Biggest cost/latency lever.** Defaults to `high`; set `low` to cut output tokens and speed things up. |
| `Brave_API` | Optional (deferred) | Brave Search key. Case matters. Omit to run without web search — see below. |
| `CANCERPATIENT_MODEL` | Optional | Override the model. Default `gemini-flash-lite-latest`. Use `gemini-3-flash-preview` for higher quality. (`gemini-2.5-flash` is retired; the `gemini-3.5-flash` tier has had sustained 503s.) |
| `CANCERPATIENT_PROVIDER` + `OPENAI_API_KEY` | Optional | Switch to OpenAI instead of Gemini. |
| `NCBI_EMAIL` + `NCBI_API_KEY` | Optional | Raises the PubMed rate limit. |

## Deploy steps

1. In Railway: **New Project → Deploy from GitHub repo**, and authorize the Railway
   GitHub app on your fork.
2. Select the fork. Railway reads `railpack.json` and builds automatically — no
   Dockerfile needed. The start command is already configured:
   `uvicorn app.server:app --host 0.0.0.0 --port $PORT`.
3. Add the environment variables above (at minimum `GEMINI_API_KEY`).
4. **Settings → Networking → Generate Domain** to get a public URL. Railway sets
   `$PORT` automatically.
5. Copy that URL into `CANCERPATIENT_ALLOWED_ORIGINS` and redeploy.

## Keep costs down

- **Set a spend limit.** Railway → project → **Usage limits**. This is your hard
  ceiling if traffic spikes.
- **Enable App Sleep** (serverless) so the service idles when no one is using it,
  instead of billing compute 24/7.
- **`CANCERPATIENT_MAX_ACTIVE_SESSIONS`** bounds how many queries can run at once.
  Each query fans out to ~6 specialists, so this directly caps API spend.
- **`CANCERPATIENT_REASONING_EFFORT=low`** roughly cuts the expensive output-token
  cost and latency of every model call.

### A note on Gemini: paid tier is required

A single patient query launches up to 6 specialists in parallel, each making
several tool-calling LLM calls, plus a synthesizer and translator — commonly ~30
Gemini calls per query. The **free tier cannot serve this** (in practice 429/quota
errors on even a single call for new projects). Enable billing on the key's Google
Cloud project (Tier 1). `gemini-flash-lite-latest` is inexpensive — a full query with
`CANCERPATIENT_REASONING_EFFORT=low` is roughly a few cents. Set a **budget alert**
in Google Cloud as a spend guard.

Latency note: a full query takes ~3–5 minutes (parallel specialists each doing
multiple search + LLM rounds). That is expected, not a hang.

## Running without Brave

Brave Search is **optional**. With `Brave_API` unset, the app still runs and the
web-search tools (`web_search`, `patient_source_search`, `social_resource_search`,
`patient_stories_search`) return a "no backend configured" message instead of
crashing. The agents are instructed to fall back to keyless sources.

What still works fully:

- **Physiotherapist, Dietician, Speech & Swallowing, Emotional Wellbeing** — these
  draw on PubMed, Europe PMC, and Semantic Scholar (no key needed).

What degrades until you add a Brave key:

- **Patient Navigator** and **Stories from Others** rely most on curated web search,
  so their answers will be thin or abstain.

### Adding Brave later

When you're ready to pay for Brave, just add the `Brave_API` variable in Railway and
redeploy — no code change needed.

## Local development

```bash
cp .env.example .env
# Edit .env: set at least GEMINI_API_KEY (Brave_API optional)
./run.sh
```

Then open http://localhost:8000. `run.sh` creates a virtualenv and installs
dependencies on first run.
