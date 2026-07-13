"""Run the seed cases through the board under the CURRENT env config and save
results (outputs + token usage + deterministic metrics) to a JSON file.

Config comes from env, so you A/B by running this once per config:

  CANCERPATIENT_MODEL=gemini-3.5-flash CANCERPATIENT_REASONING_EFFORT=high \
      python -m evals.run_eval --label flash-high
  CANCERPATIENT_MODEL=gemini-flash-lite-latest CANCERPATIENT_REASONING_EFFORT=low \
      python -m evals.run_eval --label lite-low

Then compare the two output files with evals/compare.py.
"""
import argparse
import asyncio
import json
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

# Load .env (API keys) BEFORE importing app modules — config.py reads the model
# name at import time, and any CLI-set env vars take precedence over .env.
load_dotenv()

import os  # noqa: E402
from app import board, llm  # noqa: E402
from app.config import MODEL_NAME, PROVIDER, MAX_TOOL_ITERATIONS  # noqa: E402
from evals import metrics  # noqa: E402


# --- Token-usage capture -----------------------------------------------------
# Wrap llm.chat so every model call in the pipeline adds its usage to a
# thread-safe tally (specialists run in parallel threads). No app code changes.
_usage_lock = threading.Lock()
_usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}
_orig_chat = llm.chat


def _chat_with_usage(*args, **kwargs):
    resp = _orig_chat(*args, **kwargs)
    u = getattr(resp, "usage", None)
    if u is not None:
        with _usage_lock:
            _usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            _usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
            _usage["calls"] += 1
    return resp


llm.chat = _chat_with_usage


def _reset_usage():
    with _usage_lock:
        _usage.update(prompt_tokens=0, completion_tokens=0, calls=0)


def _snapshot_usage() -> dict:
    with _usage_lock:
        return dict(_usage)


def _load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            cases.append(json.loads(line))
    return cases


def _run_one(case: dict) -> dict:
    statuses: dict[str, str] = {}

    def emit(event_type: str, payload: dict) -> None:
        if event_type == "specialist_round_complete":
            statuses[payload.get("specialist")] = payload.get("status")

    _reset_usage()
    t0 = time.perf_counter()
    result = asyncio.run(
        board.run_board(
            case["case"],
            case.get("location", ""),
            case.get("target_language", "English"),
            emit,
            preferences=case.get("preferences", ""),
        )
    )
    wall = time.perf_counter() - t0
    usage = _snapshot_usage()
    return {
        "english_markdown": result.get("english_markdown", ""),
        "translated_markdown": result.get("translated_markdown", ""),
        "references": result.get("references", []),
        "timing": result.get("timing", {}),
        "statuses": statuses,
        "usage": usage,
        "wall_s": round(wall, 1),
        "metrics": metrics.compute(result, statuses),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Run eval cases under the current env config.")
    ap.add_argument("--label", required=True, help="Short name for this config, e.g. 'lite-low'.")
    ap.add_argument("--cases", default=str(Path(__file__).parent / "cases.jsonl"))
    ap.add_argument("--runs", type=int, default=2, help="Runs per case (averaged). Default 2.")
    ap.add_argument("--limit", type=int, default=0, help="Only run the first N cases (0 = all).")
    ap.add_argument("--out", default="", help="Output JSON path (default evals/results/<label>.json).")
    args = ap.parse_args()

    cases = _load_cases(Path(args.cases))
    if args.limit:
        cases = cases[: args.limit]

    config = {
        "label": args.label,
        "model": MODEL_NAME,
        "provider": PROVIDER,
        "reasoning_effort": os.getenv("CANCERPATIENT_REASONING_EFFORT", "high"),
        "max_tool_iterations": MAX_TOOL_ITERATIONS,
        "disabled_specialists": os.getenv("CANCERPATIENT_DISABLED_SPECIALISTS", ""),
    }
    print(f"Config: {config}")
    print(f"Cases: {len(cases)}  Runs each: {args.runs}\n")

    runs = []
    for case in cases:
        for i in range(args.runs):
            print(f"  [{case['id']}] run {i + 1}/{args.runs} …", flush=True)
            rec = _run_one(case)
            rec.update(
                case_id=case["id"],
                case=case["case"],
                location=case.get("location", ""),
                preferences=case.get("preferences", ""),
                target_language=case.get("target_language", "English"),
                run_index=i,
            )
            runs.append(rec)
            m, u = rec["metrics"], rec["usage"]
            print(
                f"      cites={m['citation_count']} grade={m['reading_grade']} "
                f"tokens={u['prompt_tokens']}+{u['completion_tokens']} "
                f"llm_calls={u['calls']} wall={rec['wall_s']}s",
                flush=True,
            )

    out_path = Path(args.out) if args.out else Path(__file__).parent / "results" / f"{args.label}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"config": config, "runs": runs}, indent=2), encoding="utf-8")
    print(f"\nWrote {len(runs)} runs to {out_path}")


if __name__ == "__main__":
    main()
