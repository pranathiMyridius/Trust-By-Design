"""
Risk-assessment evaluation harness, shared by `pytest tests/evals` and
`python -m tests.evals.run`.

What is evaluated is exactly what the product produces, minus the
database: the real risk-factor analyzer (prompt, parsing, evidence
verification), the real likelihood/impact suggestion call for every
applicable factor, and the app's own deterministic methodology
(app/risk_engine/scoring.py) turning those suggestions into a score and
band -- i.e. what an analyst sees after "Suggest Ratings with AI".

Two layers of checks:

  deterministic  classification vs acceptable levels, score vs range,
                 category / indicator recall, forbidden indicators,
                 verified-quote rate, output shape. Needs no judge.
  DeepEval       reasoning quality, completeness, faithfulness and
                 relevance, scored by an LLM judge against rubrics
                 (metrics.py). Skipped with --no-judge.

Every failed check becomes a typed defect (category + severity) so a run
produces a machine-readable defect list as well as a pass/fail verdict.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import statistics
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

EVAL_DIR = Path(__file__).resolve().parent
DATASET_PATH = EVAL_DIR / "dataset.json"
BASELINE_PATH = EVAL_DIR / "baseline.json"
RESULTS_ROOT = EVAL_DIR.parents[1] / "eval_results"

BANDS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEVERITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
# LOW defects are reported as warnings; anything above fails the case.
FAILING_SEVERITIES = {"CRITICAL", "HIGH", "MEDIUM"}

JUDGE_METRICS = ("reasoning_quality", "completeness", "faithfulness", "relevance")
# Metrics compared against the baseline for regression detection.
REGRESSION_METRICS = (
    "pass_rate",
    "risk_classification_accuracy",
    "category_recall",
    "indicator_recall",
    "evidence_verification_rate",
    *JUDGE_METRICS,
)

# Same fields app/api/assessments.py::_assessment_context_for_rating sends.
RATING_CONTEXT_FIELDS = (
    "title",
    "change_type",
    "description",
    "product_or_service_name",
    "customer_segment",
    "countries_jurisdictions",
    "delivery_channels",
    "transaction_types",
    "expected_transaction_volume",
    "expected_transaction_value",
    "third_party_vendor_usage",
    "technology_process_changes",
    "legal_entity",
)

# Placeholders the analyzer writes itself; not model reasoning.
_ANALYZER_PLACEHOLDERS = (
    "No rationale was provided by the model.",
    "The model did not return an assessment for this category.",
)
_ANALYZER_MISSING_INFO = "The AI returned no assessment for this category."


class Defect:
    SCORING_ERROR = "SCORING_ERROR"
    RISK_CLASSIFICATION_ERROR = "RISK_CLASSIFICATION_ERROR"
    MISSING_RISK_FACTOR = "MISSING_RISK_FACTOR"
    HALLUCINATION = "HALLUCINATION"
    INSUFFICIENT_REASONING = "INSUFFICIENT_REASONING"
    INCOMPLETE_OUTPUT = "INCOMPLETE_OUTPUT"
    FORMAT_ERROR = "FORMAT_ERROR"
    WORKFLOW_ERROR = "WORKFLOW_ERROR"
    API_ERROR = "API_ERROR"
    UI_ERROR = "UI_ERROR"


# ---------------------------------------------------------------------------
# dataset
# ---------------------------------------------------------------------------


def load_dataset(path: Path = DATASET_PATH, case_ids: Iterable[str] | None = None) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    wanted = {cid.strip() for cid in case_ids or [] if cid.strip()}
    if wanted:
        unknown = wanted - {case["id"] for case in data["cases"]}
        if unknown:
            raise ValueError(f"Unknown case id(s): {', '.join(sorted(unknown))}")
        data["cases"] = [case for case in data["cases"] if case["id"] in wanted]
    return data


def selected_case_ids() -> list[str]:
    return [cid for cid in (os.getenv("EVAL_CASES") or "").split(",") if cid.strip()]


# ---------------------------------------------------------------------------
# running the AI pipeline
# ---------------------------------------------------------------------------


def configure_live_run() -> None:
    """Retry settings suited to rate-limited (e.g. free-tier) models."""

    import app.ai.risk_factor_analyzer as analyzer

    analyzer.OPENROUTER_MAX_ATTEMPTS = max(1, int(os.getenv("EVAL_MAX_ATTEMPTS") or 3))
    analyzer.OPENROUTER_BACKOFF_SECONDS = float(os.getenv("EVAL_BACKOFF_SECONDS") or 5)


@contextmanager
def model_override(model: str | None):
    import app.ai.likelihood_impact_analyzer as likelihood
    import app.ai.risk_factor_analyzer as analyzer

    if not model:
        yield
        return
    previous = (analyzer.OPENROUTER_MODEL, likelihood.OPENROUTER_MODEL)
    analyzer.OPENROUTER_MODEL = likelihood.OPENROUTER_MODEL = model
    try:
        yield
    finally:
        analyzer.OPENROUTER_MODEL, likelihood.OPENROUTER_MODEL = previous


def current_model() -> str:
    import app.ai.risk_factor_analyzer as analyzer

    return analyzer.OPENROUTER_MODEL


def run_pipeline(case: dict[str, Any], model: str | None = None, dataset_version: str | None = None) -> dict[str, Any]:
    """One live run of the AI pipeline for a case. Returns a JSON-safe dict."""

    from app.ai.errors import AIProviderError
    from app.ai.likelihood_impact_analyzer import estimate_likelihood_impact
    from app.ai.risk_factor_analyzer import identify_risk_factors
    from app.observability import tracing
    from app.risk_engine.evidence import build_evidence_sources
    from app.risk_engine.methodology import config_from_row
    from app.risk_engine.scoring import calculate_inherent_risk, compute_factor_score

    fields = case["input"]
    assessment = {"id": None, "status": "INTAKE", **fields}
    config = config_from_row(None)
    mitigants = set(config["mitigant_categories"])

    raw: dict[str, Any] = {
        "case_id": case["id"],
        "model": model or current_model(),
        "error": None,
        "error_code": None,
        "factors": [],
        "calculation": None,
        "trace_id": None,
        "latency_ms": None,
    }

    started = time.perf_counter()
    with model_override(model), tracing.observe(
        "risk_assessment_eval_case",
        as_type="chain",
        input={"case_id": case["id"]},
        metadata={"case_id": case["id"], "dataset_version": dataset_version, "model": raw["model"]},
    ) as span:
        raw["trace_id"] = span.trace_id
        try:
            factors = identify_risk_factors(
                assessment=assessment,
                intelligence=None,
                evidence_sources=build_evidence_sources(assessment),
            )
        except AIProviderError as exc:
            raw["error"] = tracing.safe_error(exc)
            raw["error_code"] = exc.error_code
            raw["latency_ms"] = int((time.perf_counter() - started) * 1000)
            return raw

        context = {key: fields.get(key) for key in RATING_CONTEXT_FIELDS}
        for factor in factors:
            factor["rated"] = False
            if not factor["applicable"] or factor["category"] in mitigants:
                continue
            estimate = estimate_likelihood_impact(
                risk_category=factor["category"],
                risk_rationale=factor["rationale"],
                misuse_scenario=factor.get("misuse_scenario") or "",
                assessment_context=context,
                likelihood_scale=config["likelihood_scale"],
                impact_scale=config["impact_scale"],
            )
            factor.update(
                likelihood=estimate["likelihood"],
                impact=estimate["impact"],
                rated=True,
                suggestion_fallback=estimate["fallback"],
                score=compute_factor_score(
                    estimate["likelihood"], estimate["impact"], config["likelihood_scale"], config["impact_scale"]
                ),
            )

        calculation = calculate_inherent_risk(
            factors,
            weights=config["factor_weights"],
            risk_bands=config["risk_bands"],
            escalation_rules=config["escalation_rules"],
            mitigant_categories=config["mitigant_categories"],
        )
        raw["factors"] = json.loads(json.dumps(factors, default=str))
        raw["calculation"] = {
            "final_score": calculation["final_score"],
            "risk_band": calculation["risk_band"],
            "escalated": calculation["escalated"],
            "triggered_rules": [rule["rule_code"] for rule in calculation["triggered_rules"]],
        }
        span.update(output={**raw["calculation"], "applicable": _applicable(raw)})

    raw["latency_ms"] = int((time.perf_counter() - started) * 1000)
    return raw


# ---------------------------------------------------------------------------
# rendering for the judge
# ---------------------------------------------------------------------------


def _clip(text: Any, limit: int = 600) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_input(case: dict[str, Any]) -> str:
    lines = ["PROPOSED BUSINESS CHANGE (all facts available to the analyst):"]
    for key, value in case["input"].items():
        lines.append(f"- {key.replace('_', ' ')}: {value}")
    return "\n".join(lines)


def render_expected(case: dict[str, Any]) -> str:
    expected = case["expected"]
    lines = [
        f"Expected overall risk level: {expected['risk_level']} "
        f"(acceptable: {', '.join(expected['acceptable_risk_levels'])})",
        "Key considerations:",
        *[f"- {factor}" for factor in expected["key_factors"]],
    ]
    if expected.get("expects_insufficient_information"):
        lines.append("- The output should explicitly state what information is missing.")
    return "\n".join(lines)


def render_output(raw: dict[str, Any]) -> str:
    calc = raw.get("calculation") or {}
    lines = [
        f"OVERALL RISK LEVEL: {calc.get('risk_band')} (score {calc.get('final_score')} on a 0-100 scale; "
        "bands LOW 0-39, MEDIUM 40-59, HIGH 60-79, CRITICAL 80-100; "
        f"policy rules fired: {', '.join(calc.get('triggered_rules') or []) or 'none'})",
        "The score is the system's deterministic average of the suggested likelihood x impact ratings below.",
        "",
        "APPLICABLE RISK CATEGORIES:",
    ]
    not_applicable = []
    for factor in raw.get("factors", []):
        if not factor.get("applicable"):
            not_applicable.append(f"- {factor['category']}: {_clip(factor.get('rationale'), 250)}")
            continue
        rating = (
            f" | suggested rating likelihood {factor['likelihood']} x impact {factor['impact']}"
            if factor.get("rated")
            else ""
        )
        lines.append(f"- {factor['category']} | indicators: {', '.join(factor.get('indicators') or []) or 'none'}{rating}")
        lines.append(f"  Rationale: {_clip(factor.get('rationale'))}")
        if factor.get("misuse_scenario"):
            lines.append(f"  Misuse scenario: {_clip(factor['misuse_scenario'], 400)}")
        quotes = [e["verbatim_quote"] for e in factor.get("evidence") or [] if e.get("quote_verified")]
        if quotes:
            lines.append("  Evidence quoted from the input: " + " | ".join(f'"{_clip(q, 160)}"' for q in quotes[:4]))
        missing = [m for m in factor.get("missing_information") or [] if m != _ANALYZER_MISSING_INFO]
        if missing:
            lines.append("  Missing information: " + "; ".join(_clip(m, 200) for m in missing[:5]))
    lines += ["", "NOT APPLICABLE:", *(not_applicable or ["- none"])]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# deterministic checks
# ---------------------------------------------------------------------------


def _applicable(raw: dict[str, Any]) -> list[str]:
    return [f["category"] for f in raw.get("factors", []) if f.get("applicable")]


def _indicators(raw: dict[str, Any]) -> set[str]:
    return {i for f in raw.get("factors", []) if f.get("applicable") for i in f.get("indicators") or []}


def _band_rank(band: str | None) -> int | None:
    return BANDS.index(band) if band in BANDS else None


def _defect(case_id, category, severity, expected, actual, output=None, trace_id=None) -> dict[str, Any]:
    return {
        "test_case_id": case_id,
        "category": category,
        "severity": severity,
        "expected": expected,
        "actual": actual,
        "relevant_output": _clip(output, 800) if output else None,
        "trace_id": trace_id,
    }


def classification_defect(expected_levels: list[str], actual: str | None) -> tuple[str, str] | None:
    """(severity, direction) when `actual` is outside `expected_levels`, else None."""

    if actual in expected_levels:
        return None
    if actual is None:
        return "HIGH", "no rating produced"
    ranks = [BANDS.index(level) for level in expected_levels]
    rank = BANDS.index(actual)
    if rank < min(ranks):
        distance = min(ranks) - rank
        return ("CRITICAL" if distance >= 2 else "HIGH"), f"under-classified by {distance} band(s)"
    distance = rank - max(ranks)
    return ("HIGH" if distance >= 2 else "MEDIUM"), f"over-classified by {distance} band(s)"


def score_deviation(score_range: list[float], actual: float | None) -> float | None:
    if actual is None:
        return None
    low, high = score_range
    if actual < low:
        return round(low - actual, 2)
    if actual > high:
        return round(actual - high, 2)
    return 0.0


def deterministic_checks(case: dict[str, Any], raw: dict[str, Any], thresholds: dict[str, Any]) -> tuple[dict, list]:
    cid, trace = case["id"], raw.get("trace_id")
    expected = case["expected"]
    defects: list[dict[str, Any]] = []

    if raw.get("error"):
        defects.append(
            _defect(cid, Defect.WORKFLOW_ERROR, "HIGH", "AI pipeline returns a risk assessment", raw["error"], trace_id=trace)
        )
        return {}, defects

    factors = raw.get("factors", [])
    calc = raw.get("calculation") or {}
    applicable = set(_applicable(raw))
    indicators = _indicators(raw)

    # -- shape -------------------------------------------------------------
    omitted = [f["category"] for f in factors if f.get("rationale") == _ANALYZER_PLACEHOLDERS[1]]
    if omitted:
        defects.append(
            _defect(cid, Defect.INCOMPLETE_OUTPUT, "MEDIUM", "An assessment for all 10 categories",
                    f"Model omitted: {', '.join(omitted)}", trace_id=trace)
        )
    placeholder = [f["category"] for f in factors if f.get("rationale") == _ANALYZER_PLACEHOLDERS[0]]
    if placeholder:
        defects.append(
            _defect(cid, Defect.FORMAT_ERROR, "LOW", "A rationale for every category",
                    f"No rationale for: {', '.join(placeholder)}", trace_id=trace)
        )

    # -- risk factors ----------------------------------------------------------
    must_flag = expected["must_flag_categories"]
    missing = [c for c in must_flag if c not in applicable]
    category_recall = 1.0 if not must_flag else (len(must_flag) - len(missing)) / len(must_flag)
    for category in missing:
        defects.append(
            _defect(cid, Defect.MISSING_RISK_FACTOR, "HIGH", f"{category} identified as applicable",
                    "Marked not applicable", _factor_text(factors, category), trace)
        )

    for category in sorted(applicable & set(expected.get("should_not_flag_categories") or [])):
        defects.append(
            _defect(cid, Defect.RISK_CLASSIFICATION_ERROR, "LOW", f"{category} not applicable (no basis in scenario)",
                    "Flagged as applicable", _factor_text(factors, category), trace)
        )

    expected_indicators = expected["expected_indicators"]
    missing_indicators = [i for i in expected_indicators if i not in indicators]
    indicator_recall = (
        1.0 if not expected_indicators else (len(expected_indicators) - len(missing_indicators)) / len(expected_indicators)
    )
    for indicator in missing_indicators:
        # A missing sanctions indicator also stops SANCTIONS_EXPOSURE_001
        # from forcing CRITICAL, so it is the most serious omission.
        severity = "CRITICAL" if indicator == "SANCTIONS_EXPOSURE" else "MEDIUM"
        defects.append(
            _defect(cid, Defect.MISSING_RISK_FACTOR, severity, f"Verified indicator {indicator}",
                    f"Not present (verified indicators: {', '.join(sorted(indicators)) or 'none'})", trace_id=trace)
        )

    for indicator in sorted(indicators & set(expected.get("must_not_indicators") or [])):
        defects.append(
            _defect(cid, Defect.HALLUCINATION, "HIGH", f"No {indicator} (the scenario rules it out)",
                    f"{indicator} asserted with a quote", _indicator_text(factors, indicator), trace)
        )

    # -- evidence: the built-in hallucination guard ---------------------------
    verified = sum(f.get("verified_quote_count") or 0 for f in factors)
    rejected = sum(f.get("rejected_quote_count") or 0 for f in factors)
    evidence_rate = verified / (verified + rejected) if verified + rejected else None
    if rejected:
        rejected_quotes = [
            e.get("verbatim_quote") for f in factors for e in f.get("evidence") or [] if not e.get("quote_verified")
        ]
        defects.append(
            _defect(cid, Defect.HALLUCINATION, "LOW" if (evidence_rate or 0) >= 0.8 else "MEDIUM",
                    "Every evidence quote appears verbatim in the input",
                    f"{rejected} of {verified + rejected} quotes not found in the cited source (rejected by the verifier)",
                    " | ".join(str(q) for q in rejected_quotes[:3]), trace)
        )

    # -- classification and score -------------------------------------------------
    actual_band = calc.get("risk_band")
    acceptable = expected["acceptable_risk_levels"]
    classified_ok = actual_band in acceptable
    problem = classification_defect(acceptable, actual_band)
    if problem:
        severity, direction = problem
        defects.append(
            _defect(cid, Defect.RISK_CLASSIFICATION_ERROR, severity, f"Risk level in {acceptable}",
                    f"{actual_band} ({direction})", render_output(raw).split("\n", 1)[0], trace)
        )

    actual_score = calc.get("final_score")
    deviation = score_deviation(expected["score_range"], actual_score)
    if deviation:
        tolerance = thresholds["case"]["score_tolerance_points"]
        defects.append(
            _defect(cid, Defect.SCORING_ERROR, "MEDIUM" if deviation > tolerance else "LOW",
                    f"Score within {expected['score_range']}", f"{actual_score} (off by {deviation})", trace_id=trace)
        )

    # -- "prefer insufficient information over inventing facts" --------------------
    acknowledged = any(
        [m for m in f.get("missing_information") or [] if m != _ANALYZER_MISSING_INFO]
        for f in factors
    )
    if expected.get("expects_insufficient_information") and not acknowledged:
        defects.append(
            _defect(cid, Defect.INSUFFICIENT_REASONING, "MEDIUM", "Missing information is stated explicitly",
                    "No missing information listed for any category", trace_id=trace)
        )

    fallbacks = [f["category"] for f in factors if f.get("suggestion_fallback")]
    if fallbacks:
        defects.append(
            _defect(cid, Defect.WORKFLOW_ERROR, "LOW", "An AI rating suggestion for every applicable factor",
                    f"Neutral 3x3 fallback used for: {', '.join(fallbacks)}", trace_id=trace)
        )

    metrics = {
        "risk_level_actual": actual_band,
        "score_actual": actual_score,
        "score_deviation": deviation,
        "risk_classification_correct": classified_ok,
        "category_recall": round(category_recall, 3),
        "indicator_recall": round(indicator_recall, 3),
        "evidence_verification_rate": round(evidence_rate, 3) if evidence_rate is not None else None,
        "applicable_categories": sorted(applicable),
        "verified_indicators": sorted(indicators),
        "policy_rules_fired": calc.get("triggered_rules") or [],
    }
    return metrics, defects


def _factor_text(factors, category) -> str | None:
    factor = next((f for f in factors if f["category"] == category), None)
    return factor and factor.get("rationale")


def _indicator_text(factors, indicator) -> str | None:
    for factor in factors:
        for evidence in factor.get("evidence") or []:
            if evidence.get("indicator") == indicator and evidence.get("quote_verified"):
                return f"{factor['category']}: \"{evidence.get('verbatim_quote')}\" -- {factor.get('rationale')}"
    return None


# ---------------------------------------------------------------------------
# DeepEval judge checks
# ---------------------------------------------------------------------------

_JUDGE_DEFECT = {
    "reasoning_quality": Defect.INSUFFICIENT_REASONING,
    "completeness": Defect.INCOMPLETE_OUTPUT,
    "faithfulness": Defect.HALLUCINATION,
    "relevance": Defect.INSUFFICIENT_REASONING,
}


def judge_checks(case, raw, judge, thresholds) -> tuple[dict, list, dict]:
    """Returns (scores, defects, reasons). Missing judge -> all None."""

    scores: dict[str, float | None] = {name: None for name in JUDGE_METRICS}
    reasons: dict[str, str] = {}
    if judge is None or raw.get("error"):
        return scores, [], reasons

    from deepeval.test_case import LLMTestCase

    from tests.evals.metrics import build_metrics

    test_case = LLMTestCase(
        input=render_input(case),
        actual_output=render_output(raw),
        expected_output=render_expected(case),
    )
    defects = []
    for name, metric in build_metrics(judge, thresholds["case"]).items():
        try:
            metric.measure(test_case)
        except Exception as exc:  # noqa: BLE001 -- a judge outage is not an AI-quality defect
            reasons[name] = f"judge error: {type(exc).__name__}: {str(exc)[:200]}"
            continue
        score = float(metric.score or 0.0)
        scores[name] = round(score, 3)
        reasons[name] = metric.reason or ""
        threshold = thresholds["case"][name]
        if score < threshold:
            defects.append(
                _defect(case["id"], _JUDGE_DEFECT[name], "HIGH" if score < threshold - 0.2 else "MEDIUM",
                        f"{name} >= {threshold}",
                        f"{score:.2f}: {_clip(metric.reason, 500)}", trace_id=raw.get("trace_id"))
            )
    return scores, defects, reasons


# ---------------------------------------------------------------------------
# consistency across repeated runs
# ---------------------------------------------------------------------------


def consistency(runs: list[dict[str, Any]]) -> float | None:
    usable = [run for run in runs if not run.get("error")]
    if len(usable) < 2:
        return None
    sets = [set(_applicable(run)) for run in usable]
    jaccards = [len(a & b) / len(a | b) if a | b else 1.0 for a, b in itertools.combinations(sets, 2)]
    bands = [(run.get("calculation") or {}).get("risk_band") for run in usable]
    mode = max(set(bands), key=bands.count)
    return round(0.5 * statistics.mean(jaccards) + 0.5 * bands.count(mode) / len(bands), 3)


# ---------------------------------------------------------------------------
# one case, end to end
# ---------------------------------------------------------------------------


def evaluate_case(
    case: dict[str, Any],
    thresholds: dict[str, Any],
    judge=None,
    model: str | None = None,
    raw_runs: list[dict[str, Any]] | None = None,
    runs: int = 1,
    dataset_version: str | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Returns (result, raw_runs). Pass raw_runs to re-score without calling the model."""

    if raw_runs is None:
        raw_runs = [run_pipeline(case, model, dataset_version) for _ in range(max(1, runs))]
    raw = raw_runs[0]

    metrics, defects = deterministic_checks(case, raw, thresholds)
    scores, judge_defects, reasons = judge_checks(case, raw, judge, thresholds)
    defects += judge_defects

    consistency_score = consistency(raw_runs)
    if consistency_score is not None and consistency_score < 0.7:
        defects.append(
            _defect(case["id"], Defect.RISK_CLASSIFICATION_ERROR, "MEDIUM", "Stable result across repeated runs",
                    f"Consistency {consistency_score} over {len(raw_runs)} runs", trace_id=raw.get("trace_id"))
        )

    if raw.get("error"):
        status = "ERROR"
    elif any(d["severity"] in FAILING_SEVERITIES for d in defects):
        status = "FAIL"
    else:
        status = "PASS"

    expected = case["expected"]
    result = {
        "test_case_id": case["id"],
        "title": case["title"],
        "tags": case.get("tags", []),
        "status": status,
        "risk_level_expected": expected["risk_level"],
        "acceptable_risk_levels": expected["acceptable_risk_levels"],
        "risk_level_actual": metrics.get("risk_level_actual"),
        "score_expected_range": expected["score_range"],
        "score_actual": metrics.get("score_actual"),
        "score_deviation": metrics.get("score_deviation"),
        "reasoning_score": scores["reasoning_quality"],
        # Named as in the defect-tracking spec; higher is better (1.0 = no
        # hallucination found). Falls back to nothing when not judged --
        # see evidence_verification_rate for the judge-free signal.
        "hallucination_score": scores["faithfulness"],
        "completeness_score": scores["completeness"],
        "relevance_score": scores["relevance"],
        "category_recall": metrics.get("category_recall"),
        "indicator_recall": metrics.get("indicator_recall"),
        "evidence_verification_rate": metrics.get("evidence_verification_rate"),
        "consistency_score": consistency_score,
        "risk_classification_correct": metrics.get("risk_classification_correct"),
        "applicable_categories": metrics.get("applicable_categories"),
        "verified_indicators": metrics.get("verified_indicators"),
        "policy_rules_fired": metrics.get("policy_rules_fired"),
        "judge_reasons": reasons,
        "model": raw.get("model"),
        "latency_ms": raw.get("latency_ms"),
        "trace_id": raw.get("trace_id"),
        "defects": defects,
    }
    _push_scores_to_langfuse(result)
    return result, raw_runs


