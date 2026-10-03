"""
The evaluation harness, tested without any real model: dataset
integrity, defect classification, the full case pipeline against the
fake provider, and DeepEval metric wiring through a stub judge. If
these fail, AI evaluation results cannot be trusted either.
"""

import json

import pytest
from deepeval.models import DeepEvalBaseLLM

from app.schemas.risk_factor import RISK_CATEGORIES, RISK_INDICATORS
from app.risk_engine.engine import CHANGE_TYPE_RISK_CATEGORY
from tests.evals import harness

DATASET = harness.load_dataset()
CASES = {case["id"]: case for case in DATASET["cases"]}


# -- dataset integrity --------------------------------------------------------------


def test_dataset_size_and_unique_ids():
    assert 15 <= len(DATASET["cases"]) <= 25
    assert len(CASES) == len(DATASET["cases"])


@pytest.mark.parametrize("case", DATASET["cases"], ids=list(CASES))
def test_case_is_well_formed(case):
    expected = case["expected"]
    assert expected["risk_level"] in harness.BANDS
    assert expected["risk_level"] in expected["acceptable_risk_levels"]
    assert set(expected["acceptable_risk_levels"]) <= set(harness.BANDS)
    low, high = expected["score_range"]
    assert 0 <= low <= high <= 100
    for key in ("must_flag_categories", "should_not_flag_categories"):
        assert set(expected[key]) <= set(RISK_CATEGORIES), key
    for key in ("expected_indicators", "must_not_indicators"):
        assert set(expected[key]) <= set(RISK_INDICATORS), key
    assert not set(expected["expected_indicators"]) & set(expected["must_not_indicators"])
    assert not set(expected["must_flag_categories"]) & set(expected["should_not_flag_categories"])
    assert expected["key_factors"]
    assert case["input"]["change_type"] in CHANGE_TYPE_RISK_CATEGORY
    assert case["input"]["description"].strip()


def test_dataset_covers_the_required_scenario_mix():
    tags = {tag for case in DATASET["cases"] for tag in case["tags"]}
    for required in ("LOW", "MEDIUM", "HIGH", "CRITICAL", "SANCTIONS", "AML", "FRAUD", "CROSS_BORDER", "INCOMPLETE", "EDGE", "AMBIGUOUS"):
        assert required in tags, required
    levels = {case["expected"]["risk_level"] for case in DATASET["cases"]}
    assert levels == set(harness.BANDS)


def test_unknown_case_filter_is_an_error():
    with pytest.raises(ValueError):
        harness.load_dataset(case_ids=["RA-999"])


# -- defect classification ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("acceptable", "actual", "expected"),
    [
        (["HIGH"], "HIGH", None),
        (["HIGH", "CRITICAL"], "MEDIUM", ("HIGH", "under-classified by 1 band(s)")),
        (["HIGH"], "LOW", ("CRITICAL", "under-classified by 2 band(s)")),
        (["LOW"], "MEDIUM", ("MEDIUM", "over-classified by 1 band(s)")),
        (["LOW"], "HIGH", ("HIGH", "over-classified by 2 band(s)")),
        (["MEDIUM"], None, ("HIGH", "no rating produced")),
    ],
)
def test_under_classification_is_treated_as_worse(acceptable, actual, expected):
    assert harness.classification_defect(acceptable, actual) == expected


@pytest.mark.parametrize(("actual", "deviation"), [(50, 0.0), (35, 5.0), (95, 5.0), (None, None)])
def test_score_deviation_is_distance_to_the_range(actual, deviation):
    assert harness.score_deviation([40, 90], actual) == deviation


def synthetic_raw(**overrides):
    factors = []
    for category in RISK_CATEGORIES:
        factors.append(
            {
                "category": category,
                "applicable": category in overrides.get("applicable", []),
                "indicators": overrides.get("indicators", {}).get(category, []),
                "rationale": "Case-specific reason.",
                "missing_information": [],
                "evidence": [],
                "verified_quote_count": overrides.get("verified", 1),
                "rejected_quote_count": overrides.get("rejected", 0) if category == RISK_CATEGORIES[0] else 0,
            }
        )
    return {
        "case_id": "T",
        "error": overrides.get("error"),
        "factors": factors,
        "calculation": {"final_score": overrides.get("score", 70.0), "risk_band": overrides.get("band", "HIGH"), "triggered_rules": []},
        "trace_id": "trace-x",
    }


def categories_of(defects, category):
    return [d for d in defects if d["category"] == category]


