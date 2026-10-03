import { useEffect, useState, type KeyboardEvent } from "react";
import {
  getExplainStatements,
  type EvidenceCategory,
  type ExplainStatement,
  type ExplainStatements,
  type StatementKind,
} from "../api/system";

// P4 (R5.4): direct evidence, extracted information, system
// interpretation, analyst commentary and assumptions, shown on every item.
const EVIDENCE_CATEGORY_LABEL: Record<EvidenceCategory, string> = {
  DIRECT_EVIDENCE: "Direct evidence",
  EXTRACTED_INFORMATION: "Extracted information",
  SYSTEM_INTERPRETATION: "System interpretation",
  ANALYST_COMMENTARY: "Analyst commentary",
  ASSUMPTION: "Assumption",
};
import "./NonFunctional.css";

/*
 * Stage 19 (Explainability): the assessment's facts, assumptions,
 * recommendations and decisions, kept visibly separate. Every automated
 * item says it's automated and whether a person has reviewed it; every
 * item names its source, author/model, time and the record it came from.
 * Recommendations carry a standing notice that they are not decisions.
 */

const KINDS: { kind: StatementKind; label: string; icon: string; help: string }[] = [
  {
    kind: "FACT",
    label: "Facts",
    icon: "■",
    help: "Information stated by the requester, evidence on file, and confirmed ratings.",
  },
  {
    kind: "ASSUMPTION",
    label: "Assumptions",
    icon: "◇",
    help: "Things taken as true without confirmation — e.g. AI-identified risks nobody has rated yet.",
  },
  {
    kind: "RECOMMENDATION",
    label: "Recommendations",
    icon: "➜",
    help: "Suggestions for reviewers. Not approvals or rejections.",
  },
  {
    kind: "DECISION",
    label: "Decisions",
    icon: "✔",
    help: "Choices made and recorded by a named person.",
  },
];

function formatDate(value: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function OriginTag({ statement }: { statement: ExplainStatement }) {
  if (statement.origin === "HUMAN") {
    return <span className="explain-tag explain-tag--human">Person</span>;
  }
  const pending = statement.review_status === "PENDING_REVIEW";
  return (
    <span className={`explain-tag ${pending ? "explain-tag--pending" : "explain-tag--reviewed"}`}>
      <span aria-hidden="true">{pending ? "⚠ " : "✓ "}</span>
      Automated — {pending ? "not yet reviewed" : "reviewed by a person"}
    </span>
  );
}

export default function ExplainabilityPanel({ assessmentId }: { assessmentId: number }) {
  const [data, setData] = useState<ExplainStatements | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [activeKind, setActiveKind] = useState<StatementKind>("FACT");
  const [category, setCategory] = useState<EvidenceCategory | "ALL">("ALL");

  useEffect(() => {
    let cancelled = false;
    getExplainStatements(assessmentId)
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError("");
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "The explanation couldn't be loaded.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  if (loading) {
    return <p role="status">Loading the explanation…</p>;
  }

  if (error || !data) {
    return (
      <p className="explain-error" role="alert">
        {error || "The explanation couldn't be loaded."} Try reopening the assessment.
      </p>
    );
  }

  const active = KINDS.find((k) => k.kind === activeKind)!;
  const statements = data.statements.filter(
    (s) => s.kind === activeKind && (category === "ALL" || s.evidence_category === category)
  );

  function onTabKeyDown(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (event.key !== "ArrowRight" && event.key !== "ArrowLeft") return;
    event.preventDefault();
    const next = (index + (event.key === "ArrowRight" ? 1 : KINDS.length - 1)) % KINDS.length;
    setActiveKind(KINDS[next].kind);
    document.getElementById(`explain-tab-${KINDS[next].kind}`)?.focus();
  }

  return (
    <section className="explain-panel" aria-labelledby={`explain-heading-${assessmentId}`}>
      <h3 id={`explain-heading-${assessmentId}`}>How this assessment was reached</h3>

      <p className="explain-notice" role="note">
        <span aria-hidden="true">ℹ </span>
        {data.advisory_notice}
        {!data.final_decision_recorded && " No final decision has been recorded yet."}
      </p>

      {data.automated_pending_review > 0 && (
        <p className="explain-pending" role="status">
          <span aria-hidden="true">⚠ </span>
          {data.automated_pending_review} automated item(s) have not been reviewed by a person yet.
        </p>
      )}

      <div className="explain-tabs" role="tablist" aria-label="Statement type">
        {KINDS.map((kind, index) => (
          <button
            key={kind.kind}
            id={`explain-tab-${kind.kind}`}
            type="button"
            role="tab"
            aria-selected={activeKind === kind.kind}
            aria-controls={`explain-tabpanel-${assessmentId}`}
            tabIndex={activeKind === kind.kind ? 0 : -1}
            className={`explain-tab explain-tab--${kind.kind.toLowerCase()}`}
            onClick={() => setActiveKind(kind.kind)}
            onKeyDown={(event) => onTabKeyDown(event, index)}
          >
            <span aria-hidden="true">{kind.icon} </span>
            {kind.label} ({data.counts[kind.kind] ?? 0})
          </button>
        ))}
      </div>

      <div
        id={`explain-tabpanel-${assessmentId}`}
        role="tabpanel"
        aria-labelledby={`explain-tab-${activeKind}`}
        className="explain-tabpanel"
      >
        <p className="explain-help">{active.help}</p>

        {data.evidence_categories && (
          <label className="explain-help" style={{ display: "block" }}>
            Evidence category:{" "}
            <select
              value={category}
              onChange={(event) => setCategory(event.target.value as EvidenceCategory | "ALL")}
              aria-label="Filter by evidence category"
            >
              <option value="ALL">All</option>
              {(Object.keys(EVIDENCE_CATEGORY_LABEL) as EvidenceCategory[]).map((key) => (
                <option key={key} value={key} title={data.evidence_categories?.[key]}>
                  {EVIDENCE_CATEGORY_LABEL[key]} ({data.evidence_category_counts?.[key] ?? 0})
                </option>
              ))}
            </select>
          </label>
        )}

        {statements.length === 0 ? (
          <p className="explain-empty">No {active.label.toLowerCase()} recorded yet.</p>
        ) : (
          <ul className="explain-list">
            {statements.map((statement, index) => (
              <li
                key={`${statement.reference?.type}-${statement.reference?.id}-${index}`}
                className={`explain-item explain-item--${statement.kind.toLowerCase()}`}
              >
                <div className="explain-item__meta">
                  <span className="explain-kind">
                    <span aria-hidden="true">{active.icon} </span>
                    {active.label.replace(/s$/, "")}
                  </span>
                  <OriginTag statement={statement} />
                  {statement.evidence_category && (
                    <span
                      className="explain-tag"
                      title={data.evidence_categories?.[statement.evidence_category]}
                      data-testid="evidence-category"
                    >
                      {EVIDENCE_CATEGORY_LABEL[statement.evidence_category]}
                    </span>
                  )}
                </div>
                <p className="explain-item__text">{statement.statement}</p>
                <p className="explain-item__trace">
                  Source: {statement.source}
                  {statement.actor && <> · By {statement.actor}</>}
                  {statement.model_version && <> · Model {statement.model_version}</>}
                  {statement.basis && <> · {statement.basis}</>}
                  {statement.recorded_at && <> · {formatDate(statement.recorded_at)}</>}
                  {statement.reference && (
                    <>
                      {" "}
                      · Record: {statement.reference.type.replace(/_/g, " ")} #
                      {statement.reference.id}
                    </>
                  )}
                </p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
