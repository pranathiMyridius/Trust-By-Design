import ManualScoringCalculator from "./ManualScoringCalculator";

/**
 * Standalone "Risk Calculator" page (its own sidebar section).
 * Not tied to any single assessment — a free-standing what-if
 * calculator using the same weights as the production risk engine.
 */
function RiskCalculatorPage() {
  return (
    <>
      <div className="page-header">
        <div>
          <h2>Risk Calculator</h2>
          <p>
            Manually score each risk dimension to see the deterministic
            weighted total, independent of any single assessment.
          </p>
        </div>
      </div>

      <ManualScoringCalculator
        riskResults={[]}
        overallScore={null}
        riskLevel={null}
      />
    </>
  );
}

export default RiskCalculatorPage;
