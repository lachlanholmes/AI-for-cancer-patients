"""Compare two eval result files (config A vs B): aggregate metrics + cost,
run a blinded pairwise LLM judge, and emit a self-contained HTML report where
YOU can judge the outputs side by side (blinded) before revealing which config
produced which — and see how your picks compare to the judge's.

  python -m evals.compare evals/results/flash-high.json evals/results/lite-low.json
  # add --no-judge to skip LLM judging (free; still gives you the side-by-side)
"""
import argparse
import html
import json
import random
import statistics as stats
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

import markdown as md_lib  # noqa: E402

_PRICES = json.loads((Path(__file__).parent / "prices.json").read_text(encoding="utf-8"))


def _load(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _by_case(runs: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in runs:
        out.setdefault(r["case_id"], []).append(r)
    return out


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(stats.mean(xs), 2) if xs else 0.0


def _cost_per_query(model: str, in_tok: float, out_tok: float):
    p = _PRICES.get(model)
    if not p:
        return None
    return in_tok / 1e6 * p["input_per_mtok"] + out_tok / 1e6 * p["output_per_mtok"]


def _aggregate(data: dict) -> dict:
    all_runs = data["runs"]
    # Exclude degraded runs (provider outages etc.) so they don't skew averages.
    runs = [r for r in all_runs if not r.get("degraded")]
    n_degraded = len(all_runs) - len(runs)
    if not runs:  # everything degraded — nothing trustworthy to average
        return {
            "label": data["config"]["label"],
            "model": data["config"]["model"],
            "reasoning_effort": data["config"].get("reasoning_effort", "?"),
            "max_tool_iterations": data["config"].get("max_tool_iterations", "?"),
            "degraded_runs": n_degraded,
            "total_runs": len(all_runs),
            "all_degraded": True,
        }
    model = data["config"]["model"]
    in_tok = _mean([r["usage"]["prompt_tokens"] for r in runs])
    out_tok = _mean([r["usage"]["completion_tokens"] for r in runs])
    cost = _cost_per_query(model, in_tok, out_tok)
    return {
        "label": data["config"]["label"],
        "model": model,
        "reasoning_effort": data["config"].get("reasoning_effort", "?"),
        "max_tool_iterations": data["config"].get("max_tool_iterations", "?"),
        "avg_prompt_tokens": int(in_tok),
        "avg_completion_tokens": int(out_tok),
        "avg_llm_calls": _mean([r["usage"]["calls"] for r in runs]),
        "avg_tool_calls": _mean([r["timing"].get("tool_calls", 0) for r in runs]),
        "est_cost_per_query": cost,
        "avg_citations": _mean([r["metrics"]["citation_count"] for r in runs]),
        "avg_reading_grade": _mean([r["metrics"]["reading_grade"] for r in runs]),
        "avg_word_count": int(_mean([r["metrics"]["word_count"] for r in runs])),
        "safety_framing_rate": _mean([1 if r["metrics"]["has_safety_framing"] else 0 for r in runs]),
        "avg_abstentions": _mean([r["metrics"]["abstentions"] for r in runs]),
        "avg_wall_s": _mean([r["wall_s"] for r in runs]),
        "degraded_runs": n_degraded,
        "total_runs": len(all_runs),
        "all_degraded": False,
    }


def _fmt_cost(c):
    return f"${c:.3f}" if isinstance(c, (int, float)) else "n/a"


def _render_md(md_text: str) -> str:
    return md_lib.markdown(md_text or "", extensions=["extra", "sane_lists", "nl2br"])


def _metric_rows(agg_a: dict, agg_b: dict) -> str:
    rows = [
        ("Model", "model"),
        ("Reasoning effort", "reasoning_effort"),
        ("Tool iterations cap", "max_tool_iterations"),
        ("Degraded / total runs", "_degraded"),
        ("Est. $ / query", "est_cost_per_query"),
        ("Avg prompt tokens", "avg_prompt_tokens"),
        ("Avg completion tokens", "avg_completion_tokens"),
        ("Avg LLM calls", "avg_llm_calls"),
        ("Avg tool (Brave/PubMed) calls", "avg_tool_calls"),
        ("Avg citations", "avg_citations"),
        ("Avg reading grade", "avg_reading_grade"),
        ("Avg word count", "avg_word_count"),
        ("Safety-framing rate", "safety_framing_rate"),
        ("Avg abstentions", "avg_abstentions"),
        ("Avg wall time (s)", "avg_wall_s"),
    ]

    def _cell(agg, key):
        if key == "_degraded":
            return f"{agg.get('degraded_runs', 0)} / {agg.get('total_runs', 0)}"
        if agg.get("all_degraded"):
            return "—"
        v = agg.get(key)
        if key == "est_cost_per_query":
            return _fmt_cost(v)
        return "—" if v is None else str(v)

    out = []
    for label, key in rows:
        out.append(
            f"<tr><td>{html.escape(label)}</td><td>{html.escape(_cell(agg_a, key))}</td>"
            f"<td>{html.escape(_cell(agg_b, key))}</td></tr>"
        )
    return "\n".join(out)


def build_report(data_a: dict, data_b: dict, *, do_judge: bool, seed: int) -> str:
    label_a = data_a["config"]["label"]
    label_b = data_b["config"]["label"]
    agg_a, agg_b = _aggregate(data_a), _aggregate(data_b)
    by_a, by_b = _by_case(data_a["runs"]), _by_case(data_b["runs"])

    # Import judge lazily so --no-judge needs no API key.
    judge = None
    if do_judge:
        from evals import judge as judge  # noqa: PLC0414

    case_blocks = []
    js_cases = []
    win_a = win_b = win_tie = 0

    n_deg_cases = 0
    for case_id in [c for c in by_a if c in by_b]:
        # Prefer a healthy run for the side-by-side; fall back to run 0.
        run_a = next((r for r in by_a[case_id] if not r.get("degraded")), by_a[case_id][0])
        run_b = next((r for r in by_b[case_id] if not r.get("degraded")), by_b[case_id][0])
        case_text = run_a["case"]
        case_degraded = run_a.get("degraded") or run_b.get("degraded")
        if case_degraded:
            n_deg_cases += 1

        # Blind: randomly assign A/B to left/right, deterministically per case.
        rng = random.Random(f"{seed}:{case_id}")
        swap = rng.random() < 0.5
        left_run, right_run = (run_b, run_a) if swap else (run_a, run_b)
        left_label, right_label = (label_b, label_a) if swap else (label_a, label_b)

        judge_winner_label = ""
        judge_reason = ""
        if do_judge and not case_degraded:
            # Judge is blind too: it sees "A"=left, "B"=right.
            verdict = judge.pairwise(case_text, left_run["english_markdown"], right_run["english_markdown"])
            w = str(verdict.get("winner", "tie")).upper()
            judge_reason = verdict.get("reason", "")
            if w == "A":
                judge_winner_label = left_label
            elif w == "B":
                judge_winner_label = right_label
            else:
                judge_winner_label = "tie"
            if judge_winner_label == label_a:
                win_a += 1
            elif judge_winner_label == label_b:
                win_b += 1
            else:
                win_tie += 1

        def _mini(run):
            m = run["metrics"]
            return (
                f"cites {m['citation_count']} · grade {m['reading_grade']} · "
                f"words {m['word_count']} · abstains {m['abstentions']} · "
                f"{run['usage']['prompt_tokens']}+{run['usage']['completion_tokens']} tok"
            )

        reveal = (
            f"<div class='reveal' hidden>"
            f"<p><b>Output&nbsp;1</b> = <code>{html.escape(left_label)}</code> &nbsp;|&nbsp; "
            f"<b>Output&nbsp;2</b> = <code>{html.escape(right_label)}</code></p>"
            f"<p class='mini'>Output 1: {html.escape(_mini(left_run))}</p>"
            f"<p class='mini'>Output 2: {html.escape(_mini(right_run))}</p>"
        )
        if do_judge and judge_winner_label:
            reveal += (
                f"<p class='judge'>⚖️ Judge picked: <b>{html.escape(judge_winner_label)}</b>"
                f" — {html.escape(judge_reason)}</p>"
            )
        reveal += "</div>"

        badge = ""
        if case_degraded:
            deg_bits = []
            if run_a.get("degraded"):
                deg_bits.append(f"{html.escape(label_a)}: {html.escape(run_a.get('degraded_reason', ''))}")
            if run_b.get("degraded"):
                deg_bits.append(f"{html.escape(label_b)}: {html.escape(run_b.get('degraded_reason', ''))}")
            badge = (
                "<div class='degraded'>⚠️ Degraded run (provider failure) — not judged; "
                "excluded from averages. " + " · ".join(deg_bits) + "</div>"
            )

        case_blocks.append(
            f"""
<section class="case" data-case="{html.escape(case_id)}">
  <h2>{html.escape(case_id)}</h2>
  {badge}
  <div class="case-text">{html.escape(case_text)}
    <div class="loc">{html.escape(run_a['location'] or '(no location)')} · {html.escape(run_a['target_language'])}</div>
  </div>
  <div class="cols">
    <div class="col"><div class="col-h">Output 1</div><div class="md">{_render_md(left_run['english_markdown'])}</div></div>
    <div class="col"><div class="col-h">Output 2</div><div class="md">{_render_md(right_run['english_markdown'])}</div></div>
  </div>
  <div class="pick">
    Your pick:
    <label><input type="radio" name="pick-{html.escape(case_id)}" value="1"> Output 1</label>
    <label><input type="radio" name="pick-{html.escape(case_id)}" value="2"> Output 2</label>
    <label><input type="radio" name="pick-{html.escape(case_id)}" value="tie"> Tie</label>
    <button class="revealbtn" type="button">Reveal</button>
  </div>
  {reveal}
</section>"""
        )

        js_cases.append({"id": case_id, "left": left_label, "right": right_label, "judge": judge_winner_label})

    judge_summary = ""
    if do_judge:
        judge_summary = (
            f"<p class='jtally'>LLM judge across {win_a + win_b + win_tie} judged cases: "
            f"<b>{html.escape(label_a)}</b> {win_a} · <b>{html.escape(label_b)}</b> {win_b} · tie {win_tie}</p>"
        )

    degraded_banner = ""
    total_deg = (agg_a.get("degraded_runs", 0) or 0) + (agg_b.get("degraded_runs", 0) or 0)
    if total_deg or n_deg_cases:
        degraded_banner = (
            f"<div class='degraded-banner'>⚠️ {total_deg} degraded run(s) across the two configs "
            f"(likely a provider outage). {n_deg_cases} case(s) shown but not judged, and degraded "
            f"runs are excluded from the averages above. Re-run those configs once the model is healthy.</div>"
        )

    return _HTML_TEMPLATE.format(
        label_a=html.escape(label_a),
        label_b=html.escape(label_b),
        metric_rows=_metric_rows(agg_a, agg_b),
        judge_summary=judge_summary,
        degraded_banner=degraded_banner,
        cases="\n".join(case_blocks),
        js_cases=json.dumps(js_cases),
        label_a_js=json.dumps(label_a),
        label_b_js=json.dumps(label_b),
    )


_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>A/B eval: {label_a} vs {label_b}</title>
<style>
  body {{ font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; margin: 0; background:#f7f7f8; color:#1a1a1a; }}
  header {{ background:#fff; border-bottom:1px solid #e3e3e6; padding:20px 24px; position:sticky; top:0; z-index:5; }}
  h1 {{ font-size:20px; margin:0 0 8px; }}
  .wrap {{ max-width:1200px; margin:0 auto; padding:24px; }}
  table.summary {{ border-collapse:collapse; width:100%; background:#fff; border:1px solid #e3e3e6; font-size:14px; }}
  table.summary td, table.summary th {{ border:1px solid #ececed; padding:7px 10px; text-align:left; }}
  table.summary th {{ background:#fafafa; }}
  .tally {{ background:#fff; border:1px solid #e3e3e6; padding:12px 16px; margin:16px 0; font-size:15px; }}
  .jtally, .ytally {{ margin:4px 0; }}
  .case {{ background:#fff; border:1px solid #e3e3e6; border-radius:8px; padding:18px; margin:20px 0; }}
  .case h2 {{ font-size:15px; margin:0 0 8px; color:#555; font-family:ui-monospace,monospace; }}
  .case-text {{ background:#f2f4f7; border-radius:6px; padding:10px 12px; font-size:14px; margin-bottom:14px; }}
  .loc {{ color:#777; font-size:12px; margin-top:4px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; }}
  @media (max-width:820px) {{ .cols {{ grid-template-columns:1fr; }} }}
  .col {{ border:1px solid #e8e8ea; border-radius:6px; overflow:hidden; }}
  .col-h {{ background:#111827; color:#fff; padding:6px 10px; font-weight:600; font-size:13px; }}
  .md {{ padding:12px 14px; font-size:14px; line-height:1.5; max-height:520px; overflow:auto; }}
  .md h1,.md h2,.md h3 {{ font-size:15px; margin:14px 0 6px; }}
  .md ul {{ padding-left:20px; }}
  .pick {{ margin-top:14px; font-size:14px; display:flex; gap:14px; align-items:center; flex-wrap:wrap; }}
  .pick label {{ cursor:pointer; }}
  .revealbtn {{ margin-left:auto; background:#4f46e5; color:#fff; border:0; border-radius:6px; padding:6px 14px; cursor:pointer; }}
  .reveal {{ margin-top:12px; padding:12px; background:#f8f9fb; border:1px dashed #cbd0d8; border-radius:6px; font-size:13px; }}
  .reveal code {{ background:#eef; padding:1px 5px; border-radius:4px; }}
  .mini {{ color:#555; margin:3px 0; font-family:ui-monospace,monospace; font-size:12px; }}
  .judge {{ margin-top:6px; }}
  .agree {{ font-weight:600; }}
  .degraded-banner {{ background:#fff4e5; border:1px solid #f0c27a; color:#7a4a00; padding:12px 16px; border-radius:6px; margin:0 0 16px; font-size:14px; }}
  .degraded {{ background:#fff4e5; border:1px solid #f0c27a; color:#7a4a00; padding:8px 12px; border-radius:6px; margin-bottom:12px; font-size:13px; }}
</style></head>
<body>
<header>
  <h1>A/B eval &mdash; <code>{label_a}</code> vs <code>{label_b}</code></h1>
  <div>Judge each case blind (Output 1 vs 2), then <b>Reveal</b>. Your picks are saved locally.</div>
</header>
<div class="wrap">
  {degraded_banner}
  <table class="summary"><tr><th>Metric</th><th>{label_a}</th><th>{label_b}</th></tr>
  {metric_rows}
  </table>
  <div class="tally">
    {judge_summary}
    <p class="ytally" id="ytally">Your picks: none yet.</p>
    <p class="ytally" id="agreement"></p>
  </div>
  {cases}
</div>
<script>
  const CASES = {js_cases};
  const LABEL_A = {label_a_js}, LABEL_B = {label_b_js};
  const KEY = "abpicks:" + LABEL_A + ":" + LABEL_B;
  const picks = JSON.parse(localStorage.getItem(KEY) || "{{}}");

  function pickedLabel(caseId, val) {{
    const c = CASES.find(x => x.id === caseId);
    if (!c || val === "tie") return "tie";
    return val === "1" ? c.left : c.right;
  }}
  function refresh() {{
    const ids = CASES.map(c => c.id);
    const done = ids.filter(id => picks[id]);
    let a=0,b=0,t=0;
    done.forEach(id => {{ const l = pickedLabel(id, picks[id]); if(l===LABEL_A)a++; else if(l===LABEL_B)b++; else t++; }});
    document.getElementById("ytally").innerHTML =
      done.length ? `Your picks (${{done.length}}/${{ids.length}}): <b>${{LABEL_A}}</b> ${{a}} &middot; <b>${{LABEL_B}}</b> ${{b}} &middot; tie ${{t}}` : "Your picks: none yet.";
    const judged = CASES.filter(c => c.judge && picks[c.id]);
    if (judged.length) {{
      const agree = judged.filter(c => pickedLabel(c.id, picks[c.id]) === c.judge).length;
      document.getElementById("agreement").innerHTML =
        `<span class="agree">You agree with the judge on ${{agree}}/${{judged.length}} judged cases.</span>`;
    }}
  }}
  document.querySelectorAll(".case").forEach(sec => {{
    const id = sec.dataset.case;
    sec.querySelectorAll(`input[name="pick-${{id}}"]`).forEach(inp => {{
      if (picks[id] === inp.value) inp.checked = true;
      inp.addEventListener("change", () => {{ picks[id] = inp.value; localStorage.setItem(KEY, JSON.stringify(picks)); refresh(); }});
    }});
    sec.querySelector(".revealbtn").addEventListener("click", () => {{
      const r = sec.querySelector(".reveal"); r.hidden = !r.hidden;
    }});
  }});
  refresh();
</script>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser(description="Compare two eval result files into an HTML report.")
    ap.add_argument("result_a")
    ap.add_argument("result_b")
    ap.add_argument("--out", default="evals/report.html")
    ap.add_argument("--no-judge", action="store_true", help="Skip the LLM judge (free; still builds side-by-side).")
    ap.add_argument("--seed", type=int, default=7, help="Seed for blinding left/right order.")
    args = ap.parse_args()

    data_a, data_b = _load(args.result_a), _load(args.result_b)
    print(f"Comparing {data_a['config']['label']} vs {data_b['config']['label']} …")
    html_out = build_report(data_a, data_b, do_judge=not args.no_judge, seed=args.seed)
    out = Path(args.out)
    out.write_text(html_out, encoding="utf-8")
    print(f"Wrote report: {out.resolve()}")
    print("Open it in your browser to judge the outputs side by side (blinded).")


if __name__ == "__main__":
    main()
