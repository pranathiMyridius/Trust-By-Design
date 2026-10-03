"""
AI quality evaluation: one test per dataset scenario, then the suite
gates. Calls a real model -- run explicitly with `pytest tests/evals -v`.

A case fails on any CRITICAL/HIGH/MEDIUM defect (LOW defects are
warnings). The final test fails when a suite-level threshold in
dataset.json is missed or a metric regresses against baseline.json.
"""

import json

import pytest

from tests.evals import harness

pytestmark = pytest.mark.live_llm

DATASET = harness.load_dataset(case_ids=harness.selected_case_ids())


def _describe(result: dict) -> str:
    lines = [
        f"{result['test_case_id']} {result['title']}: {result['status']}",
        f"  level expected {result['acceptable_risk_levels']} actual {result['risk_level_actual']}; "
        f"score expected {result['score_expected_range']} actual {result['score_actual']}",
    ]
    for defect in result["defects"]:
        lines.append(f"  [{defect['severity']}] {defect['category']}: expected {defect['expected']}; got {defect['actual']}")
    if result.get("trace_id"):
        lines.append(f"  trace: {result['trace_id']}")
    return "\n".join(lines)


@pytest.mark.parametrize("case", DATASET["cases"], ids=[case["id"] for case in DATASET["cases"]])
def test_risk_assessment_case(case, eval_session):
    result = eval_session.evaluate(case)
    assert result["status"] == "PASS", _describe(result)


def test_suite_quality_gates_and_regression(eval_session):
    summary = eval_session.finish()
    if summary is None:
        pytest.skip("no cases were evaluated")
    problems = [g for g in summary["gates"] if not g["passed"]] + summary["regressions"]
    assert summary["regression_status"] == "PASS", json.dumps(problems, indent=2)