def test_missing_category_and_indicator_defects():
    case = CASES["RA-009"]
    raw = synthetic_raw(applicable=["GEOGRAPHIC_RISK"], indicators={"GEOGRAPHIC_RISK": ["CROSS_BORDER_CAPABILITY"]})
    metrics, defects = harness.deterministic_checks(case, raw, DATASET["thresholds"])
    missing = categories_of(defects, "MISSING_RISK_FACTOR")
    assert metrics["category_recall"] == 0.25
    assert metrics["indicator_recall"] == 0.25
    assert {d["severity"] for d in missing} == {"HIGH", "MEDIUM"}
    assert all(d["trace_id"] == "trace-x" for d in defects)


def test_missing_sanctions_indicator_is_critical():
    case = CASES["RA-014"]
    raw = synthetic_raw(applicable=case["expected"]["must_flag_categories"], band="CRITICAL", score=60)
    _, defects = harness.deterministic_checks(case, raw, DATASET["thresholds"])
    sanctions = [d for d in defects if "SANCTIONS_EXPOSURE" in d["expected"]]
    assert sanctions and sanctions[0]["severity"] == "CRITICAL"


def test_ruled_out_indicator_is_a_hallucination():
    case = CASES["RA-019"]  # cash withdrawal is explicitly disabled
    raw = synthetic_raw(
        applicable=["GEOGRAPHIC_RISK", "PRODUCT_SERVICE_RISK"],
        indicators={"GEOGRAPHIC_RISK": ["CROSS_BORDER_CAPABILITY"], "PRODUCT_SERVICE_RISK": ["CASH_ACCESS"]},
        band="MEDIUM",
        score=45,
    )
    _, defects = harness.deterministic_checks(case, raw, DATASET["thresholds"])
    hallucinations = categories_of(defects, "HALLUCINATION")
    assert hallucinations and hallucinations[0]["severity"] == "HIGH"


def test_rejected_quotes_are_reported_as_contained_hallucinations():
    case = CASES["RA-001"]
    raw = synthetic_raw(band="LOW", score=16, verified=1, rejected=5)
    metrics, defects = harness.deterministic_checks(case, raw, DATASET["thresholds"])
    assert metrics["evidence_verification_rate"] == pytest.approx(10 / 15, abs=0.001)
    assert categories_of(defects, "HALLUCINATION")[0]["severity"] == "MEDIUM"


def test_expected_missing_information_must_be_stated():
    case = CASES["RA-017"]
    raw = synthetic_raw(applicable=["PRODUCT_SERVICE_RISK"], band="MEDIUM", score=45)
    _, defects = harness.deterministic_checks(case, raw, DATASET["thresholds"])
    assert categories_of(defects, "INSUFFICIENT_REASONING")


def test_provider_error_is_a_workflow_error():
    _, defects = harness.deterministic_checks(CASES["RA-001"], synthetic_raw(error="AITimeoutError (AI_TIMEOUT)"), DATASET["thresholds"])
    assert [d["category"] for d in defects] == ["WORKFLOW_ERROR"]


# -- the real pipeline against the fake provider ----------------------------------------------


def test_pipeline_produces_a_scored_band(fake_llm):
    raw = harness.run_pipeline(CASES["RA-009"])
    assert raw["error"] is None
    assert [f["category"] for f in raw["factors"]] == RISK_CATEGORIES
    assert raw["calculation"]["risk_band"] in harness.BANDS
    assert raw["calculation"]["final_score"] is not None
    rated = [f for f in raw["factors"] if f.get("rated")]
    assert rated and all(f["likelihood"] and f["impact"] for f in rated)
    json.dumps(raw)  # replayable


def test_sanctions_scenario_fires_the_policy_rule(fake_llm):
    raw = harness.run_pipeline(CASES["RA-014"])
    assert raw["calculation"]["risk_band"] == "CRITICAL"
    assert "SANCTIONS_EXPOSURE_001" in raw["calculation"]["triggered_rules"]


def test_evaluate_case_emits_the_defect_tracking_format(fake_llm):
    result, raw_runs = harness.evaluate_case(CASES["RA-005"], DATASET["thresholds"])
    for key in (
        "test_case_id", "status", "risk_level_expected", "risk_level_actual", "score_expected_range",
        "score_actual", "reasoning_score", "hallucination_score", "completeness_score", "defects",
    ):
        assert key in result
    assert result["status"] in {"PASS", "FAIL", "ERROR"}
    # Replay re-scores the stored output without calling the model again.
    calls = len(fake_llm.calls)
    replayed, _ = harness.evaluate_case(CASES["RA-005"], DATASET["thresholds"], raw_runs=raw_runs)
    assert len(fake_llm.calls) == calls
    assert replayed["risk_level_actual"] == result["risk_level_actual"]


