import { useCallback, useEffect, useId, useState } from "react";

import {
  activateMethodology,
  attestSnapshot,
  cloneMethodology,
  createFromDefaults,
  getActiveRules,
  getMethodologyConfig,
  listMethodologies,
  listSnapshots,
  reloadSnapshots,
  saveResidualGrid,
  saveRules,
  type Methodology,
  type MethodologyConfig,
  type PolicyRule,
  type ReferenceSnapshot,
  type ResidualGrid,
} from "../api/governance";
import { friendlyError } from "../utils/errorMessages";
import ChallengeTriggerPanel from "./ChallengeTriggerPanel";
import ScoringConfigPanel from "./ScoringConfigPanel";

const RATINGS = ["WEAK", "PARTIAL", "EFFECTIVE"] as const;
const RATING_LABEL: Record<string, string> = {
  WEAK: "Weak / absent",
  PARTIAL: "Partially effective",
  EFFECTIVE: "Effective / strong",
};

function when(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString() : "—";
}

function shortHash(value: string | null | undefined): string {
  return value ? `${value.slice(0, 19)}…` : "—";
}

function conditionText(rule: PolicyRule): string {
  const c = rule.condition ?? {};
  if (c.indicator) return `Indicator ${c.indicator.replace(/_/g, " ").toLowerCase()} on a verified quote`;
  if (c.jurisdiction_tiers) return `Country designated ${c.jurisdiction_tiers.join(" / ")} in an attested list`;
  if (c.categories) {
    const threshold = c.min_factor_band
      ? ` rated ${c.min_factor_band} or above`
      : c.min_factor_score != null
      ? ` scored ${c.min_factor_score}+`
      : "";
    return `${c.categories.map((x) => x.replace(/_RISK$/, "").replace(/_/g, " ").toLowerCase()).join(", ")}${threshold}`;
  }
  return "—";
}

/** A reason prompt shared by every change on this page. */
function ReasonForm({
  label,
  submitLabel,
  minLength = 1,
  onSubmit,
  onCancel,
  busy,
}: {
  label: string;
  submitLabel: string;
  minLength?: number;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
  busy: boolean;
}) {
  const [reason, setReason] = useState("");
  const id = useId();
  const tooShort = reason.trim().length < minLength;

  return (
    <form
      className="form-group gov-reason-form"
      onSubmit={(event) => {
        event.preventDefault();
        if (!tooShort) onSubmit(reason.trim());
      }}
    >
      <label htmlFor={id}>{label}</label>
      <textarea id={id} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} required />
      {minLength > 1 && (
        <small className="gov-hint">At least {minLength} characters.</small>
      )}
      <div className="gov-form-actions">
        <button type="submit" className="primary-button" disabled={busy || tooShort}>
          {busy ? "Saving…" : submitLabel}
        </button>
        <button type="button" className="secondary-button" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
      </div>
    </form>
  );
}

