"""Deterministic reports from scores.jsonl (docs/spec/BENCHMARK.md sections 5-7). Never hand-edited."""
from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from agent_sql.eval.stats import holm, mcnemar_exact, paired_bootstrap_diff, wilson

PRIMARY_PAIRS = [("H1", "a1", "b1"), ("H2", "a2", "a1"), ("H3", "a2", "b0")]
SECONDARY_PAIRS = [("H6", "b1", "b0"), ("H5", "a1", "b1_sc")]


def _rate(xs: list[bool]) -> tuple[float, float, float, int]:
    n = len(xs)
    k = sum(1 for x in xs if x)
    lo, hi = wilson(k, n)
    return (k / n if n else float("nan"), lo, hi, n)


def _fmt_rate(t: tuple[float, float, float, int]) -> str:
    r, lo, hi, n = t
    return "n/a" if n == 0 else f"{r:.3f} [{lo:.3f}, {hi:.3f}] (n={n})"


def _q(xs: list[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    i = min(len(s) - 1, max(0, round(q * (len(s) - 1))))
    return s[i]


def load_scores(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def summarize(scores: list[dict]) -> dict:
    by = defaultdict(list)
    for s in scores:
        by[(s["system_id"], s["dataset"])].append(s)
        by[(s["system_id"], "combined")].append(s)
    rows = {}
    for (sid, ds), items in sorted(by.items()):
        rc = [s for s in items if s["result_checkable"]]
        fail = [s for s in items if s["had_failure"]]
        succ = sum(1 for s in items if s["success"])
        cost = sum(s["cost_usd"] for s in items)
        tables = [s for s in items if s["tables_exact"] is not None]
        runs = sorted({s["run_index"] for s in items})
        per_run = [sum(1 for s in items if s["run_index"] == r and s["success"])
                   / max(1, sum(1 for s in items if s["run_index"] == r)) for r in runs]
        per_task = defaultdict(list)
        for s in items:
            per_task[s["task_id"]].append(s["success"])
        numeric = sum(s["numeric_claims"] for s in items)
        rows[(sid, ds)] = {
            "system": sid, "dataset": ds, "n": len(items),
            "M01_exec_success": _rate([s["executed"] for s in rc]),
            "M02_exec_accuracy": _rate([bool(s["correct"]) for s in rc]),
            "M03_task_success": _rate([s["success"] for s in items]),
            "M04_recovery_exec": _rate([bool(s["recovered_exec"]) for s in fail]),
            "M04_recovery_success": _rate([bool(s["recovered_success"]) for s in fail]),
            "M05_tables_exact": _rate([bool(s["tables_exact"]) for s in tables]),
            "M06_tool_calls_mean": statistics.fmean([s["tool_calls"] for s in items]) if items else float("nan"),
            "M06_llm_calls_mean": statistics.fmean([s["llm_calls"] for s in items]) if items else float("nan"),
            "M07_latency_p50": _q([s["latency_s"] for s in items], 0.5),
            "M07_latency_p90": _q([s["latency_s"] for s in items], 0.9),
            "M08_input_tokens_mean": statistics.fmean(
                [s["input_tokens"] + s["cache_read_input_tokens"] + s["cache_creation_input_tokens"] for s in items])
            if items else float("nan"),
            "M08_output_tokens_mean": statistics.fmean([s["output_tokens"] for s in items]) if items else float("nan"),
            "M09_cost_mean_usd": cost / len(items) if items else float("nan"),
            "M10_cost_per_success_usd": (cost / succ) if succ else float("inf"),
            "M11_validation_failure": _rate([s["validation_failed"] for s in items]),
            "M12_success_sd_across_runs": statistics.pstdev(per_run) if len(per_run) > 1 else float("nan"),
            "M12_pass_all_runs": (sum(1 for v in per_task.values() if all(v)) / len(per_task)) if len(runs) > 1
            else float("nan"),
            "M12_flip_rate": (sum(1 for v in per_task.values() if len(set(v)) > 1) / len(per_task))
            if len(runs) > 1 else float("nan"),
            "S01_grounded_claim_rate": (sum(s["grounded_claims"] for s in items) / numeric) if numeric else float("nan"),
            "S04_abstention_on_insufficient": _rate([s["success"] for s in items if s["task_type"] == "insufficient"]),
            "S04_false_abstention_on_answerable": _rate([s["abstained"] for s in items
                                                         if s["task_type"] != "insufficient"]),
            "S05_silent_wrong": _rate([s["silent_wrong"] for s in items]),
            "S06_self_service": _rate([s["self_service"] for s in items if s["dataset"] == "bi"]),
            "S03_retrieval_recall": statistics.fmean(rr) if (rr := [s["retrieval_recall"] for s in items
                                                                   if s.get("retrieval_recall") is not None])
            else float("nan"),
            "failure_codes": dict(Counter(s["failure_code"] for s in items if s["failure_code"])),
        }
    return rows


def paired(scores: list[dict], a: str, b: str, dataset: str = "combined") -> dict | None:
    def per_task(sid: str) -> dict[str, list[bool]]:
        out = defaultdict(list)
        for s in scores:
            if s["system_id"] == sid and (dataset == "combined" or s["dataset"] == dataset):
                out[s["task_id"]].append(s["success"])
        return out

    pa, pb = per_task(a), per_task(b)
    common = sorted(set(pa) & set(pb))
    if not common:
        return None
    mean_a = [sum(pa[t]) / len(pa[t]) for t in common]
    mean_b = [sum(pb[t]) / len(pb[t]) for t in common]
    maj_a = [sum(pa[t]) * 2 > len(pa[t]) for t in common]
    maj_b = [sum(pb[t]) * 2 > len(pb[t]) for t in common]
    diff, lo, hi = paired_bootstrap_diff(mean_a, mean_b)
    return {"a": a, "b": b, "dataset": dataset, "n_tasks": len(common), "delta_M03": diff, "ci": [lo, hi],
            **mcnemar_exact(maj_a, maj_b)}


def render(scores: list[dict], manifest: dict | None = None) -> tuple[str, list[dict]]:
    rows = summarize(scores)
    lines = ["# Evaluation report", ""]
    if manifest:
        lines += [f"- experiment: `{manifest.get('experiment_id')}`", f"- git sha: `{manifest.get('git_sha')}`"
                  + (" (dirty tree: exploratory)" if manifest.get("dirty") else ""),
                  f"- config hash: `{manifest.get('config_hash')}`", f"- model: `{manifest.get('model_id')}`",
                  f"- llm mode: `{manifest.get('llm_mode')}`", f"- pricing version: `{manifest.get('pricing_version')}`",
                  f"- completed: {manifest.get('completed')}/{manifest.get('scheduled')}"
                  + (" **INCOMPLETE**" if manifest.get("incomplete") else ""),
                  f"- post-hoc checks: `{scores[0].get('posthoc_version', 'v1:V1,V6,V7')}`" if scores else "", ""]
        if manifest.get("exploratory") or manifest.get("llm_mode") == "replay":
            lines += ["> Exploratory / dev-time output. Not an experimental result.", ""]
    lines += ["## Main metrics (rate [95% Wilson CI])", "",
              "| system | dataset | n | M01 exec | M02 accuracy | M03 success | M05 tables | M11 valid-fail | S05 silent-wrong |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows.values():
        lines.append(f"| {r['system']} | {r['dataset']} | {r['n']} | {_fmt_rate(r['M01_exec_success'])} | "
                     f"{_fmt_rate(r['M02_exec_accuracy'])} | {_fmt_rate(r['M03_task_success'])} | "
                     f"{_fmt_rate(r['M05_tables_exact'])} | {_fmt_rate(r['M11_validation_failure'])} | "
                     f"{_fmt_rate(r['S05_silent_wrong'])} |")
    lines += ["", "## Cost, latency, tokens", "",
              "| system | dataset | tool calls | LLM calls | p50 s | p90 s | input tok | output tok | cost/task $ | cost/success $ |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows.values():
        lines.append(f"| {r['system']} | {r['dataset']} | {r['M06_tool_calls_mean']:.2f} | {r['M06_llm_calls_mean']:.2f} | "
                     f"{r['M07_latency_p50']:.2f} | {r['M07_latency_p90']:.2f} | {r['M08_input_tokens_mean']:.0f} | "
                     f"{r['M08_output_tokens_mean']:.0f} | {r['M09_cost_mean_usd']:.4f} | {r['M10_cost_per_success_usd']:.4f} |")
    lines += ["", "## Recovery, reliability, grounding", "",
              "| system | dataset | M04 recov→exec | M04 recov→success | M12 SD | M12 pass^k | M12 flip | S01 grounded | S03 retrieval recall | S04 correct abstain | S04 false abstain | S06 self-service |",
              "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in rows.values():
        lines.append(f"| {r['system']} | {r['dataset']} | {_fmt_rate(r['M04_recovery_exec'])} | "
                     f"{_fmt_rate(r['M04_recovery_success'])} | {r['M12_success_sd_across_runs']:.3f} | "
                     f"{r['M12_pass_all_runs']:.3f} | {r['M12_flip_rate']:.3f} | {r['S01_grounded_claim_rate']:.3f} | "
                     f"{r['S03_retrieval_recall']:.3f} | "
                     f"{_fmt_rate(r['S04_abstention_on_insufficient'])} | {_fmt_rate(r['S04_false_abstention_on_answerable'])} | "
                     f"{_fmt_rate(r['S06_self_service'])} |")
    systems = {s["system_id"] for s in scores}
    comps = [(h, a, b) for h, a, b in PRIMARY_PAIRS + SECONDARY_PAIRS if a in systems and b in systems]
    if comps:
        lines += ["", "## Paired comparisons (Δ M03 = a − b; exact McNemar on per-task majority)", "",
                  "| id | a | b | n | Δ M03 [95% CI] | a-only | b-only | p | Holm p (H1–H3) |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        results = {h: paired(scores, a, b, "bi" if h == "H2" else "combined") for h, a, b in comps}
        prim = {h: r["p_value"] for h, r in results.items() if r and h in {"H1", "H2", "H3"}}
        adj = holm(prim) if prim else {}
        for h, r in results.items():
            if r:
                lines.append(f"| {h} | {r['a']} | {r['b']} | {r['n_tasks']} | {r['delta_M03']:+.3f} "
                             f"[{r['ci'][0]:+.3f}, {r['ci'][1]:+.3f}] | {r['a_only']} | {r['b_only']} | "
                             f"{r['p_value']:.4f} | {adj.get(h, float('nan')):.4f} |")
    lines += ["", "## Failure codes (auto; UNLABELED requires hand labeling in E7)", ""]
    codes = sorted({c for r in rows.values() for c in r["failure_codes"]})
    if codes:
        lines += ["| system | dataset | " + " | ".join(codes) + " |", "| --- | --- | " + " | ".join("---" for _ in codes) + " |"]
        for r in rows.values():
            lines.append(f"| {r['system']} | {r['dataset']} | " + " | ".join(str(r["failure_codes"].get(c, 0))
                                                                            for c in codes) + " |")
    else:
        lines.append("No failures.")
    flat = []
    for r in rows.values():
        flat.append({k: (v[0] if isinstance(v, tuple) else (json.dumps(v) if isinstance(v, dict) else v))
                     for k, v in r.items()})
    return "\n".join(lines) + "\n", flat


def write_report(run_dir: Path, out_md: Path | None = None) -> Path:
    scores = load_scores(run_dir / "scores.jsonl")
    manifest = json.loads((run_dir / "manifest.json").read_text()) if (run_dir / "manifest.json").exists() else None
    md, flat = render(scores, manifest)
    out_md = out_md or run_dir / "report.md"
    out_md.write_text(md, encoding="utf-8")
    with open(out_md.with_suffix(".csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(flat[0].keys()) if flat else ["system"], lineterminator="\n")
        w.writeheader()
        w.writerows(flat)
    return out_md