def test_provider_outage_is_an_errored_case(fake_llm):
    from tests.support.fake_llm import FakeLLM

    fake_llm.transport = FakeLLM.timeout
    result, _ = harness.evaluate_case(CASES["RA-001"], DATASET["thresholds"])
    assert result["status"] == "ERROR"
    assert result["defects"][0]["category"] == "WORKFLOW_ERROR"


def test_consistency_across_runs(fake_llm):
    _, runs = harness.evaluate_case(CASES["RA-005"], DATASET["thresholds"], runs=2)
    assert harness.consistency(runs) == 1.0
    assert harness.consistency(runs[:1]) is None


# -- DeepEval wiring, with a stub judge -------------------------------------------------------


class StubJudge(DeepEvalBaseLLM):
    def __init__(self, score):
        self.score = score
        super().__init__("stub-judge")

    def load_model(self):
        return self

    def generate(self, prompt, schema=None):
        return json.dumps({"score": self.score, "reason": "stub verdict"})

    async def a_generate(self, prompt, schema=None):
        return self.generate(prompt, schema)

    def get_model_name(self):
        return "stub-judge"


def test_geval_metrics_are_scored_through_the_judge(fake_llm):
    result, _ = harness.evaluate_case(CASES["RA-005"], DATASET["thresholds"], judge=StubJudge(9))
    for key in ("reasoning_score", "completeness_score", "hallucination_score", "relevance_score"):
        assert result[key] == pytest.approx(0.9)
    assert not [d for d in result["defects"] if d["category"] == "INSUFFICIENT_REASONING"]


def test_low_judge_scores_become_defects(fake_llm):
    result, _ = harness.evaluate_case(CASES["RA-005"], DATASET["thresholds"], judge=StubJudge(2))
    judged = {d["category"] for d in result["defects"] if d["actual"].startswith("0.20")}
    assert judged >= {"INSUFFICIENT_REASONING", "INCOMPLETE_OUTPUT", "HALLUCINATION"}
    assert result["status"] == "FAIL"


# -- suite summary, gates, regression and report -----------------------------------------------


def fake_result(case_id, status="PASS", correct=True, reasoning=0.9, defects=()):
    return {
        "test_case_id": case_id, "title": case_id, "status": status, "acceptable_risk_levels": ["HIGH"],
        "risk_level_actual": "HIGH", "score_actual": 70, "risk_classification_correct": correct,
        "reasoning_score": reasoning, "completeness_score": 0.9, "hallucination_score": 0.95,
        "relevance_score": 0.9, "category_recall": 1.0, "indicator_recall": 1.0,
        "evidence_verification_rate": 1.0, "consistency_score": None, "score_deviation": 0.0,
        "latency_ms": 1000, "defects": list(defects),
    }


def test_summary_passes_when_all_gates_pass():
    summary = harness.summarize([fake_result(f"C{i}") for i in range(4)], DATASET["thresholds"], "1.0.0", "m", "j")
    assert summary["regression_status"] == "PASS"
    text = harness.format_summary(summary)
    assert "Risk classification:      100%" in text and "Regression status:        PASS" in text


def test_a_critical_defect_fails_the_suite():
    critical = {"category": "MISSING_RISK_FACTOR", "severity": "CRITICAL"}
    results = [fake_result("C1"), fake_result("C2", status="FAIL", defects=[critical])]
    summary = harness.summarize(results, DATASET["thresholds"], "1.0.0", "m", "j")
    assert summary["regression_status"] == "FAIL"
    assert summary["defects_by_severity"]["CRITICAL"] == 1


def test_regression_against_baseline_is_detected():
    baseline = {"metrics": {"reasoning_quality": 0.95, "pass_rate": 1.0}}
    results = [fake_result(f"C{i}", reasoning=0.8) for i in range(4)]
    summary = harness.summarize(results, DATASET["thresholds"], "1.0.0", "m", "j", baseline=baseline)
    assert summary["regressions"] == [{"metric": "reasoning_quality", "baseline": 0.95, "current": 0.8}]
    assert summary["regression_status"] == "FAIL"


def test_report_files_are_written(tmp_path):
    results = [fake_result("C1")]
    summary = harness.summarize(results, DATASET["thresholds"], "1.0.0", "m", None)
    out = harness.write_report(results, summary, {"C1": []}, tmp_path / "run", update_latest=False)
    for name in ("results.json", "defects.json", "summary.json", "summary.txt", "raw_outputs.json"):
        assert (out / name).exists(), name
