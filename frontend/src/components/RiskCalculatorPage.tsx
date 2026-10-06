import ManualScoringCalculator from "./ManualScoringCalculator";

/**
 * Standalone "Risk Calculator" page (its own sidebar section).
 * Not tied to any single assessment — a free-standing what-if
 * calculator using the same weights as the production risk engine.
 */
function RiskCalculatorPage({ canApplyWeights = false }: { canApplyWeights?: boolean }) {
  return (
    <>
      <div className="page-header">
        <div>
          <h2>Risk Calculator</h2>
          <p>
            Manually score each risk dimension to see the deterministic
            weighted total, independent of any single assessment. Your inputs
            are saved as a private draft; only an Admin applying the weights
            changes how new assessments are scored.
          </p>
        </div>
      </div>

      <ManualScoringCalculator
        riskResults={[]}
        overallScore={null}
        riskLevel={null}
        canApplyWeights={canApplyWeights}
      />
    </>
  );
}

export default RiskCalculatorPage;
