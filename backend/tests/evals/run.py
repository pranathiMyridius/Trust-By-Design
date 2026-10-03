"""
Run the AI evaluation suite from the command line and print the report.

    python -m tests.evals.run                         # all cases, with judge
    python -m tests.evals.run --cases RA-001,RA-014   # a subset
    python -m tests.evals.run --no-judge              # deterministic checks only
    python -m tests.evals.run --runs 3                # + consistency across runs
    python -m tests.evals.run --models a/x,b/y        # compare models
    python -m tests.evals.run --replay eval_results/latest/raw_outputs.json
    python -m tests.evals.run --save-baseline         # accept this run as baseline

Exit code 1 when a quality gate fails or a metric regresses against
tests/evals/baseline.json -- suitable for CI. Run from backend/.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path


def _bootstrap() -> None:
    # AI usage is metered to a database; never let an eval run write into
    # the real one (backend/.env's DATABASE_URL).
    tmp = tempfile.mkdtemp(prefix="raw-evals-")
    os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp) / 'evals.db'}"
    os.environ.setdefault("DEEPEVAL_TELEMETRY_OPT_OUT", "YES")
    os.environ.setdefault("LANGFUSE_ENVIRONMENT", "eval")

    from app.migrations import run_migrations

    run_migrations()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cases", help="Comma-separated case ids (default: all, or $EVAL_CASES)")
    parser.add_argument("--models", help="Comma-separated model ids to compare (default: OPENROUTER_MODEL)")
    parser.add_argument("--judge-model", help="Judge model id (default: $EVAL_JUDGE_MODEL or OPENROUTER_MODEL)")
    parser.add_argument("--no-judge", action="store_true", help="Skip DeepEval judge metrics")
    parser.add_argument("--runs", type=int, default=int(os.getenv("EVAL_RUNS") or 1), help="Runs per case (consistency)")
    parser.add_argument("--replay", type=Path, help="Re-score a previous raw_outputs.json without calling the model")
    parser.add_argument("--baseline", type=Path, help="Baseline summary to compare against")
    parser.add_argument("--save-baseline", action="store_true", help="Save this run's metrics as the new baseline")
    parser.add_argument("--out", type=Path, help="Output directory (default: backend/eval_results/<timestamp>)")
    args = parser.parse_args(argv)

    # Model and judge text can contain characters a Windows console
    # code page cannot print; never let that crash a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    _bootstrap()

    from app.observability import tracing
    from tests.evals import harness

    case_ids = (args.cases or "").split(",") if args.cases else harness.selected_case_ids()
    dataset = harness.load_dataset(case_ids=case_ids)
    thresholds = dataset["thresholds"]
    replay = json.loads(args.replay.read_text(encoding="utf-8")) if args.replay else None

    from app.ai import provider

    if not replay and not provider.API_KEY:
        print(f"{provider.KEY_NAME} is not set (backend/.env or environment); cannot run live evaluations.")
        print("Use --replay <raw_outputs.json> to re-score a previous run instead.")
        return 2

    judge = None
    if not args.no_judge:
        from tests.evals.judge import OpenRouterJudge

        judge = OpenRouterJudge(args.judge_model)

    harness.configure_live_run()
    models = [m.strip() for m in args.models.split(",")] if args.models else [None]
    baseline = harness.load_baseline(args.baseline or harness.BASELINE_PATH)
    overall_ok = True
    comparison = []

    for model in models:
        results, raw_outputs = [], {}
        label = model or harness.current_model()
        print(f"\nEvaluating {len(dataset['cases'])} case(s) with {label} ...", flush=True)
        for case in dataset["cases"]:
            prior = replay.get(case["id"]) if replay else None
            if replay and prior is None:
                print(f"  {case['id']}  skipped (not in replay file)")
                continue
            result, raw_runs = harness.evaluate_case(
                case, thresholds, judge=judge, model=model, raw_runs=prior, runs=args.runs,
                dataset_version=dataset["version"],
            )
            results.append(result)
            raw_outputs[case["id"]] = raw_runs
            worst = min((harness.SEVERITIES.index(d["severity"]) for d in result["defects"]), default=None)
            note = f"  worst defect: {harness.SEVERITIES[worst]}" if worst is not None else ""
            print(f"  {case['id']}  {result['status']:<5}  {result['risk_level_actual']!s:<9}{note}", flush=True)

        summary = harness.summarize(
            results, thresholds, dataset["version"], label,
            judge.get_model_name() if judge else None,
            baseline=None if len(models) > 1 else baseline,
        )
        out_dir = args.out if len(models) == 1 else (args.out or harness.RESULTS_ROOT) / label.replace("/", "_")
        written = harness.write_report(results, summary, raw_outputs, out_dir)
        print()
        print(harness.format_summary(summary, results))
        print(f"\nReport: {written}")
        overall_ok &= summary["regression_status"] == "PASS"
        comparison.append(summary)

        if args.save_baseline and len(models) == 1:
            harness.save_baseline(summary)
            print(f"Baseline saved to {harness.BASELINE_PATH}")

    if len(comparison) > 1:
        print("\nMODEL COMPARISON")
        print("================")
        keys = ("pass_rate", "risk_classification_accuracy", "reasoning_quality", "completeness", "faithfulness",
                "category_recall", "average_score_deviation", "average_latency_ms")
        print(f"{'metric':<30}" + "".join(f"{s['model'][:28]:>30}" for s in comparison))
        for key in keys:
            print(f"{key:<30}" + "".join(f"{str(s['metrics'][key]):>30}" for s in comparison))

    tracing.flush()
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
