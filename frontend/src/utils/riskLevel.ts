/*
 * Stage 19 (accessibility): shared risk-level normalisation used by
 * RiskLevelBadge -- a distinct shape per level so risk is never shown
 * by colour alone:
 *
 *   VERY LOW  ▽   LOW  ○   MEDIUM  ◆   HIGH  ▲   VERY HIGH / CRITICAL  ‼
 */

export type RiskKey =
  | "very_low"
  | "low"
  | "medium"
  | "high"
  | "very_high"
  | "critical"
  | "unknown";

const RISK_ICONS: Record<RiskKey, string> = {
  very_low: "▽",
  low: "○",
  medium: "◆",
  high: "▲",
  very_high: "‼",
  critical: "‼",
  unknown: "–",
};

function toTitleCase(value: string): string {
  return value
    .toLowerCase()
    .split(" ")
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export function riskLevelInfo(
  level: string | null | undefined,
  emptyLabel = "Not analyzed"
): { key: RiskKey; label: string; icon: string } {
  const raw = (level ?? "").toString().trim();
  if (!raw) {
    return { key: "unknown", label: emptyLabel, icon: RISK_ICONS.unknown };
  }

  const normalised = raw.toLowerCase().replace(/[\s-]+/g, "_");
  const key: RiskKey =
    normalised in RISK_ICONS ? (normalised as RiskKey) : "unknown";

  return {
    key,
    label: toTitleCase(raw.replace(/_/g, " ")),
    icon: RISK_ICONS[key],
  };
}
