import type { ReactNode } from "react";

import type { DecisionPackage } from "../api/assessments";
import { ValueComparisonTable, VoteHistoryList } from "./GovernanceRecordPanels";
import RiskLevelBadge from "./RiskLevelBadge";

/**
 * R12.2: everything the committee receives -- executive summary, inherent
 * and residual risk, main risk drivers, control gaps, evidence, analyst
 * recommendation, challenge findings, conditions, open issues and the
 * assessment history -- as readable sections rather than raw JSON.
 */

function text(value: unknown): string {
  return value == null || value === "" ? "—" : String(value);
}

function humanize(value: unknown): string {
  return text(value).replace(/_RISK$/, "").replace(/_/g, " ").toLowerCase();
}

function RiskLine({ label, risk }: { label: string; risk: Record<string, unknown> | undefined }) {
  const band = risk?.band ? String(risk.band) : null;
  return (
    <p style={{ margin: "4px 0" }}>
      <strong>{label}:</strong> {band ? <RiskLevelBadge level={band} /> : "Not yet determined"}
      {risk?.score != null && <> — score {Number(risk.score).toFixed(1)}</>}
      {Boolean(risk?.overridden) && (
        <>
          {" "}
          (analyst override of the calculated {text(risk?.calculated_band)}
          {risk?.calculated_score != null ? ` ${Number(risk.calculated_score).toFixed(1)}` : ""}:{" "}
          {text(risk?.override_reason)})
        </>
      )}
      {Boolean(risk?.provisional) && <> — provisional</>}
    </p>
  );
}

function Section({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <section style={{ marginTop: 12 }}>
      <h4 style={{ margin: "0 0 4px" }}>
        {title}
        {count != null && ` (${count})`}
      </h4>
      {children}
    </section>
  );
}

function None({ label = "None." }: { label?: string }) {
  return <p style={{ margin: 0 }}>{label}</p>;
}

