"""Configuration for the patient-facing cancer support team.

Mirrors the shape of the AI Tumor Board's config.py but with patient-care agents,
single-round orchestration, and a translator that runs as a post-synthesis pass
(not a 'specialist' in the parallel gather).
"""
import os

from app import prompts


# Accept either prefix so an .env copied from the Tumor Board (MEDBOARD_*) works
# unchanged. CANCERPATIENT_* takes precedence when both are set.


def _resolve_provider() -> str:
    """Pick the LLM provider.

    An explicit CANCERPATIENT_PROVIDER / MEDBOARD_PROVIDER always wins. When none
    is set, infer it from whichever API key is actually present so a deployment
    that only supplies one key (e.g. OPENAI_API_KEY on Railway) just works without
    a separate provider flag. Gemini stays the documented default when its key is
    present (free tier), with OpenAI as the fallback.
    """
    explicit = (
        os.getenv("CANCERPATIENT_PROVIDER")
        or os.getenv("MEDBOARD_PROVIDER")
        or ""
    ).strip().lower()
    if explicit:
        return explicit
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return "gemini"  # nothing set: keep the documented default; get_client() raises a clear error


PROVIDER = _resolve_provider()

# The default model has to match the resolved provider — a Gemini model name sent
# to OpenAI (or vice versa) fails. An explicit *_MODEL override always wins.
# NOTE: gemini-2.5-flash is retired for new Google Cloud projects (returns 404
# "no longer available to new users"); gemini-3.5-flash is the current GA flash.
_DEFAULT_MODEL = "gpt-5.1" if PROVIDER == "openai" else "gemini-3.5-flash"
MODEL_NAME = (
    os.getenv("CANCERPATIENT_MODEL")
    or os.getenv("MEDBOARD_MODEL")
    or _DEFAULT_MODEL
)

# Single round of parallel specialists; no judge, no consensus loop.
SINGLE_ROUND = True
PARALLEL_SPECIALISTS = 5
# Tool-call budget per specialist. Lower = cheaper/faster, fewer search rounds.
# Env-tunable so cost/quality can be A/B-tested (see evals/).
MAX_TOOL_ITERATIONS = int(os.getenv("CANCERPATIENT_MAX_TOOL_ITERATIONS", "5"))


# Tools available to every research agent. The translator gets none.
PATIENT_BASE_TOOLS: set[str] = {
    "pubmed_search_and_fetch",
    "pubmed_search",
    "pubmed_fetch",
    "europe_pmc_search",
    "semantic_scholar_search",
    "patient_source_search",
    "web_search",
}


# Trusted patient-facing source allowlists, used by `patient_source_search`.
# These are passed by the agent's system prompt; the tool restricts the Brave
# query with `site:` filters built from the agent's list.

_AUTHORITATIVE_PATIENT = [
    "cancer.net",            # ASCO patient site
    "cancer.gov",            # NCI
    "cancer.org",            # American Cancer Society
    "macmillan.org.uk",
    "cancerresearchuk.org",
    "esmo.org",
    "lls.org",
    "komen.org",
    "cancercare.org",
]

_ACADEMIC_PATIENT_PAGES = [
    "mskcc.org",
    "mayoclinic.org",
    "dana-farber.org",
    "mdanderson.org",
    "hopkinsmedicine.org",
    "clevelandclinic.org",
]

# Patient-story podcast allowlist: iTunes collectionId → show display name.
# IDs were looked up once via the iTunes Search API; they are stable for the
# life of the podcast. Match on numeric ID, not name (names drift).
_PATIENT_STORY_PODCASTS: dict[str, int] = {
    "Cancer.Net Podcast (ASCO)":                          1334855695,
    "The Stupid Cancer Show":                             1622221056,
    "Cancer Straight Talk (MSKCC)":                       1531529982,
    "Unraveled (Dana-Farber)":                            1575993703,
    "CancerCare Connect Education Workshops":              451511608,
    "Digital Diaries: Cancer Patient Stories (Macmillan)":1616856984,
}