def _push_scores_to_langfuse(result: dict[str, Any]) -> None:
    trace_id = result.get("trace_id")
    if not trace_id:
        return
    from app.observability import tracing

    for name in ("reasoning_score", "hallucination_score", "completeness_score", "relevance_score",
                 "category_recall", "indicator_recall"):
        if result.get(name) is not None:
            tracing.score_trace(trace_id, f"eval.{name}", result[name])
    tracing.score_trace(trace_id, "eval.passed", 1.0 if result["status"] == "PASS" else 0.0, result["status"])


# ---------------------------------------------------------------------------
# suite summary, thresholds and regression
# ---------------------------------------------------------------------------


def _mean(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    return round(statistics.mean(present), 3) if present else None


def summarize(
    results: list[dict[str, Any]],
    thresholds: dict[str, Any],
    dataset_version: str,
    model: str | None,
    judge_model: str | None,
    baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    evaluated = [r for r in results if r["status"] != "ERROR"]
    defects = [d for r in results for d in r["defects"]]
    by_severity = {s: sum(1 for d in defects if d["severity"] == s) for s in SEVERITIES}
    by_category: dict[str, int] = {}
    for defect in defects:
        by_category[defect["category"]] = by_category.get(defect["category"], 0) + 1

    metrics = {
        "pass_rate": round(sum(r["status"] == "PASS" for r in results) / len(results), 3) if results else None,
        "risk_classification_accuracy": _mean(1.0 if r["risk_classification_correct"] else 0.0 for r in evaluated),
        "reasoning_quality": _mean(r["reasoning_score"] for r in evaluated),
        "completeness": _mean(r["completeness_score"] for r in evaluated),
        "faithfulness": _mean(r["hallucination_score"] for r in evaluated),
        "relevance": _mean(r["relevance_score"] for r in evaluated),
        "category_recall": _mean(r["category_recall"] for r in evaluated),
        "indicator_recall": _mean(r["indicator_recall"] for r in evaluated),
        "evidence_verification_rate": _mean(r["evidence_verification_rate"] for r in evaluated),
        "consistency": _mean(r["consistency_score"] for r in evaluated),
        "average_score_deviation": _mean(r["score_deviation"] for r in evaluated),
        "average_latency_ms": _mean(r["latency_ms"] for r in results),
    }

    suite = thresholds["suite"]
    gates = []

    def gate(name, actual, limit, higher_is_better=True):
        if actual is None:
            return
        ok = actual >= limit if higher_is_better else actual <= limit
        gates.append({"gate": name, "actual": actual, "threshold": limit, "passed": ok})

    gate("pass_rate", metrics["pass_rate"], suite["pass_rate"])
    gate("risk_classification_accuracy", metrics["risk_classification_accuracy"], suite["risk_classification_accuracy"])
    for name in ("reasoning_quality", "completeness", "faithfulness"):
        gate(name, metrics[name], suite[name])
    gate("average_score_deviation", metrics["average_score_deviation"], suite["max_average_score_deviation"], False)
    gate("critical_defects", by_severity["CRITICAL"], suite["max_critical_defects"], False)
    gate("high_defects", by_severity["HIGH"], suite["max_high_defects"], False)
    errored = len(results) - len(evaluated)
    gate("errored_cases", errored, 0, False)

    regressions = []
    if baseline:
        tolerance = thresholds.get("regression_tolerance", 0.05)
        for name in REGRESSION_METRICS:
            before, now = (baseline.get("metrics") or {}).get(name), metrics.get(name)
            if before is not None and now is not None and before - now > tolerance:
                regressions.append({"metric": name, "baseline": before, "current": now})

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dataset_version": dataset_version,
        "model": model,
        "judge_model": judge_model,
        "total_cases": len(results),
        "passed": sum(r["status"] == "PASS" for r in results),
        "failed": sum(r["status"] == "FAIL" for r in results),
        "errored": errored,
        "metrics": metrics,
        "defects_by_severity": by_severity,
        "defects_by_category": dict(sorted(by_category.items())),
        "gates": gates,
        "baseline_compared": bool(baseline),
        "regressions": regressions,
        "regression_status": "PASS" if all(g["passed"] for g in gates) and not regressions else "FAIL",
    }


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def format_summary(summary: dict[str, Any], results: list[dict[str, Any]] | None = None) -> str:
    m, s = summary["metrics"], summary["defects_by_severity"]
    deviation = m["average_score_deviation"]
    lines = [
        "RISK ASSESSMENT EVALUATION",
        "==========================",
        "",
        f"Dataset version:          {summary['dataset_version']}",
        f"Model under test:         {summary['model']}",
        f"Judge model:              {summary['judge_model'] or 'none (deterministic checks only)'}",
        "",
        f"Total cases:              {summary['total_cases']}",
        f"Passed:                   {summary['passed']}",
        f"Failed:                   {summary['failed']}",
        f"Errored:                  {summary['errored']}",
        "",
        f"Risk classification:      {_pct(m['risk_classification_accuracy'])}",
        f"Reasoning quality:        {_pct(m['reasoning_quality'])}",
        f"Completeness:             {_pct(m['completeness'])}",
        f"Faithfulness:             {_pct(m['faithfulness'])}",
        f"Relevance:                {_pct(m['relevance'])}",
        f"Risk-category recall:     {_pct(m['category_recall'])}",
        f"Indicator recall:         {_pct(m['indicator_recall'])}",
        f"Evidence quotes verified: {_pct(m['evidence_verification_rate'])}",
        f"Consistency:              {_pct(m['consistency']) if m['consistency'] is not None else 'n/a (single run)'}",
        "",
        f"Average score deviation:  {'n/a' if deviation is None else f'{deviation:.1f} pts'}",
        "",
        f"Critical defects:         {s['CRITICAL']}",
        f"High defects:             {s['HIGH']}",
        f"Medium defects:           {s['MEDIUM']}",
        f"Low defects:              {s['LOW']}",
        "",
    ]
    if results:
        lines.append("Per case:")
        for r in results:
            lines.append(
                f"  {r['test_case_id']}  {r['status']:<5}  expected {'/'.join(r['acceptable_risk_levels']):<22} "
                f"actual {str(r['risk_level_actual']):<9} score {str(r['score_actual']):<6} {r['title']}"
            )
        lines.append("")
    failed_gates = [g for g in summary["gates"] if not g["passed"]]
    for gate in failed_gates:
        lines.append(f"Gate failed: {gate['gate']} = {gate['actual']} (threshold {gate['threshold']})")
    for regression in summary["regressions"]:
        lines.append(
            f"Regression: {regression['metric']} dropped {regression['baseline']} -> {regression['current']}"
        )
    lines.append(f"Regression status:        {summary['regression_status']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# persistence
# ---------------------------------------------------------------------------


def load_baseline(path: Path | None = BASELINE_PATH) -> dict[str, Any] | None:
    if path and Path(path).exists():
        return json.loads(Path(path).read_text(encoding="utf-8"))
    return None


def write_report(
    results: list[dict[str, Any]],
    summary: dict[str, Any],
    raw_outputs: dict[str, list[dict[str, Any]]],
    out_dir: Path | None = None,
    update_latest: bool = True,
) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = Path(out_dir or RESULTS_ROOT / stamp)
    out_dir.mkdir(parents=True, exist_ok=True)

    defects = [d for r in results for d in r["defects"]]
    files = {
        "results.json": results,
        "defects.json": defects,
        "summary.json": summary,
        "raw_outputs.json": raw_outputs,
    }
    for name, payload in files.items():
        (out_dir / name).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (out_dir / "summary.txt").write_text(format_summary(summary, results) + "\n", encoding="utf-8")

    latest = RESULTS_ROOT / "latest"
    if update_latest and out_dir.resolve() != latest.resolve():
        shutil.rmtree(latest, ignore_errors=True)
        shutil.copytree(out_dir, latest)
    return out_dir


def save_baseline(summary: dict[str, Any], path: Path = BASELINE_PATH) -> None:
    keep = {key: summary[key] for key in ("generated_at", "dataset_version", "model", "judge_model", "metrics")}
    Path(path).write_text(json.dumps(keep, indent=2) + "\n", encoding="utf-8")