export default function DecisionPackageView({ pkg }: { pkg: DecisionPackage }) {
  const draft = pkg.draft;
  const drivers = pkg.main_risk_drivers ?? [];
  const gaps = draft?.risk_gaps ?? [];
  const evidence = draft?.evidence_references ?? [];
  const controls = draft?.mapped_controls ?? [];
  const recommended = draft?.recommended_conditions ?? [];

  return (
    <div>
      {draft ? (
        <>
          <p style={{ color: "#667085", fontSize: 13, margin: 0 }}>
            Assessment draft v{draft.version}, generated {new Date(draft.generated_at).toLocaleString()} (
            {draft.generation_method}). Regenerate the draft if the assessment changed after this.
          </p>

          <Section title="Executive summary">
            <p style={{ margin: 0 }}>{text(draft.executive_summary)}</p>
          </Section>

          <Section title="Inherent and residual risk">
            <RiskLine label="Inherent" risk={draft.inherent_risk} />
            <RiskLine label="Residual" risk={draft.residual_risk} />
          </Section>
        </>
      ) : (
        <p>No assessment draft available yet.</p>
      )}

      <Section title="Main risk drivers" count={drivers.length}>
        {drivers.length === 0 ? (
          <None label="No rated risk factors yet." />
        ) : (
          <ul style={{ margin: 0 }}>
            {drivers.map((driver) => (
              <li key={driver.category}>
                <strong>{humanize(driver.category)}</strong>{" "}
                {driver.severity && <RiskLevelBadge level={driver.severity} />} — likelihood{" "}
                {text(driver.likelihood)} × impact {text(driver.impact)} ={" "}
                {driver.score != null ? driver.score.toFixed(1) : "—"}
                {driver.rationale && <div style={{ color: "#475467", fontSize: 13 }}>{driver.rationale}</div>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      {draft && (
        <>
          <Section title="Mapped controls" count={controls.length}>
            {controls.length === 0 ? (
              <None label="No controls mapped." />
            ) : (
              <ul style={{ margin: 0 }}>
                {controls.map((control, index) => (
                  <li key={index}>
                    {humanize(control.control_type)} — design {humanize(control.design_adequacy)}, operating{" "}
                    {humanize(control.operating_effectiveness)}
                    {control.has_evidence ? "" : " (no supporting evidence)"}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Control gaps" count={gaps.length}>
            {gaps.length === 0 ? (
              <None />
            ) : (
              <ul style={{ margin: 0 }}>
                {gaps.map((gap, index) => (
                  <li key={index}>
                    <strong>{humanize(gap.gap_type)}</strong>: {text(gap.description)}
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Evidence" count={evidence.length}>
            {evidence.length === 0 ? (
              <None label="No documents on file." />
            ) : (
              <ul style={{ margin: 0 }}>
                {evidence.map((item, index) => (
                  <li key={index}>
                    {text(item.filename)} ({humanize(item.document_type)})
                  </li>
                ))}
              </ul>
            )}
          </Section>

          <Section title="Analyst recommendation">
            <p style={{ margin: 0 }}>{text(draft.analyst_recommendation)}</p>
            {draft.recommendation_notice && (
              <p style={{ color: "#667085", fontSize: 13, margin: "4px 0 0" }}>{draft.recommendation_notice}</p>
            )}
          </Section>
        </>
      )}

      {/* R10.4 / R6.7: calculated values never change; human values sit beside them. */}
      <Section title="Calculated values and human overrides" count={(pkg.value_comparisons ?? []).length}>
        <ValueComparisonTable comparisons={pkg.value_comparisons ?? []} />
      </Section>

      {/* R11: the mandatory challenge-review sign-off. */}
      <Section title="Challenge review sign-off">
        {pkg.challenge_signoff ? (
          <p style={{ margin: 0 }}>
            Signed off by <strong>{pkg.challenge_signoff.reviewer}</strong> on{" "}
            {pkg.challenge_signoff.completed_at ? new Date(pkg.challenge_signoff.completed_at).toLocaleString() : "—"} —{" "}
            {pkg.challenge_signoff.outcome === "NO_TRIGGERS_FIRED" ? "no challenge trigger fired" : "findings addressed"}:{" "}
            {pkg.challenge_signoff.reason}
          </p>
        ) : (
          <None label="Not signed off." />
        )}
      </Section>

      {/* R12.6: every vote, superseded ones kept. */}
      <Section title="Committee votes" count={pkg.committee_votes.filter((v) => v.is_current !== false).length}>
        <VoteHistoryList votes={pkg.committee_votes} />
      </Section>

      <Section title="Challenge findings" count={pkg.challenge_findings.length}>
        {pkg.challenge_findings.length === 0 ? (
          <None />
        ) : (
          <ul style={{ margin: 0 }}>
            {pkg.challenge_findings.map((finding, index) => (
              <li key={index}>
                <RiskLevelBadge level={String(finding.severity)} /> {text(finding.description)} —{" "}
                {humanize(finding.resolution_status)}
                {finding.accepted_reason ? ` (accepted: ${text(finding.accepted_reason)})` : ""}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Conditions" count={pkg.committee_conditions.length + recommended.length}>
        {pkg.committee_conditions.length === 0 && recommended.length === 0 ? (
          <None />
        ) : (
          <ul style={{ margin: 0 }}>
            {recommended.map((condition, index) => (
              <li key={`recommended-${index}`}>Recommended: {condition}</li>
            ))}
            {pkg.committee_conditions.map((condition) => (
              <li key={condition.id}>
                Committee: {condition.description} — owner {condition.owner}, due {condition.due_date},{" "}
                {humanize(condition.status)}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Open issues" count={pkg.open_issues.length}>
        {pkg.open_issues.length === 0 ? (
          <None />
        ) : (
          <ul style={{ margin: 0 }}>
            {pkg.open_issues.map((issue, index) => (
              <li key={index}>{issue}</li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Assessment history" count={pkg.assessment_history.length}>
        <ul style={{ maxHeight: 180, overflowY: "auto", margin: 0 }}>
          {pkg.assessment_history.map((event, index) => (
            <li key={index}>
              {new Date(String(event.created_at)).toLocaleString()} — {humanize(event.action)} by{" "}
              {text(event.actor)}
              {event.details ? `: ${String(event.details)}` : ""}
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}
