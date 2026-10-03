import { useEffect, useMemo, useRef, useState } from "react";

import {
  applyManualScoreOverride,
  getAllAuditEvents,
  getAssessment,
  getAssessmentAudit,
  logCalculatorAudit,
  ManualScoreOverrideError,
  saveManualScoreDraft,
  getCalculatorDraft,
  saveCalculatorDraft,
  RISK_CATEGORIES,
} from "../api/assessments";
import RiskLevelBadge, { RiskLevelIcon } from "./RiskLevelBadge";
import type {
  Assessment,
  AuditEvent,
  ManualScoreDraftPayload,
  RiskResult,
} from "../api/assessments";

import "./ManualScoringCalculator.css";

// How long to wait after the last edit before logging a snapshot to
// Audit History — avoids one audit event per keystroke.
const AUDIT_LOG_DEBOUNCE_MS = 2500;

// How many recent audit entries the "Recent Calculator Activity" card shows.
const RECENT_ACTIVITY_LIMIT = 2;

// Mirrors backend LOCKED_OVERRIDE_STATUSES (app/api/assessments.py) plus
// FINAL_DECISION_STATUSES (app/services/decision_lock.py) — once an
// assessment reaches one of these, its score can no longer be overridden
// from the calculator (it still works as a what-if tool).
const LOCKED_OVERRIDE_STATUSES = new Set([
  "AUDIT",
  "REJECTED",
  "REMEDIATION",
  "APPROVED",
  "APPROVED_WITH_CONDITIONS",
  "MANAGER_REJECTED",
  "CLOSED",
]);

// Fallback dimension list -- used only when there are no riskResults yet
// (e.g. the standalone Risk Calculator page, or an assessment that hasn't
// been analyzed). Once real riskResults exist, DIMENSIONS below is derived
// from whatever categories the analysis actually found applicable for THIS
// assessment, which since Stage 4 is a dynamic subset of these 10.
const FALLBACK_DIMENSIONS = RISK_CATEGORIES.map((option) => option.value);

// Weight defaults are shown to 1dp so an equal share of, say, 6 factors
// reads as "16.7%" rather than a full float expansion.
function formatWeightPercent(value: number): string {
  return Number.isInteger(value) ? String(value) : value.toFixed(1);
}

