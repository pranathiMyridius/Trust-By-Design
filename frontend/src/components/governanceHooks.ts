import { useEffect, useState } from "react";

import { getResidualRisk, type ResidualRiskCalculation } from "../api/assessments";

/**
 * The backend's residual result (grid lookup), refetched whenever
 * `refreshKey` changes -- e.g. after controls are edited.
 */
export function useResidualRisk(
  assessmentId: number,
  refreshKey: unknown
): { residual: ResidualRiskCalculation | null; error: string | null } {
  const [residual, setResidual] = useState<ResidualRiskCalculation | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getResidualRisk(assessmentId)
      .then((result) => {
        if (!cancelled) {
          setResidual(result);
          setError(null);
        }
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId, refreshKey]);

  return { residual, error };
}

export function formatResidualScore(value: number | null | undefined): string {
  return value == null ? "—" : value.toFixed(1);
}
