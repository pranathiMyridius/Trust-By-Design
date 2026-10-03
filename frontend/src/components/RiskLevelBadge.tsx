import { riskLevelInfo } from "../utils/riskLevel";

/*
 * Stage 19 (accessibility): risk levels must not be conveyed by colour
 * alone. RiskLevelBadge always renders the level as TEXT plus a distinct
 * SHAPE per level (see utils/riskLevel.ts), and keeps the colour classes
 * as an additional cue:
 *
 *   VERY LOW  ▽   LOW  ○   MEDIUM  ◆   HIGH  ▲   VERY HIGH / CRITICAL  ‼
 *
 * Accepts any casing and underscores ("very_high" -> "Very High");
 * null/empty renders the `emptyLabel` (default "Not analyzed").
 */

/** Just the shape, for places that already print the level as text. */
export function RiskLevelIcon({ level }: { level: string | null | undefined }) {
  const { key, icon } = riskLevelInfo(level);
  return (
    <span className={`risk-level-icon risk-level-icon-${key}`} aria-hidden="true">
      {icon}
    </span>
  );
}

interface RiskLevelBadgeProps {
  level: string | null | undefined;
  /**
   * Extra classes, e.g. an existing colour class like "severity high".
   * When omitted the badge uses its own colour scheme (.risk-level-*).
   */
  className?: string;
  /** Screen-reader prefix; defaults to "Risk level". */
  prefix?: string;
  emptyLabel?: string;
}

export default function RiskLevelBadge({
  level,
  className,
  prefix = "Risk level",
  emptyLabel = "Not analyzed",
}: RiskLevelBadgeProps) {
  const { key, label, icon } = riskLevelInfo(level, emptyLabel);
  const classes = className
    ? `risk-level-badge-inline ${className}`
    : `risk-level-badge risk-level-${key}`;

  return (
    <span
      className={classes}
      role="img"
      aria-label={`${prefix}: ${label}`}
      title={`${prefix}: ${label}`}
    >
      <span className="risk-level-icon" aria-hidden="true">
        {icon}
      </span>{" "}
      {label}
    </span>
  );
}

/** Shape legend for charts / matrices that use risk colours. */
export function RiskLevelLegend({
  levels = ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
}: {
  levels?: string[];
}) {
  return (
    <ul className="risk-level-legend" aria-label="Risk level legend">
      {levels.map((level) => (
        <li key={level}>
          <RiskLevelBadge level={level} />
        </li>
      ))}
    </ul>
  );
}
