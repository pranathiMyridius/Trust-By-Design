"""
pytest wiring for the AI evaluation suite (`pytest tests/evals -v`).

Each dataset case is one test; a final test applies the suite-level
quality gates and baseline regression check. The same report as
`python -m tests.evals.run` is written to backend/eval_results/ and
printed at the end of the session.

    EVAL_CASES=RA-001,RA-014   subset of cases
    EVAL_NO_JUDGE=1            deterministic checks only
    EVAL_RUNS=3                repeated runs per case (consistency)
    EVAL_REPLAY=path           re-score a previous raw_outputs.json
    EVAL_JUDGE_MODEL=...       judge model id
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.evals import harness

# The deterministic suite turns tracing off; evaluations are exactly
# what it is for (still needs LANGFUSE_* keys to send anything).
os.environ["LANGFUSE_ENABLED"] = os.getenv("EVAL_LANGFUSE_ENABLED", "true")
os.environ.setdefault("LANGFUSE_ENVIRONMENT", "eval")


class EvalSession:
    def __init__(self) -> None:
        self.dataset = harness.load_dataset(case_ids=harness.selected_case_ids())
        self.thresholds = self.dataset["thresholds"]
        replay_path = os.getenv("EVAL_REPLAY")
        self.replay = json.loads(Path(replay_path).read_text(encoding="utf-8")) if replay_path else None
        self.runs = int(os.getenv("EVAL_RUNS") or 1)
        self.judge = None
        if not os.getenv("EVAL_NO_JUDGE"):
            from tests.evals.judge import OpenRouterJudge

            self.judge = OpenRouterJudge()
        self.results: list[dict] = []
        self.raw_outputs: dict[str, list[dict]] = {}
        self.summary: dict | None = None
        self.report_dir: Path | None = None

    def evaluate(self, case: dict) -> dict:
        prior = self.replay.get(case["id"]) if self.replay else None
        if self.replay and prior is None:
            pytest.skip(f"{case['id']} is not in the replay file")
        result, raw_runs = harness.evaluate_case(
            case, self.thresholds, judge=self.judge, raw_runs=prior, runs=self.runs,
            dataset_version=self.dataset["version"],
        )
        self.results.append(result)
        self.raw_outputs[case["id"]] = raw_runs
        return result

    def finish(self) -> dict:
        if self.summary is None and self.results:
            self.summary = harness.summarize(
                self.results, self.thresholds, self.dataset["version"], harness.current_model(),
                self.judge.get_model_name() if self.judge else None, baseline=harness.load_baseline(),
            )
            self.report_dir = harness.write_report(self.results, self.summary, self.raw_outputs)
            from app.observability import tracing

            tracing.flush()
        return self.summary


_SESSION: EvalSession | None = None


@pytest.fixture(scope="session")
def eval_session() -> EvalSession:
    global _SESSION
    from app.ai import provider

    if not os.getenv("EVAL_REPLAY") and not provider.API_KEY:
        pytest.skip(
            f"{provider.KEY_NAME} is not set (live evaluations also need LIVE_LLM=1); set EVAL_REPLAY to re-score."
        )
    if _SESSION is None:
        harness.configure_live_run()
        _SESSION = EvalSession()
    return _SESSION


def pytest_terminal_summary(terminalreporter):
    if _SESSION is None or not _SESSION.results:
        return
    summary = _SESSION.finish()
    terminalreporter.write_sep("=", "AI evaluation report")
    terminalreporter.write_line(harness.format_summary(summary, _SESSION.results))
    terminalreporter.write_line(f"Report written to {_SESSION.report_dir}")