# Written-story site allowlist for Brave site:-filtered search.
_PATIENT_STORY_DOMAINS: list[str] = [
    "healthtalk.org",            # UK research-grade narrative archive (gold standard)
    "patientstory.com",
    "cancer.net",                # Voices section
    "cancer.org",                # ACS Voices
    "macmillan.org.uk",          # Stories section
    "cancersupportcommunity.org",
    "stupidcancer.org",
    "bagitcancer.org",
    "imermanangels.org",
    "lbbc.org",                  # Living Beyond Breast Cancer
    "youngsurvival.org",         # young-adult breast
]


SPECIALIST_CONFIGS: dict[str, dict] = {
    "physio": {
        "display_name": "Physiotherapist",
        "color": "#4A7C6F",     # sage
        "system_prompt": prompts.PHYSIO,
        "allowed_tools": PATIENT_BASE_TOOLS,
        "pubmed_bias": {
            "mesh": [
                "Exercise Therapy",
                "Rehabilitation",
                "Cancer Survivors",
                "Lymphedema",
                "Peripheral Nervous System Diseases",
                "Fatigue",
            ]
        },
        "trusted_sources": (
            _AUTHORITATIVE_PATIENT
            + _ACADEMIC_PATIENT_PAGES
            + [
                "apta.org",
                "oncologypt.org",
                "csoppt.com",
                "lymphaticnetwork.org",
                "lymphnet.org",
                "lymphoedema.org",
            ]
        ),
        "citation_required": True,
        "conditional": False,
    },
    "dietician": {
        "display_name": "Dietician",
        "color": "#8E9F4A",     # warm olive
        "system_prompt": prompts.DIETICIAN,
        "allowed_tools": PATIENT_BASE_TOOLS,
        "pubmed_bias": {
            "mesh": [
                "Nutrition Therapy",
                "Diet Therapy",
                "Cancer Survivors",
                "Mucositis",
                "Antineoplastic Combined Chemotherapy Protocols",
            ]
        },
        "trusted_sources": (
            _AUTHORITATIVE_PATIENT
            + _ACADEMIC_PATIENT_PAGES
            + [
                "aicr.org",
                "eatright.org",
                "oncologynutrition.org",
                "espen.org",
                "bda.uk.com",
            ]
        ),
        "citation_required": True,
        "conditional": False,
    },
    "slp": {
        "display_name": "Speech & Swallowing",
        "color": "#5A8FA8",     # muted teal
        "system_prompt": prompts.SLP,
        "allowed_tools": PATIENT_BASE_TOOLS,
        "pubmed_bias": {
            "mesh": [
                "Speech Therapy",
                "Deglutition Disorders",
                "Laryngectomy",
                "Head and Neck Neoplasms",
            ]
        },
        "trusted_sources": (
            _AUTHORITATIVE_PATIENT
            + [
                "asha.org",
                "rcslt.org",
                "dysphagiaresearch.org",
                "iaoo.org",
                "webwhispers.org",
                "mdanderson.org",
                "mskcc.org",
                "headandneck.org",
            ]
        ),
        "citation_required": True,
        "conditional": True,
    },
    "mental": {
        "display_name": "Emotional Wellbeing",
        "color": "#7A6BAA",     # muted purple
        "system_prompt": prompts.MENTAL_HEALTH,
        "allowed_tools": PATIENT_BASE_TOOLS,
        "pubmed_bias": {
            "mesh": [
                "Psycho-Oncology",
                "Adjustment Disorders",
                "Anxiety",
                "Depression",
                "Sleep Initiation and Maintenance Disorders",
            ]
        },
        "trusted_sources": (
            _AUTHORITATIVE_PATIENT
            + [
                "apos-society.org",
                "ipos-society.org",
                "nccn.org",
                "cancersupportcommunity.org",
                "nami.org",
                "988lifeline.org",
                "samaritans.org",
                "findahelpline.com",
                "crisistextline.org",
                "mskcc.org",
                "dana-farber.org",
            ]
        ),
        "citation_required": True,
        "conditional": False,
    },
    "stories": {
        "display_name": "Stories from Others",
        "color": "#B05E6E",     # warm rose
        "system_prompt": prompts.STORIES,
        "allowed_tools": {"patient_stories_search", "patient_source_search"},
        "pubmed_bias": None,
        "trusted_sources": _PATIENT_STORY_DOMAINS,
        "podcast_allowlist": _PATIENT_STORY_PODCASTS,
        # Story citations are URLs to lived experience, not PubMed papers — the
        # citation gate is soft (the carve-out is also in the SYNTHESIZER prompt).
        "citation_required": True,
        "soft_citation_gate": True,
        "conditional": False,
    },
    "navigator": {
        "display_name": "Patient Navigator",
        "color": "#C97B3F",     # warm amber
        "system_prompt": prompts.SOCIAL_WORKER,
        "allowed_tools": PATIENT_BASE_TOOLS | {"social_resource_search"},
        "pubmed_bias": None,
        "trusted_sources": (
            _AUTHORITATIVE_PATIENT
            + [
                "aosw.org",
                "ons.org",
                # US directories
                "needymeds.org",
                "panfoundation.org",
                "healthwellfoundation.org",
                "copays.org",
                "triagecancer.org",
                "cancerlegalresources.org",
                "cancerfac.org",
                "ulmanfoundation.org",
                "lazarex.org",
                # International
                "citizensadvice.org.uk",
                "mariecurie.org.uk",
                "cancer.ca",
                "wellspring.ca",
                "cancer.org.au",
                "canteen.org.au",
                # Government
                "dol.gov",
                "eeoc.gov",
                "medicare.gov",
                "gov.uk",
                "canada.ca",
            ]
        ),
        # The navigator's value is in directing people to real-world programs;
        # peer-reviewed evidence rarely exists for "is there a ride-to-chemo
        # program in Manchester." The citation gate is softened so that web
        # sources from `patient_source_search` and `social_resource_search`
        # satisfy the [N] requirement.
        "citation_required": True,
        "soft_citation_gate": True,
        "needs_location": True,
        "conditional": False,
    },
    "translator": {
        "display_name": "Translator",
        "color": "#6B5F52",     # warm taupe
        "system_prompt": prompts.TRANSLATOR,
        "allowed_tools": set(),
        "pubmed_bias": None,
        "trusted_sources": [],
        "citation_required": False,
        # Marker: this agent is NOT run through the parallel gather. It runs
        # once, after the synthesizer, via board._translate().
        "role": "post_synthesis",
    },
}


