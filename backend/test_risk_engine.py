"""
Manual smoke test for the deterministic rule engine.

Mirrors the rules-only (degraded) path the workflow actually uses when
AI analysis is unavailable: RiskEngine's dimension results are
translated into the 10 canonical risk-factor categories, then scored as
an unweighted average -- see app/risk_engine/degraded.py and
app/risk_engine/scoring.py.

Run with: python test_risk_engine.py
"""

from app.risk_engine.degraded import build_rules_only_factors
from app.risk_engine.engine import RiskEngine
from app.risk_engine.scoring import (
    calculate_overall_score_from_factors,
    determine_risk_level,
)


engine = RiskEngine()

results = engine.assess(
    change_type="NEW_GEOGRAPHY",
    description="Launch fleet platform in Germany.",
    evidence="The product will collect customer location data and driver information.",
)

for result in results:
    print(
        f"{result.dimension}: "
        f"{result.score} - "
        f"{result.severity}"
    )

factors = build_rules_only_factors(results)

print()
for factor in factors:
    print(f"{factor['category']}: {factor['score']} - {factor['severity']}")

score = calculate_overall_score_from_factors(factors)
level = determine_risk_level(score)

print()
print(f"Overall Score: {score}")
print(f"Risk Level: {level}")
