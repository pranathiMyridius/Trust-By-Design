from app.risk_engine.engine import RiskEngine
from app.risk_engine.scoring import (
    calculate_overall_score,
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

score = calculate_overall_score(results)
level = determine_risk_level(score)

print()
print(f"Overall Score: {score}")
print(f"Risk Level: {level}")