def _disabled_specialists() -> set[str]:
    """Specialist ids to skip entirely, from CANCERPATIENT_DISABLED_SPECIALISTS
    (comma-separated). Lets cost/quality be A/B-tested by dropping helpers."""
    raw = (os.getenv("CANCERPATIENT_DISABLED_SPECIALISTS") or "").strip()
    return {s.strip() for s in raw.split(",") if s.strip()}


def researcher_ids() -> list[str]:
    """All specialists that participate in the parallel research round.

    Honors CANCERPATIENT_DISABLED_SPECIALISTS so specific helpers can be turned
    off (e.g. to cut per-query cost). Defaults to all researchers when unset.
    """
    disabled = _disabled_specialists()
    return [
        sid
        for sid, cfg in SPECIALIST_CONFIGS.items()
        if cfg.get("role") != "post_synthesis" and sid not in disabled
    ]


SPECIALIST_IDS = list(SPECIALIST_CONFIGS.keys())


def public_specialist_info() -> list[dict]:
    """Roster sent to the frontend on `board_started`."""
    out = []
    for sid in SPECIALIST_IDS:
        cfg = SPECIALIST_CONFIGS[sid]
        out.append(
            {
                "id": sid,
                "display_name": cfg["display_name"],
                "color": cfg["color"],
                "conditional": cfg.get("conditional", False),
                "role": cfg.get("role"),
            }
        )
    return out
