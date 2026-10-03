import RiskLevelBadge from "./RiskLevelBadge";

/**
 * An assessment's risk result on a list card. Usually a score plus its
 * band; but a policy rule (e.g. a verified sanctions indicator) can set a
 * band before any factor is rated, and then there is a band with no score
 * yet -- shown as such rather than hidden.
 */
export default function AssessmentScore({
  score,
  level,
}: {
  score: number | null | undefined;
  level: string | null | undefined;
}) {
  if (score != null) {
    return (
      <>
        <strong aria-label={`Risk score ${score}`}>{score}</strong>
        <RiskLevelBadge level={level} />
      </>
    );
  }

  return (
    <>
      <strong aria-label="No score yet" title="Set by a policy rule; factors not yet rated">
        —
      </strong>
      <RiskLevelBadge level={level} />
      <small style={{ display: "block", color: "#6b7280" }}>set by rule · awaiting ratings</small>
    </>
  );
}