function humanizeDimension(dimension: string): string {
  const known = RISK_CATEGORIES.find((option) => option.value === dimension);
  if (known) return known.label;

  return dimension
    .toLowerCase()
    .split("_")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

// Mirrors backend/app/risk_engine/scoring.py::determine_risk_level.
function riskLevelFromScore(score: number): string {
  if (score >= 80) return "CRITICAL";
  if (score >= 60) return "HIGH";
  if (score >= 40) return "MEDIUM";
  return "LOW";
}

function clampScore(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

function clampWeightPercent(value: number): number {
  if (Number.isNaN(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

// Parses the assessment's saved manual_score_draft JSON text. Returns
// null for an empty/missing/malformed draft so callers can fall back to
// the AI-assessed defaults without special-casing it everywhere.
function parseDraft(
  raw: string | null | undefined
): ManualScoreDraftPayload | null {
  if (!raw) return null;

  try {
    const parsed = JSON.parse(raw);

    if (
      parsed &&
      typeof parsed === "object" &&
      parsed.scores &&
      parsed.included &&
      parsed.weights
    ) {
      return parsed as ManualScoreDraftPayload;
    }
  } catch {
    // Malformed/legacy draft — ignore and fall back to defaults.
  }

  return null;
}

function formatDateTime(value: string) {
  const date = new Date(value);
  return `${date.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
  })}, ${date.toLocaleTimeString("en-IN", {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

interface ManualScoringCalculatorProps {
  riskResults: RiskResult[];
  overallScore: number | null;
  riskLevel: string | null;
  // Set when embedded on a specific assessment's detail page, so audit
  // events log against — and recent activity is scoped to — that
  // assessment. Omit for the standalone Risk Calculator page, where
  // activity is assessment-less and there's nothing to override.
  assessmentId?: number | null;
  // The assessment's current pipeline status, so the "Apply as official
  // score" action can be disabled once it's Approved, Rejected, or in
  // Remediation. Only meaningful alongside assessmentId.
  assessmentStatus?: string | null;
  // The assessment's last-saved calculator draft (Assessment.manual_score_draft,
  // raw JSON text), so the calculator opens with whatever was last saved
  // instead of resetting to the AI-assessed defaults every time this
  // page is left and re-opened. Omit/null when there's no saved draft yet.
  savedDraft?: string | null;
  // Called with the updated assessment after a successful override, so
  // the parent can refresh its local copy (overall_score/risk_level).
  onScoreOverridden?: (assessment: Assessment) => void;
  // AW: the backend restricts PATCH .../manual-score (applying an
  // override as the official score) to Manager/Admin, until a dedicated
  // FCRM Analyst role exists. Defaults to true so the standalone Risk
  // Calculator page (no review-workflow concept) is unaffected.
  canApplyOverride?: boolean;
}

function ManualScoringCalculator({
  riskResults,
  overallScore,
  riskLevel,
  assessmentId = null,
  assessmentStatus = null,
  savedDraft = null,
  onScoreOverridden,
  canApplyOverride = true,
}: ManualScoringCalculatorProps) {
  const aiScoresByDimension = useMemo(() => {
    const map: Record<string, number> = {};

    riskResults.forEach((result) => {
      map[result.dimension.toUpperCase()] = result.score;
    });

    return map;
  }, [riskResults]);

  // The dimensions this calculator actually works with -- whatever the
  // AI identified for this assessment (a dynamic subset of the 10 Stage
  // 4 risk categories), deduped and uppercased. Falls back to the
  // legacy fixed 6 when there's nothing to derive from yet (no
  // riskResults), so the standalone Risk Calculator page and an
  // unanalyzed assessment still show a sensible starting set.
  const DIMENSIONS = useMemo(() => {
    if (riskResults.length === 0) {
      return FALLBACK_DIMENSIONS;
    }

    return Array.from(
      new Set(riskResults.map((result) => result.dimension.toUpperCase()))
    );
  }, [riskResults]);

  // Default weight: an equal share across whatever dimensions are in
  // play. This mirrors the backend, which scores an assessment as an
  // unweighted average of its applicable, non-excluded risk factors (see
  // backend/app/risk_engine/scoring.py::calculate_overall_score_from_factors)
  // -- there's no curated weighting scheme for the Stage 4 categories.
  //
  // Because a weighted mean with equal weights *is* the plain average,
  // an untouched calculator now reproduces the stored score exactly.
  // Weights stay editable as a deliberate what-if lever; any edit away
  // from the default is flagged as "(edited)" below.
  function getDefaultWeightPercent(): number {
    return DIMENSIONS.length > 0 ? 100 / DIMENSIONS.length : 0;
  }

  function getDimensionLabel(dimension: string): string {
    return humanizeDimension(dimension);
  }

  // The parsed draft, read once — this component remounts (via a `key`
  // prop keyed on assessment id) whenever the selected assessment
  // changes, so there's no need to react to savedDraft changing later.
  const [initialDraft] = useState(() => parseDraft(savedDraft));

  function buildDefaultScores() {
    const initial: Record<string, number> = {};

    DIMENSIONS.forEach((dimension) => {
      initial[dimension] = aiScoresByDimension[dimension] ?? 0;
    });

    return initial;
  }

  function buildDefaultIncluded() {
    const initial: Record<string, boolean> = {};

    DIMENSIONS.forEach((dimension) => {
      initial[dimension] = dimension in aiScoresByDimension;
    });

    return initial;
  }

  // Weights are stored as whole percentages (15 = 15%) since that's what
  // the input field edits; divide by 100 wherever used in the score math.
  function buildDefaultWeights() {
    const initial: Record<string, number> = {};

    DIMENSIONS.forEach((dimension) => {
      initial[dimension] = getDefaultWeightPercent();
    });

    return initial;
  }

  // "Reset to defaults" always goes back to the AI-assessed/engine
  // defaults, ignoring any saved draft — buildInitial* below is only for
  // the very first render, where a saved draft (if any) wins per-field.
  function buildInitialScores() {
    const defaults = buildDefaultScores();
    if (!initialDraft) return defaults;

    const merged = { ...defaults };
    DIMENSIONS.forEach((dimension) => {
      const value = initialDraft.scores[dimension];
      if (typeof value === "number") {
        merged[dimension] = clampScore(value);
      }
    });
    return merged;
  }

  function buildInitialIncluded() {
    const defaults = buildDefaultIncluded();
    if (!initialDraft) return defaults;

    const merged = { ...defaults };
    DIMENSIONS.forEach((dimension) => {
      const value = initialDraft.included[dimension];
      if (typeof value === "boolean") {
        merged[dimension] = value;
      }
    });
    return merged;
  }

  function buildInitialWeights() {
    const defaults = buildDefaultWeights();
    if (!initialDraft) return defaults;

    const merged = { ...defaults };
    DIMENSIONS.forEach((dimension) => {
      const value = initialDraft.weights[dimension];
      if (typeof value === "number") {
        merged[dimension] = clampWeightPercent(value * 100);
      }
    });
    return merged;
  }

  const [scores, setScores] = useState<Record<string, number>>(
    buildInitialScores
  );

  const [included, setIncluded] = useState<Record<string, boolean>>(
    buildInitialIncluded
  );

  const [weights, setWeights] = useState<Record<string, number>>(
    buildInitialWeights
  );

  const [likelihood, setLikelihood] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    DIMENSIONS.forEach((dimension) => {
      initial[dimension] = initialDraft?.likelihood?.[dimension] ?? 0;
    });
    return initial;
  });

  const [impact, setImpact] = useState<Record<string, number>>(() => {
    const initial: Record<string, number> = {};
    DIMENSIONS.forEach((dimension) => {
      initial[dimension] = initialDraft?.impact?.[dimension] ?? 0;
    });
    return initial;
  });

  // Flips true the moment the user touches anything, so the "fetch the
  // latest saved draft" reconciliation effect below never clobbers an
  // edit that's already in progress by the time that fetch resolves.
  const hasUserEditedRef = useRef(false);

  function handleScoreChange(dimension: string, raw: string) {
    hasUserEditedRef.current = true;
    setScores((current) => ({
      ...current,
      [dimension]: clampScore(Number(raw)),
    }));
  }

  function handleWeightChange(dimension: string, raw: string) {
    hasUserEditedRef.current = true;
    setWeights((current) => ({
      ...current,
      [dimension]: clampWeightPercent(Number(raw)),
    }));
  }

  function handleToggle(dimension: string) {
    hasUserEditedRef.current = true;
    setIncluded((current) => ({
      ...current,
      [dimension]: !current[dimension],
    }));
  }

  function handleReset() {
    hasUserEditedRef.current = true;
    setScores(buildDefaultScores());
    setIncluded(buildDefaultIncluded());
    setWeights(buildDefaultWeights());
  }

  function handleLikelihoodChange(dimension: string, raw: string) {
    hasUserEditedRef.current = true;
    setLikelihood((current) => ({
      ...current,
      [dimension]: Math.max(0, Math.min(5, Number(raw))),
    }));
  }

  function handleImpactChange(dimension: string, raw: string) {
    hasUserEditedRef.current = true;
    setImpact((current) => ({
      ...current,
      [dimension]: Math.max(0, Math.min(5, Number(raw))),
    }));
  }

  const { manualScore, totalWeight } = useMemo(() => {
    let weightedSum = 0;
    let weightSum = 0;

    DIMENSIONS.forEach((dimension) => {
      if (!included[dimension]) return;

      const weightFraction = weights[dimension] / 100;
      weightedSum += scores[dimension] * weightFraction;
      weightSum += weightFraction;
    });

    const score = weightSum > 0 ? weightedSum / weightSum : 0;

    return {
      manualScore: Math.round(score * 100) / 100,
      totalWeight: weightSum,
    };
  }, [scores, included, weights]);

  const manualLevel = riskLevelFromScore(manualScore);

  // An equal share is rarely a whole number (100/6 = 16.666…), and the
  // draft round-trip divides by 100 on save and multiplies back on load,
  // so compare weights with a tolerance rather than exactly -- otherwise
  // float drift alone would report an untouched calculator as edited.
  const WEIGHT_EPSILON = 0.001;

  const isEdited = DIMENSIONS.some(
    (dimension) =>
      included[dimension] !== dimension in aiScoresByDimension ||
      scores[dimension] !== (aiScoresByDimension[dimension] ?? 0) ||
      Math.abs(weights[dimension] - getDefaultWeightPercent()) > WEIGHT_EPSILON
  );

  const delta =
    overallScore != null
      ? Math.round((manualScore - overallScore) * 100) / 100
      : null;

  // Standalone Risk Calculator page has no assessment to override.
  // Otherwise, locked once the assessment has reached a terminal/closed
  // status — it stays usable as a what-if tool, just can't write back.
  const isLocked =
    assessmentId != null &&
    assessmentStatus != null &&
    LOCKED_OVERRIDE_STATUSES.has(assessmentStatus);

  /*
   * Recent Calculator Activity — the latest couple of audit entries
   * logged by this calculator, scoped to this assessment when embedded
   * on an assessment's detail page, or to assessment-less activity on
   * the standalone Risk Calculator page.
   */
  const [recentEvents, setRecentEvents] = useState<AuditEvent[]>([]);
  const [recentLoading, setRecentLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setRecentLoading(true);

    const request =
      assessmentId != null
        ? getAssessmentAudit(assessmentId)
        : getAllAuditEvents();

    request
      .then((events) => {
        if (cancelled) return;

        const calculatorEvents = events
          .filter(
            (event) =>
              (event.action === "RISK_CALCULATOR_UPDATED" ||
                event.action === "MANUAL_SCORE_OVERRIDE") &&
              event.assessment_id === assessmentId
          )
          .sort(
            (a, b) =>
              new Date(b.created_at).getTime() -
              new Date(a.created_at).getTime()
          )
          .slice(0, RECENT_ACTIVITY_LIMIT);

        setRecentEvents(calculatorEvents);
      })
      .catch((err) => console.error(err))
      .finally(() => {
        if (!cancelled) setRecentLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [assessmentId]);

  /*
   * Log a debounced snapshot to Audit History whenever the calculator's
   * inputs change. Skipped on mount (that's just the AI scores being
   * loaded in, not a user edit) and de-duped so identical consecutive
   * snapshots (e.g. toggling a checkbox off then back on to the same
   * value) don't create repeat entries.
   */
  const skipNextLogRef = useRef(true);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastLoggedRef = useRef<string | null>(null);
  const [auditStatus, setAuditStatus] = useState<
    "idle" | "pending" | "logged" | "error"
  >("idle");

  /*
   * Draft persistence — keeps the calculator's inputs from resetting to
   * the AI-assessed defaults whenever this assessment is left and
   * re-opened. Saved automatically a couple of seconds after the last
   * edit (piggybacking on the same debounce as audit logging above), and
   * also available as an explicit "Save Draft" button for anyone who
   * wants to be sure before navigating away.
   */
  const lastSavedDraftRef = useRef<string | null>(
    initialDraft ? JSON.stringify(initialDraft) : null
  );
  const [draftSaveStatus, setDraftSaveStatus] = useState<
    "idle" | "saving" | "saved" | "error"
  >("idle");

  function buildDraftPayload(): ManualScoreDraftPayload {
    const weightFractions: Record<string, number> = {};

    DIMENSIONS.forEach((dimension) => {
      weightFractions[dimension] = weights[dimension] / 100;
    });

    return {
      scores: { ...scores },
      included: { ...included },
      weights: weightFractions,
      likelihood: { ...likelihood },
      impact: { ...impact },
    };
  }

  function persistDraft(payload: ManualScoreDraftPayload) {
    const draftKey = JSON.stringify(payload);
    if (draftKey === lastSavedDraftRef.current) return;

    setDraftSaveStatus("saving");

    // Embedded on an assessment: saved on that assessment. Standalone Risk
    // Calculator page: saved per user, so the values are still there the
    // next time the page is opened.
    const save =
      assessmentId != null
        ? saveManualScoreDraft(assessmentId, payload)
        : saveCalculatorDraft(payload);

    save
      .then(() => {
        lastSavedDraftRef.current = draftKey;
        setDraftSaveStatus("saved");
      })
      .catch((err) => {
        console.error(err);
        setDraftSaveStatus("error");
      });
  }

  function handleSaveDraft() {
    // Deliberately doesn't touch the pending debounce timer — the
    // scheduled audit-log snapshot (if any) should still fire on its own
    // schedule; this just saves the draft right away instead of waiting.
    persistDraft(buildDraftPayload());
  }

  /*
   * The `savedDraft` prop can be stale — the parent app only refetches
   * its assessments list on a full page load, so if a draft was saved
   * earlier in the session and this assessment is reopened, the prop
   * this component mounts with may not reflect it yet. Confirm against
   * the backend directly on mount and reconcile if it turns out there's
   * a newer draft than what was used for the initial render. Skipped if
   * the user has already started editing by the time this resolves, so
   * an in-progress edit is never overwritten.
   */
  useEffect(() => {
    let cancelled = false;

    // The standalone page has no assessment (and so no savedDraft prop):
    // its values come from the per-user calculator draft instead.
    const loadDraft: Promise<ManualScoreDraftPayload | null> =
      assessmentId != null
        ? getAssessment(assessmentId).then((fresh) =>
            parseDraft(fresh.manual_score_draft)
          )
        : getCalculatorDraft().then((draft) =>
            parseDraft(draft ? JSON.stringify(draft) : null)
          );

    loadDraft
      .then((freshDraft) => {
        if (cancelled || hasUserEditedRef.current) return;

        if (!freshDraft) return;

        const freshKey = JSON.stringify(freshDraft);
        if (freshKey === lastSavedDraftRef.current) return;

        // A newer draft than the one this component mounted with —
        // apply it and treat it as the new baseline, without logging an
        // audit entry or re-saving it (it's already saved).
        skipNextLogRef.current = true;
        lastSavedDraftRef.current = freshKey;

        const mergedScores = buildDefaultScores();
        const mergedIncluded = buildDefaultIncluded();
        const mergedWeights = buildDefaultWeights();
        const mergedLikelihood: Record<string, number> = {};
        const mergedImpact: Record<string, number> = {};

        DIMENSIONS.forEach((dimension) => {
          const scoreValue = freshDraft.scores[dimension];
          if (typeof scoreValue === "number") {
            mergedScores[dimension] = clampScore(scoreValue);
          }

          const includedValue = freshDraft.included[dimension];
          if (typeof includedValue === "boolean") {
            mergedIncluded[dimension] = includedValue;
          }

          const weightValue = freshDraft.weights[dimension];
          if (typeof weightValue === "number") {
            mergedWeights[dimension] = clampWeightPercent(
              weightValue * 100
            );
          }

          const likelihoodValue = freshDraft.likelihood?.[dimension];
          mergedLikelihood[dimension] =
            typeof likelihoodValue === "number" ? likelihoodValue : 0;

          const impactValue = freshDraft.impact?.[dimension];
          mergedImpact[dimension] =
            typeof impactValue === "number" ? impactValue : 0;
        });

        setScores(mergedScores);
        setIncluded(mergedIncluded);
        setWeights(mergedWeights);
        setLikelihood(mergedLikelihood);
        setImpact(mergedImpact);
      })
      .catch((err) => console.error(err));

    return () => {
      cancelled = true;
    };
    // Runs once per mount (this component remounts via a `key` on
    // assessment id, so assessmentId is effectively stable here).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [assessmentId]);

  useEffect(() => {
    if (skipNextLogRef.current) {
      skipNextLogRef.current = false;
      return;
    }

    // Inputs also change when new risk results arrive (e.g. right after
    // risk identification). That is the system refreshing its defaults,
    // not a user decision, and must not be saved or written to Audit
    // History as if someone had used the calculator.
    if (!hasUserEditedRef.current) {
      return;
    }

    setAuditStatus("pending");

    if (debounceRef.current) {
      clearTimeout(debounceRef.current);
    }

    debounceRef.current = setTimeout(() => {
      // Auto-save the draft alongside the audit snapshot below, so
      // navigating away shortly after an edit doesn't lose it even if
      // "Save Draft" was never clicked. persistDraft no-ops if this
      // exact snapshot was already the last one saved.
      persistDraft(buildDraftPayload());

      const includedFactors: Record<string, number> = {};
      const factorWeights: Record<string, number> = {};

      DIMENSIONS.forEach((dimension) => {
        if (included[dimension]) {
          includedFactors[dimension] = scores[dimension];
          factorWeights[dimension] = weights[dimension] / 100;
        }
      });

      const snapshotKey = JSON.stringify({
        assessmentId,
        includedFactors,
        factorWeights,
        manualScore,
      });

      if (snapshotKey === lastLoggedRef.current) {
        setAuditStatus("logged");
        return;
      }

      logCalculatorAudit({
        assessment_id: assessmentId,
        weighted_total: manualScore,
        risk_level: manualLevel,
        included_factors: includedFactors,
        factor_weights: factorWeights,
        actor: "Manual Scoring Calculator",
      })
        .then((event) => {
          lastLoggedRef.current = snapshotKey;
          setAuditStatus("logged");
          setRecentEvents((current) =>
            [event, ...current].slice(0, RECENT_ACTIVITY_LIMIT)
          );
        })
        .catch((err) => {
          console.error(err);
          setAuditStatus("error");
        });
    }, AUDIT_LOG_DEBOUNCE_MS);

    return () => {
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
      }
    };
    // manualScore/manualLevel are derived from scores/included/weights
    // above, so depending on those three alone keeps this from
    // re-firing when only the (referentially-new) derived values change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scores, included, weights, assessmentId]);

  /*
   * "Apply as official score" — an explicit, deliberate action (never
   * automatic/debounced) that writes the calculator's current total onto
   * the assessment's real overall_score/risk_level. Only available when
   * embedded on an assessment (assessmentId set) and that assessment
   * isn't Approved/Rejected/in Remediation (see isLocked above); the
   * backend re-checks the same rule and is the source of truth.
   */
  const [overrideStatus, setOverrideStatus] = useState<
    "idle" | "pending" | "applied" | "error"
  >("idle");
  const [overrideError, setOverrideError] = useState<string | null>(null);
  // R6.7: an override of the calculated inherent risk needs a reason.
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideReasonMissing, setOverrideReasonMissing] = useState(false);

  function handleApplyOverride() {
    if (assessmentId == null || isLocked || totalWeight <= 0) return;

    if (!overrideReason.trim()) {
      setOverrideReasonMissing(true);
      return;
    }

    setOverrideReasonMissing(false);
    setOverrideStatus("pending");
    setOverrideError(null);

    const includedFactors: Record<string, number> = {};
    const factorWeights: Record<string, number> = {};

    DIMENSIONS.forEach((dimension) => {
      if (included[dimension]) {
        includedFactors[dimension] = scores[dimension];
        factorWeights[dimension] = weights[dimension] / 100;
      }
    });

    applyManualScoreOverride(assessmentId, {
      weighted_total: manualScore,
      reason: overrideReason.trim(),
      included_factors: includedFactors,
      factor_weights: factorWeights,
    })
      .then((updatedAssessment) => {
        setOverrideStatus("applied");
        setOverrideReason("");
        onScoreOverridden?.(updatedAssessment);

        // Pull the new MANUAL_SCORE_OVERRIDE entry into the activity card
        // right away rather than waiting on a re-fetch.
        return getAssessmentAudit(assessmentId)
          .then((events) => {
            const latest = events
              .filter(
                (event) =>
                  (event.action === "RISK_CALCULATOR_UPDATED" ||
                    event.action === "MANUAL_SCORE_OVERRIDE") &&
                  event.assessment_id === assessmentId
              )
              .sort(
                (a, b) =>
                  new Date(b.created_at).getTime() -
                  new Date(a.created_at).getTime()
              )
              .slice(0, RECENT_ACTIVITY_LIMIT);

            setRecentEvents(latest);
          })
          .catch(() => {
            // Non-fatal — the override itself already succeeded.
          });
      })
      .catch((err) => {
        setOverrideStatus("error");
        setOverrideError(
          err instanceof ManualScoreOverrideError
            ? err.message
            : "Couldn't apply the override — check the backend is running."
        );
      });
  }

  return (
    <>
      <section className="content-card recent-activity-card">
        <div className="card-header">
          <h3>Recent Calculator Activity</h3>
          <p>
            {assessmentId != null
              ? "The latest calculator changes logged against this assessment."
              : "The latest calculator changes logged from the standalone Risk Calculator."}
          </p>
        </div>

        <div className="recent-activity-flex">
          {recentLoading ? (
            <div className="empty-state">Loading recent activity…</div>
          ) : recentEvents.length === 0 ? (
            <div className="empty-state">
              No calculator changes logged yet.
            </div>
          ) : (
            recentEvents.map((event) => (
              <div className="recent-activity-item" key={event.id}>
                <div className="recent-activity-top">
                  <span
                    className={`severity ${(
                      event.new_status || ""
                    ).toLowerCase()}`}
                  >
                    {event.new_status && <RiskLevelIcon level={event.new_status} />}{" "}
                    {event.new_status || "—"}
                  </span>

                  <span className="recent-activity-time">
                    {formatDateTime(event.created_at)}
                  </span>
                </div>

                <p className="recent-activity-details">{event.details}</p>

                <span className="recent-activity-actor">
                  {event.actor || "System"}
                </span>
              </div>
            ))
          )}
        </div>
      </section>

      <section className="content-card manual-score-card">
        <div className="card-header">
          <h3>Manual Scoring Calculator</h3>

          <p>
            Adjust any dimension's score and weight below to recalculate
            the weighted total. The dimensions shown are whichever risk
            categories the AI identified as applicable for this
            assessment, and weights default to an equal share among them
            — matching how the assessment's own score is calculated, so
            an untouched calculator reproduces it. Each field shows its
            default alongside the editable value; change a weight to
            explore a what-if that deliberately departs from the
            methodology.
          </p>
        </div>

        <div className="manual-score-body">
          <div className="manual-score-factors">
            <div className="manual-score-row manual-score-row-head">
              <span>Risk factor</span>
              <span>Include</span>
              <span>Score (0–100)</span>
              <span>Weight (%)</span>
              <span>Likelihood</span>
              <span>Impact</span>
              <span>Contribution</span>
            </div>

            {DIMENSIONS.map((dimension) => {
              const defaultWeightPercent = getDefaultWeightPercent();
              const dimensionLabel = getDimensionLabel(dimension);
              const weightPercent = weights[dimension];
              const weightFraction = weightPercent / 100;
              const score = scores[dimension];
              const isIncluded = included[dimension];
              const contribution = isIncluded
                ? score * weightFraction
                : 0;
              const hasAiScore = dimension in aiScoresByDimension;
              const isWeightEdited =
                Math.abs(weightPercent - defaultWeightPercent) >
                WEIGHT_EPSILON;

              return (
                <div className="manual-score-row" key={dimension}>
                  <div className="manual-score-dimension">
                    <strong>{dimensionLabel}</strong>

                    {!hasAiScore && (
                      <span className="manual-score-note">
                        No AI-assessed score yet
                      </span>
                    )}
                  </div>

                  <label className="manual-score-toggle">
                    <input
                      type="checkbox"
                      checked={isIncluded}
                      onChange={() => handleToggle(dimension)}
                      aria-label={`Include ${dimensionLabel} in weighted score`}
                    />
                  </label>

                  <div className="manual-score-input">
                    <input
                      id={`manual-score-${dimension}`}
                      type="number"
                      min={0}
                      max={100}
                      step={1}
                      value={score}
                      disabled={!isIncluded}
                      onChange={(e) =>
                        handleScoreChange(dimension, e.target.value)
                      }
                      aria-label={`${dimensionLabel} score`}
                    />
                  </div>

                  <div className="manual-score-weight-field">
                    <div className="manual-score-input">
                      <input
                        id={`manual-weight-${dimension}`}
                        type="number"
                        min={0}
                        max={100}
                        step={1}
                        value={weightPercent}
                        disabled={!isIncluded}
                        onChange={(e) =>
                          handleWeightChange(dimension, e.target.value)
                        }
                        aria-label={`${dimensionLabel} weight percent`}
                      />
                    </div>

                    <span className="manual-score-default-weight">
                      Default: {formatWeightPercent(defaultWeightPercent)}%
                      {isWeightEdited ? " (edited)" : ""}
                    </span>
                  </div>

                  <div className="manual-score-input">
                    <input
                      id={`manual-likelihood-${dimension}`}
                      type="number"
                      min={0}
                      max={5}
                      step={1}
                      value={likelihood[dimension] || 0}
                      disabled={!isIncluded}
                      onChange={(e) =>
                        handleLikelihoodChange(dimension, e.target.value)
                      }
                      aria-label={`${dimensionLabel} likelihood (1-5)`}
                      placeholder="0"
                    />
                  </div>

                  <div className="manual-score-input">
                    <input
                      id={`manual-impact-${dimension}`}
                      type="number"
                      min={0}
                      max={5}
                      step={1}
                      value={impact[dimension] || 0}
                      disabled={!isIncluded}
                      onChange={(e) =>
                        handleImpactChange(dimension, e.target.value)
                      }
                      aria-label={`${dimensionLabel} impact (1-5)`}
                      placeholder="0"
                    />
                  </div>

                  <div className="manual-score-contribution">
                    <strong>{contribution.toFixed(2)}</strong>

                    <div className="manual-score-bar">
                      <span
                        style={{
                          width: `${Math.min(
                            100,
                            contribution
                          )}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="manual-score-result">
            <span className="manual-score-result-label">
              Manual weighted total
            </span>

            <strong className="manual-score-result-value">
              {totalWeight > 0 ? manualScore.toFixed(2) : "—"}
            </strong>

            {totalWeight > 0 ? (
              <RiskLevelBadge
                level={manualLevel}
                className={`severity ${manualLevel.toLowerCase()}`}
              />
            ) : (
              <span className="severity">No factors included</span>
            )}

            {overallScore != null && (
              <p className="manual-score-delta">
                AI-assessed score: <strong>{overallScore}</strong> (
                {riskLevel ?? "—"})
                {delta !== null && (
                  <>
                    {" "}
                    &middot; delta{" "}
                    <strong
                      className={
                        delta > 0
                          ? "delta-up"
                          : delta < 0
                          ? "delta-down"
                          : undefined
                      }
                    >
                      {delta > 0 ? "+" : ""}
                      {delta}
                    </strong>
                  </>
                )}
              </p>
            )}

            {assessmentId != null && canApplyOverride && !isLocked && (
              <div className="manual-score-override-reason">
                <label htmlFor="manual-score-override-reason">
                  Reason for overriding the calculated score (required)
                </label>
                <textarea
                  id="manual-score-override-reason"
                  rows={2}
                  value={overrideReason}
                  onChange={(event) => {
                    setOverrideReason(event.target.value);
                    if (event.target.value.trim()) setOverrideReasonMissing(false);
                  }}
                  aria-invalid={overrideReasonMissing}
                  aria-describedby={
                    overrideReasonMissing ? "manual-score-override-reason-error" : undefined
                  }
                  style={{ width: "100%", boxSizing: "border-box" }}
                />
                {overrideReasonMissing && (
                  <p
                    id="manual-score-override-reason-error"
                    className="manual-score-override-status error"
                    role="alert"
                  >
                    Enter a reason before applying the override.
                  </p>
                )}
              </div>
            )}

            <div className="manual-score-actions">
              <button
                type="button"
                className="pagination-button"
                onClick={handleReset}
              >
                Reset to defaults
              </button>

              <button
                type="button"
                className="pagination-button save-draft-button"
                onClick={handleSaveDraft}
                disabled={draftSaveStatus === "saving"}
              >
                {draftSaveStatus === "saving" ? "Saving…" : "Save draft"}
              </button>

              {assessmentId != null && canApplyOverride && (
                <button
                  type="button"
                  className="pagination-button apply-override-button"
                  onClick={handleApplyOverride}
                  disabled={
                    isLocked ||
                    totalWeight <= 0 ||
                    overrideStatus === "pending"
                  }
                  title={
                    isLocked
                      ? `This assessment is ${assessmentStatus} — its score can no longer be manually overridden.`
                      : undefined
                  }
                >
                  {overrideStatus === "pending"
                    ? "Applying…"
                    : "Apply as official score"}
                </button>
              )}
              {assessmentId != null && !canApplyOverride && (
                <span style={{ color: "#667085", fontSize: 13, alignSelf: "center" }}>
                  Only an FCRM Analyst, Manager or Admin can apply this as the official score.
                </span>
              )}
            </div>

            {isEdited && (
              <p className="manual-score-warning">
                {assessmentId != null
                  ? "These are manual what-if values — they're auto-saved as a draft so they won't reset if you navigate away, but review actions still use the calculated score unless you record an override with “Apply as official score”."
                  : "These are manual what-if values — they're auto-saved for you, so they'll still be here the next time you open the Risk Calculator."}
              </p>
            )}

            {draftSaveStatus !== "idle" && (
              <p className="manual-score-draft-status">
                {draftSaveStatus === "saving" && "Saving draft…"}
                {draftSaveStatus === "saved" && "✓ Draft saved"}
                {draftSaveStatus === "error" &&
                  "Couldn't save the draft — check the backend is running."}
              </p>
            )}

            {assessmentId != null && isLocked && (
              <p className="manual-score-locked-note">
                This assessment is <strong>{assessmentStatus}</strong>, so
                its official score is locked. The calculator still works
                as a what-if tool, but changes here can no longer
                overwrite the record.
              </p>
            )}

            {auditStatus !== "idle" && (
              <p className="manual-score-audit-status" role="status" aria-live="polite">
                {auditStatus === "pending" &&
                  "Logging change to Audit History…"}
                {auditStatus === "logged" && "✓ Logged to Audit History"}
                {auditStatus === "error" &&
                  "Couldn't log to Audit History — check the backend is running."}
              </p>
            )}

            {overrideStatus === "applied" && (
              <p className="manual-score-override-status success" role="status">
                ✓ Recorded as an override of the calculated inherent risk.
                The calculated score is kept alongside it in the audit trail.
              </p>
            )}

            {overrideStatus === "error" && overrideError && (
              <p className="manual-score-override-status error" role="alert">
                {overrideError}
              </p>
            )}
          </div>
        </div>
      </section>
    </>
  );
}

export default ManualScoringCalculator;
