"""agent-sql CLI: run | score | report | prereg."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from agent_sql.config.settings import REPO_ROOT, load_eval_settings


def cmd_run(args: argparse.Namespace) -> int:
    from agent_sql.eval.runner import Runner, load_experiment

    cfg = load_experiment(Path(args.experiment))
    outcome = Runner(cfg, replay=args.replay, allow_dirty=args.allow_dirty,
                     out_dir=Path(args.output) if args.output else None).run()
    print(json.dumps({"out_dir": str(outcome.out_dir), "completed": outcome.completed, "scheduled": outcome.total,
                      "cost_usd": round(outcome.cost_usd, 4), "incomplete": outcome.incomplete,
                      "errors": outcome.errors[:5]}))
    if args.score:
        return cmd_score(argparse.Namespace(run_dir=str(outcome.out_dir), report=True))
    return 1 if outcome.errors else 0


def score_run(run_dir: Path) -> Path:
    from agent_sql.db.registry import open_database
    from agent_sql.eval.runner import load_experiment_from_dir, load_tasks, read_predictions
    from agent_sql.eval.scoring import POSTHOC_CHECKS_VERSION, Scorer

    cfg = load_experiment_from_dir(run_dir)
    ev = load_eval_settings()
    tasks = {t.task_id: t for ref in cfg.datasets for t in load_tasks(ref)}
    dbs = {}
    for t in tasks.values():
        if t.db_id not in dbs:
            dbs[t.db_id] = open_database(t.db_id, cfg.bi_engine if t.db_id.startswith("retailco_") else None)
    scorer = Scorer(dbs, ev.eval_reexec_timeout_s, ev.eval_reexec_max_rows)
    preds = sorted(read_predictions(run_dir), key=lambda p: (p[0], p[1].task_id, p[1].system_id))
    out = run_dir / "scores.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for run_index, pred in preds:
            s = scorer.score(tasks[pred.task_id], pred, run_index).to_dict()
            s["posthoc_version"] = POSTHOC_CHECKS_VERSION
            f.write(json.dumps(s, sort_keys=True) + "\n")
    return out


def cmd_score(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    print("scores:", score_run(run_dir))
    if getattr(args, "report", False):
        from agent_sql.eval.report import write_report

        print("report:", write_report(run_dir))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    from agent_sql.eval.report import write_report

    if args.verify_reproducible:
        from agent_sql.eval.report import load_scores, render

        bad = []
        for run_dir in sorted((REPO_ROOT / "results").glob("*/")) if args.all else [Path(args.run_dir)]:
            if not (run_dir / "scores.jsonl").exists() or not (run_dir / "report.md").exists():
                continue
            manifest = json.loads((run_dir / "manifest.json").read_text())
            md, _ = render(load_scores(run_dir / "scores.jsonl"), manifest)
            if md != (run_dir / "report.md").read_text():
                bad.append(str(run_dir))
        print("reproducible" if not bad else f"NOT reproducible: {bad}")
        return 1 if bad else 0
    print("report:", write_report(Path(args.run_dir), Path(args.out) if args.out else None))
    return 0


def cmd_prereg(args: argparse.Namespace) -> int:
    from agent_sql.eval.prereg import verify

    ok, msg = verify()
    print(msg)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="agent-sql")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run an experiment config")
    r.add_argument("--experiment", required=True)
    r.add_argument("--replay", action="store_true", help="serve LLM calls from the cache only (fail on miss)")
    r.add_argument("--allow-dirty", action="store_true", help="exploratory run on a dirty tree")
    r.add_argument("--output")
    r.add_argument("--score", action="store_true", help="score and report after running")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("score", help="score predictions by independent re-execution")
    s.add_argument("--run-dir", required=True)
    s.add_argument("--report", action="store_true")
    s.set_defaults(fn=cmd_score)
    p = sub.add_parser("report", help="render report from scores")
    p.add_argument("--run-dir")
    p.add_argument("--out")
    p.add_argument("--verify-reproducible", action="store_true")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_report)
    g = sub.add_parser("prereg", help="verify preregistration")
    g.add_argument("--verify", action="store_true")
    g.set_defaults(fn=cmd_prereg)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