export default function GovernancePage() {
  const [snapshots, setSnapshots] = useState<ReferenceSnapshot[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [methodologies, setMethodologies] = useState<Methodology[]>([]);
  const [activeName, setActiveName] = useState<string>("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selected, setSelected] = useState<(Methodology & { config: MethodologyConfig }) | null>(null);
  const [builtinRules, setBuiltinRules] = useState<PolicyRule[]>([]);

  const [draftRules, setDraftRules] = useState<PolicyRule[]>([]);
  const [draftGrid, setDraftGrid] = useState<ResidualGrid | null>(null);

  // Which inline form is open: "attest:<id>", "clone:<id>", "activate:<id>",
  // "rules", "grid", "create".
  const [openForm, setOpenForm] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const fetchAll = useCallback(
    () => Promise.all([listSnapshots(showHistory), listMethodologies(), getActiveRules()]),
    [showHistory]
  );

  const apply = useCallback(
    ([snaps, methods, active]: Awaited<ReturnType<typeof fetchAll>>) => {
      setSnapshots(snaps);
      setMethodologies(methods);
      setActiveName(
        active.methodology_id == null ? "Built-in defaults (no methodology configured)" : active.methodology_name
      );
      if (active.methodology_id == null) setBuiltinRules(active.rules);
      setError("");
    },
    []
  );

  const load = useCallback(async () => {
    try {
      apply(await fetchAll());
    } catch (err) {
      setError(friendlyError(err, "Governance settings couldn't be loaded."));
    }
  }, [apply, fetchAll]);

  useEffect(() => {
    let cancelled = false;
    fetchAll()
      .then((result) => !cancelled && apply(result))
      .catch((err) => !cancelled && setError(friendlyError(err, "Governance settings couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, [apply, fetchAll]);

  useEffect(() => {
    if (selectedId == null) return;
    let cancelled = false;
    getMethodologyConfig(selectedId)
      .then((result) => {
        if (cancelled) return;
        setSelected(result);
        setDraftRules(result.config.escalation_rules.map((rule) => ({ ...rule })));
        setDraftGrid(JSON.parse(JSON.stringify(result.config.residual_grid)));
      })
      .catch((err) => !cancelled && setError(friendlyError(err, "That methodology couldn't be loaded.")));
    return () => {
      cancelled = true;
    };
  }, [selectedId, methodologies]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      // An action may return its own, more specific success message.
      const result = await action();
      setMessage(typeof result === "string" ? result : success);
      setOpenForm(null);
      await load();
    } catch (err) {
      setError(friendlyError(err, "That change couldn't be saved."));
    } finally {
      setBusy(false);
    }
  }

  const bandOrder = selected?.config.risk_bands
    ?.slice()
    .sort((a, b) => a.min - b.min)
    .map((band) => band.name) ?? ["LOW", "MEDIUM", "HIGH", "CRITICAL"];

  const rulesChanged =
    selected != null &&
    JSON.stringify(draftRules) !== JSON.stringify(selected.config.escalation_rules);
  const gridChanged =
    selected != null && JSON.stringify(draftGrid) !== JSON.stringify(selected.config.residual_grid);

  return (
    <div className="nfr-page gov-page">
      <div className="page-header gov-header">
        <div>
          <h2>Risk Governance</h2>
          <p>
            Reference lists, methodology versions, policy rules and the residual grid. A
            methodology that has produced a result is locked; change it by cloning.
          </p>
        </div>
        <button type="button" className="secondary-button" onClick={load} disabled={busy}>
          Refresh
        </button>
      </div>

      {error && (
        <p className="nfr-banner nfr-banner--error" role="alert">
          <span aria-hidden="true">✕ </span>
          {error}
        </p>
      )}
      {message && (
        <p className="nfr-banner nfr-banner--ok" role="status">
          <span aria-hidden="true">✓ </span>
          {message}
        </p>
      )}

      {/* ------------------------------------------------------------------ */}
      <section className="content-card nfr-section" aria-labelledby="gov-reference">
        <div className="gov-section-head">
          <h3 id="gov-reference">Reference data</h3>
          <div className="gov-section-tools">
            <label className="gov-check">
              <input type="checkbox" checked={showHistory} onChange={(e) => setShowHistory(e.target.checked)} />
              Show superseded snapshots
            </label>
            <button
              type="button"
              className="secondary-button"
              disabled={busy}
              onClick={() =>
                run(async () => {
                  const results = await reloadSnapshots();
                  const changed = results.filter((r) => !r.unchanged);
                  return changed.length
                    ? `Loaded ${changed.map((r) => `${r.source} as of ${r.as_of}`).join(", ")} as new, unattested snapshots.`
                    : undefined;
                }, "Bundled lists reloaded; nothing changed, so existing attestations stand.")
              }
            >
              Reload bundled lists
            </button>
          </div>
        </div>
        <p className="risk-reason">
          Only a snapshot a named reviewer has attested against its primary publication feeds
          scoring rules. A file&apos;s own &quot;verified&quot; flag is recorded but not trusted.
        </p>

        {snapshots.length === 0 ? (
          <p className="risk-reason">
            No reference lists are loaded. Use <strong>Reload bundled lists</strong> to load the
            FATF and EU snapshots shipped with the application.
          </p>
        ) : (
          <div className="gov-table-wrap">
            <table className="nfr-table">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">As of</th>
                  <th scope="col">Entries</th>
                  <th scope="col">File says verified</th>
                  <th scope="col">Attested</th>
                  <th scope="col">Used in scoring</th>
                  <th scope="col">Checksum</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((snapshot) => (
                  <tr key={snapshot.id} className={snapshot.is_current ? undefined : "gov-row-muted"}>
                    <td>
                      {snapshot.source}
                      {!snapshot.is_current && " (superseded)"}
                    </td>
                    <td>{snapshot.as_of}</td>
                    <td>{snapshot.entry_count}</td>
                    <td title={snapshot.source_verification_note ?? undefined}>
                      {snapshot.source_claims_verified ? "Yes (self-declared)" : "No"}
                    </td>
                    <td title={snapshot.attestation_note ?? undefined}>
                      {snapshot.attested ? `${snapshot.attested_by}, ${when(snapshot.attested_at)}` : <span className="gov-pill gov-pill-warn">Not attested</span>}
                    </td>
                    <td>
                      {snapshot.used_in_scoring ? (
                        <span className="gov-pill gov-pill-ok">✓ Yes</span>
                      ) : (
                        <span className="gov-pill gov-pill-warn">No</span>
                      )}
                    </td>
                    <td title={snapshot.checksum}><code>{shortHash(snapshot.checksum)}</code></td>
                    <td>
                      {snapshot.is_current && !snapshot.attested && openForm !== `attest:${snapshot.id}` && (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => setOpenForm(`attest:${snapshot.id}`)}
                        >
                          Attest…
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {snapshots
          .filter((s) => openForm === `attest:${s.id}`)
          .map((snapshot) => (
            <div key={snapshot.id}>
              <p className="risk-reason">
                Attesting <strong>{snapshot.source}</strong> as of {snapshot.as_of}
                {snapshot.source_url && (
                  <>
                    {" "}— primary source:{" "}
                    <a href={snapshot.source_url} target="_blank" rel="noreferrer noopener">
                      {snapshot.source_url}
                    </a>
                  </>
                )}
                . Once attested it changes scores for every assessment exposed to these
                countries.
              </p>
              <ReasonForm
                label="What did you check, and against which primary publication?"
                submitLabel="Attest snapshot"
                minLength={20}
                busy={busy}
                onCancel={() => setOpenForm(null)}
                onSubmit={(note) =>
                  run(() => attestSnapshot(snapshot.id, note), `${snapshot.source} snapshot attested; it now feeds scoring rules.`)
                }
              />
            </div>
          ))}
      </section>

      {/* ------------------------------------------------------------------ */}
      <section className="content-card nfr-section" aria-labelledby="gov-methodology">
        <h3 id="gov-methodology">Methodology versions</h3>
        <p className="risk-reason">
          In force: <strong>{activeName || "—"}</strong>
        </p>

        {methodologies.length === 0 ? (
          <div>
            <p className="risk-reason">
              No methodology is configured, so calculations use the built-in defaults (recorded as
              version &quot;builtin&quot;). To approve a draft rule or change the grid, create an
              editable methodology from those defaults.
            </p>
            {openForm === "create" ? (
              <ReasonForm
                label="Name for the new methodology"
                submitLabel="Create"
                busy={busy}
                onCancel={() => setOpenForm(null)}
                onSubmit={(name) =>
                  run(async () => {
                    const created = await createFromDefaults(name);
                    setSelectedId(created.id);
                  }, "Methodology created as version 1 (inactive, editable).")
                }
              />
            ) : (
              <button type="button" className="primary-button" onClick={() => setOpenForm("create")}>
                Create from built-in defaults
              </button>
            )}
            {builtinRules.length > 0 && (
              <p className="risk-reason">
                Built-in rules: {builtinRules.map((r) => `${r.rule_code} (${r.status})`).join(", ")}.
              </p>
            )}
          </div>
        ) : (
          <div className="gov-table-wrap">
            <table className="nfr-table">
              <thead>
                <tr>
                  <th scope="col">Methodology</th>
                  <th scope="col">Status</th>
                  <th scope="col">Approved</th>
                  <th scope="col">Change reason</th>
                  <th scope="col">Fingerprint</th>
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {methodologies.map((m) => (
                  <tr key={m.id} aria-selected={selectedId === m.id}>
                    <td>
                      {m.name} <strong>v{m.version}</strong>
                      {m.parent_id != null && (
                        <span className="gov-muted"> (from #{m.parent_id})</span>
                      )}
                    </td>
                    <td>
                      <span className={`gov-pill ${m.is_active ? "gov-pill-ok" : m.retired_at ? "gov-pill-grey" : "gov-pill-info"}`}>
                        {m.is_active ? "✓ Active" : m.retired_at ? "Retired" : "Draft"}
                      </span>{" "}
                      <span className="gov-muted">{m.locked ? "🔒 locked" : "editable"}</span>
                    </td>
                    <td title={m.approval_reason ?? undefined}>
                      {m.approved_by ? `${m.approved_by}, ${when(m.approved_at)}` : "—"}
                    </td>
                    <td>{m.change_reason ?? "—"}</td>
                    <td title={m.fingerprint ?? undefined}><code>{shortHash(m.fingerprint)}</code></td>
                    <td>
                      <div className="gov-row-actions">
                        <button type="button" className="secondary-button" onClick={() => setSelectedId(m.id)}>
                          {selectedId === m.id ? "Viewing" : "View"}
                        </button>
                        <button type="button" className="secondary-button" onClick={() => setOpenForm(`clone:${m.id}`)}>
                          Clone…
                        </button>
                        {!m.is_active && (
                          <button type="button" className="secondary-button" onClick={() => setOpenForm(`activate:${m.id}`)}>
                            Approve &amp; activate…
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {methodologies
          .filter((m) => openForm === `clone:${m.id}` || openForm === `activate:${m.id}`)
          .map((m) =>
            openForm === `clone:${m.id}` ? (
              <ReasonForm
                key={`clone-${m.id}`}
                label={`Why is ${m.name} v${m.version} being revised?`}
                submitLabel="Clone to next version"
                busy={busy}
                onCancel={() => setOpenForm(null)}
                onSubmit={(reason) =>
                  run(async () => {
                    const clone = await cloneMethodology(m.id, reason);
                    setSelectedId(clone.id);
                  }, "Cloned. The new version is inactive and editable until it is used.")
                }
              />
            ) : (
              <ReasonForm
                key={`activate-${m.id}`}
                label={`Approval for ${m.name} v${m.version}: who approved it, and why? It replaces the methodology in force for all future calculations.`}
                submitLabel="Approve & activate"
                busy={busy}
                onCancel={() => setOpenForm(null)}
                onSubmit={(reason) =>
                  run(() => activateMethodology(m.id, reason), `${m.name} v${m.version} is now in force.`)
                }
              />
            )
          )}
      </section>

      {/* ------------------------------------------------------------------ */}
      {selected && (
        <>
          <ScoringConfigPanel
            key={`${selected.id}-${selected.locked}-${selected.config.methodology_fingerprint}`}
            methodologyId={selected.id}
            name={selected.name}
            version={selected.version}
            locked={selected.locked}
            config={selected.config}
            onSaved={(text) => {
              setMessage(text);
              load();
            }}
          />

          <section className="content-card nfr-section" aria-labelledby="gov-rules">
            <h3 id="gov-rules">
              Policy rules — {selected.name} v{selected.version}
            </h3>
            {selected.locked ? (
              <p className="risk-reason">
                🔒 Locked on {when(selected.locked_at)}: results depend on these exact rules. Clone
                this version to change them.
              </p>
            ) : (
              <p className="risk-reason">
                Only <strong>approved</strong> rules are applied. Changes take effect when this
                version is approved and activated.
              </p>
            )}

            <div className="gov-table-wrap">
              <table className="nfr-table">
                <thead>
                  <tr>
                    <th scope="col">Rule</th>
                    <th scope="col">When</th>
                    <th scope="col">Effect</th>
                    <th scope="col">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {draftRules.map((rule, index) => (
                    <tr key={rule.rule_code}>
                      <td>
                        <strong>{rule.rule_code}</strong> v{rule.version}
                        <div className="gov-muted gov-desc">{rule.description}</div>
                      </td>
                      <td>{conditionText(rule)}</td>
                      <td>
                        {rule.rule_type === "override" ? "Override to" : "Floor of"} {rule.min_band}
                        {rule.non_mitigable && " · controls cannot lower it"}
                        {rule.mandatory_review && " · mandatory review"}
                      </td>
                      <td>
                        {selected.locked ? (
                          rule.status
                        ) : (
                          <select
                            aria-label={`Status of ${rule.rule_code}`}
                            value={rule.status}
                            onChange={(event) => {
                              const next = draftRules.slice();
                              next[index] = { ...rule, status: event.target.value as PolicyRule["status"] };
                              setDraftRules(next);
                            }}
                          >
                            <option value="approved">approved</option>
                            <option value="draft">draft</option>
                            <option value="disabled">disabled</option>
                          </select>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {!selected.locked && rulesChanged && (
              openForm === "rules" ? (
                <ReasonForm
                  label="Reason for the rule changes (e.g. the compliance approval reference)"
                  submitLabel="Save rules"
                  busy={busy}
                  onCancel={() => setOpenForm(null)}
                  onSubmit={(reason) =>
                    run(() => saveRules(selected.id, draftRules, reason), "Rules saved on this version.")
                  }
                />
              ) : (
                <div className="gov-form-actions">
                  <button type="button" className="primary-button" onClick={() => setOpenForm("rules")}>
                    Save rule changes…
                  </button>
                  <button
                    type="button"
                    className="secondary-button"
                    onClick={() => setDraftRules(selected.config.escalation_rules.map((r) => ({ ...r })))}
                  >
                    Discard
                  </button>
                </div>
              )
            )}
          </section>

          {draftGrid && (
            <section className="content-card nfr-section" aria-labelledby="gov-grid">
              <h3 id="gov-grid">
                Residual grid v{draftGrid.version} — {selected.name} v{selected.version}
              </h3>
              <p className="risk-reason">
                Inherent band × control rating → residual band. A cell can never be above its
                inherent band: controls only mitigate.
              </p>
              {!selected.locked && (
                <div className="form-group gov-narrow">
                  <label htmlFor="grid-version">Grid version</label>
                  <input
                    id="grid-version"
                    value={draftGrid.version}
                    onChange={(e) => setDraftGrid({ ...draftGrid, version: e.target.value })}
                  />
                </div>
              )}
              <div className="gov-table-wrap">
                <table className="nfr-table">
                  <thead>
                    <tr>
                      <th scope="col">Inherent \ Controls</th>
                      {RATINGS.map((rating) => (
                        <th scope="col" key={rating}>{RATING_LABEL[rating]}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {bandOrder.map((band) => (
                      <tr key={band}>
                        <th scope="row">{band}</th>
                        {RATINGS.map((rating) => {
                          const value = draftGrid.cells[band]?.[rating] ?? "";
                          const allowed = bandOrder.slice(0, bandOrder.indexOf(band) + 1);
                          return (
                            <td key={rating}>
                              {selected.locked ? (
                                value
                              ) : (
                                <select
                                  aria-label={`Residual for ${band} inherent with ${RATING_LABEL[rating]} controls`}
                                  value={value}
                                  onChange={(event) =>
                                    setDraftGrid({
                                      ...draftGrid,
                                      cells: {
                                        ...draftGrid.cells,
                                        [band]: { ...draftGrid.cells[band], [rating]: event.target.value },
                                      },
                                    })
                                  }
                                >
                                  {allowed.map((option) => (
                                    <option key={option} value={option}>{option}</option>
                                  ))}
                                </select>
                              )}
                            </td>
                          );
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              {!selected.locked && gridChanged && (
                openForm === "grid" ? (
                  <ReasonForm
                    label="Reason for the grid change"
                    submitLabel="Save grid"
                    busy={busy}
                    onCancel={() => setOpenForm(null)}
                    onSubmit={(reason) =>
                      run(() => saveResidualGrid(selected.id, draftGrid, reason), "Residual grid saved on this version.")
                    }
                  />
                ) : (
                  <div className="gov-form-actions">
                    <button type="button" className="primary-button" onClick={() => setOpenForm("grid")}>
                      Save grid changes…
                    </button>
                    <button
                      type="button"
                      className="secondary-button"
                      onClick={() => setDraftGrid(JSON.parse(JSON.stringify(selected.config.residual_grid)))}
                    >
                      Discard
                    </button>
                  </div>
                )
              )}
            </section>
          )}
        </>
      )}

      <ChallengeTriggerPanel />
    </div>
  );
}
