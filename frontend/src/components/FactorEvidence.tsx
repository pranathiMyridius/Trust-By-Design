import type { EvidenceStatus, RiskFactor } from "../api/assessments";

// How each deterministic evidence status reads to an analyst. None of
// these is a rating: only an analyst's likelihood x impact scores a factor.
const EVIDENCE_STATUS_DISPLAY: Record<
  EvidenceStatus,
  { label: string; icon: string; color: string; background: string; note: string }
> = {
  EVIDENCE_FOUND: {
    label: "Evidence verified",
    icon: "✓",
    color: "#166534",
    background: "#dcfce7",
    note: "At least one quote was found word for word in its cited source.",
  },
  INSUFFICIENT_EVIDENCE: {
    label: "Insufficient evidence",
    icon: "?",
    color: "#92400e",
    background: "#fef3c7",
    note:
      "No supporting quote was found. This is unknown, not low risk: it " +
      "stays unrated until an analyst rates it or excludes it with a reason.",
  },
  CONFLICTING_EVIDENCE: {
    label: "Conflicting evidence",
    icon: "⇄",
    color: "#9a3412",
    background: "#ffedd5",
    note: "The sources contradict each other on this category. Review before rating.",
  },
  NOT_VERIFIED: {
    label: "Quotes not verified",
    icon: "✕",
    color: "#991b1b",
    background: "#fee2e2",
    note:
      "Quotes were given but none appears in its cited source, so they " +
      "support nothing. Treat this factor as unsupported.",
  },
  NOT_APPLICABLE: {
    label: "Not applicable",
    icon: "○",
    color: "#374151",
    background: "#f3f4f6",
    note: "The AI found this category does not apply.",
  },
};

export function EvidenceStatusBadge({ status }: { status: EvidenceStatus | null }) {
  if (!status) return null;
  const display = EVIDENCE_STATUS_DISPLAY[status];
  if (!display) return null;

  return (
    <span
      title={display.note}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        padding: "2px 8px",
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        color: display.color,
        background: display.background,
      }}
    >
      <span aria-hidden="true">{display.icon}</span>
      {display.label}
    </span>
  );
}

function sourceLabel(record: RiskFactor["evidence"][number]): string {
  if (record.source_type === "ASSESSMENT_FIELD") {
    return `Intake field: ${(record.source_label ?? "").replace(/_/g, " ")}`;
  }
  if (record.source_type === "DOCUMENT") {
    const version = record.document_version ? ` v${record.document_version}` : "";
    const page = record.page ? `, page ${record.page}` : "";
    return `${record.source_label ?? "Document"}${version}${page}`;
  }
  return record.source_id || "Unknown source";
}

const VERIFICATION_TEXT: Record<string, string> = {
  NOT_FOUND: "not found in the cited source",
  UNKNOWN_SOURCE: "cites a source that does not exist",
  TOO_SHORT: "too short to prove anything",
};

export function FactorEvidence({
  factor,
  indicatorLabel,
}: {
  factor: RiskFactor;
  indicatorLabel: (code: string) => string;
}) {
  if (!factor.evidence_status || factor.evidence_status === "NOT_APPLICABLE") {
    return null;
  }

  const verified = factor.evidence.filter((record) => record.quote_verified);
  const rejected = factor.evidence.filter((record) => !record.quote_verified);
  const display = EVIDENCE_STATUS_DISPLAY[factor.evidence_status];

  return (
    <div
      style={{
        borderLeft: `3px solid ${display.color}`,
        padding: "6px 10px",
        margin: "8px 0",
        background: "#fafafa",
      }}
    >
      <p className="risk-reason" style={{ margin: "0 0 6px" }}>
        <strong>Evidence: </strong>
        {display.note}
      </p>

      {verified.length > 0 && (
        <ul style={{ margin: "0 0 6px", paddingLeft: 18 }}>
          {verified.map((record, index) => (
            <li key={`v-${index}`} className="risk-reason" style={{ margin: "2px 0" }}>
              <span style={{ color: "#166534" }} aria-label="verified">✓ </span>
              “{record.verbatim_quote}”
              <span style={{ color: "#6b7280" }}>
                {" "}— {sourceLabel(record)}
                {record.indicator && ` · supports ${indicatorLabel(record.indicator)}`}
              </span>
            </li>
          ))}
        </ul>
      )}

      {rejected.length > 0 && (
        <details style={{ margin: "0 0 6px" }}>
          <summary className="risk-reason" style={{ color: "#991b1b", cursor: "pointer" }}>
            {rejected.length} quote{rejected.length === 1 ? "" : "s"} rejected by verification
          </summary>
          <ul style={{ margin: "4px 0", paddingLeft: 18 }}>
            {rejected.map((record, index) => (
              <li key={`r-${index}`} className="risk-reason" style={{ margin: "2px 0" }}>
                <span style={{ color: "#991b1b" }} aria-label="rejected">✕ </span>
                <s>“{record.verbatim_quote}”</s>
                <span style={{ color: "#6b7280" }}>
                  {" "}— {VERIFICATION_TEXT[record.verification] ?? record.verification}
                  {record.source_id && ` (${record.source_id})`}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {factor.rejected_indicators.length > 0 && (
        <p className="risk-reason" style={{ margin: "0 0 6px", color: "#991b1b" }}>
          <strong>Indicators refused: </strong>
          {factor.rejected_indicators
            .map((item) => `${indicatorLabel(item.indicator)} (${item.reason})`)
            .join("; ")}
        </p>
      )}

      {factor.missing_information.length > 0 && (
        <p className="risk-reason" style={{ margin: 0 }}>
          <strong>Information needed: </strong>
          {factor.missing_information.join("; ")}
        </p>
      )}
    </div>
  );
}
