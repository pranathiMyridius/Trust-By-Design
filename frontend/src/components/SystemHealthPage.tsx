import { useCallback, useEffect, useState } from "react";
import {
  createBackup,
  getIntegrity,
  getPerformance,
  getRecoveryStatus,
  getSecurityPosture,
  listBackups,
  verifyBackup,
  type BackupSummary,
  type BackupVerification,
  type IntegrityReport,
  type PerformanceSummary,
  type RecoveryStatus,
  type SecurityPosture,
} from "../api/system";
import "./NonFunctional.css";

/*
 * Stage 19: admin view of the non-functional requirements in operation --
 * response times against the agreed service targets, backup/recovery
 * against the recovery objectives, data integrity, and the security
 * configuration. Every pass/fail is shown as text + symbol, not colour.
 */

function Check({ ok, yes, no }: { ok: boolean; yes: string; no: string }) {
  return (
    <span className={`nfr-check ${ok ? "nfr-check--ok" : "nfr-check--warn"}`}>
      <span aria-hidden="true">{ok ? "✓ " : "⚠ "}</span>
      {ok ? yes : no}
    </span>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

interface SystemStatus {
  performance: PerformanceSummary;
  recovery: RecoveryStatus;
  integrity: IntegrityReport;
  security: SecurityPosture;
  backups: BackupSummary[];
}

async function fetchSystemStatus(): Promise<SystemStatus> {
  const [performance, recovery, integrity, security, backups] = await Promise.all([
    getPerformance(),
    getRecoveryStatus(),
    getIntegrity(),
    getSecurityPosture(),
    listBackups(),
  ]);
  return { performance, recovery, integrity, security, backups };
}

export default function SystemHealthPage() {
  const [performance, setPerformance] = useState<PerformanceSummary | null>(null);
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null);
  const [integrity, setIntegrity] = useState<IntegrityReport | null>(null);
  const [security, setSecurity] = useState<SecurityPosture | null>(null);
  const [backups, setBackups] = useState<BackupSummary[]>([]);
  const [verifications, setVerifications] = useState<Record<string, BackupVerification>>({});
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);

  const applyStatus = useCallback((status: SystemStatus) => {
    setPerformance(status.performance);
    setRecovery(status.recovery);
    setIntegrity(status.integrity);
    setSecurity(status.security);
    setBackups(status.backups);
    setError("");
  }, []);

  const load = useCallback(async () => {
    try {
      applyStatus(await fetchSystemStatus());
    } catch (err) {
      setError(err instanceof Error ? err.message : "System status couldn't be loaded.");
    }
  }, [applyStatus]);

  useEffect(() => {
    let cancelled = false;
    fetchSystemStatus()
      .then((status) => {
        if (!cancelled) applyStatus(status);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "System status couldn't be loaded.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [applyStatus]);

  async function handleBackup() {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const created = await createBackup();
      setMessage(`Backup ${created.name} created (${formatBytes(created.size_bytes)}).`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "The backup failed.");
    } finally {
      setBusy(false);
    }
  }

  async function handleVerify(name: string) {
    setBusy(true);
    setError("");
    try {
      const result = await verifyBackup(name);
      setVerifications((current) => ({ ...current, [name]: result }));
    } catch (err) {
      setError(err instanceof Error ? err.message : "The backup couldn't be verified.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="nfr-page">
      <div className="page-header">
        <div>
          <h2>System Health</h2>
          <p>Performance, backup and recovery, data integrity and security configuration.</p>
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

      <section className="content-card nfr-section" aria-labelledby="nfr-recovery">
        <h3 id="nfr-recovery">Backup and recovery</h3>
        {recovery ? (
          <>
            <dl className="nfr-facts">
              <div>
                <dt>Recovery point objective</dt>
                <dd>{recovery.recovery_point_objective_hours} hours (maximum data loss)</dd>
              </div>
              <div>
                <dt>Recovery time objective</dt>
                <dd>{recovery.recovery_time_objective_hours} hours (maximum time to restore)</dd>
              </div>
              <div>
                <dt>Scheduled backups</dt>
                <dd>
                  {recovery.backup_interval_hours > 0
                    ? `Every ${recovery.backup_interval_hours} hours`
                    : "Disabled"}
                </dd>
              </div>
              <div>
                <dt>Latest backup</dt>
                <dd>
                  {recovery.latest_backup
                    ? `${recovery.latest_backup.name} — ${recovery.latest_backup_age_hours} hours ago`
                    : "None yet"}
                </dd>
              </div>
            </dl>
            <p>
              <Check
                ok={recovery.rpo_met}
                yes="Latest backup is within the recovery point objective."
                no="No backup within the recovery point objective — create one now."
              />
            </p>
            <p>
              <Check
                ok={recovery.soft_delete_only && recovery.audit_log_append_only}
                yes="Assessments can only be soft-deleted and the audit log is append-only."
                no="Deletion protection is not active."
              />
            </p>
          </>
        ) : (
          <p role="status">Loading…</p>
        )}

        <button type="button" className="primary-button" onClick={handleBackup} disabled={busy}>
          {busy ? "Working…" : "Create backup now"}
        </button>

        {backups.length > 0 && (
          <table className="nfr-table">
            <caption className="sr-only">Backups</caption>
            <thead>
              <tr>
                <th scope="col">Backup</th>
                <th scope="col">Created</th>
                <th scope="col">Size</th>
                <th scope="col">Verification</th>
              </tr>
            </thead>
            <tbody>
              {backups.map((item) => {
                const verification = verifications[item.name];
                return (
                  <tr key={item.name}>
                    <th scope="row">
                      {item.name}
                      {item.label && <div className="nfr-muted">{item.label}</div>}
                    </th>
                    <td>
                      {item.created_at ? new Date(item.created_at).toLocaleString() : "—"}
                      {item.created_by && <div className="nfr-muted">by {item.created_by}</div>}
                    </td>
                    <td>
                      {formatBytes(item.size_bytes)}
                      <div className="nfr-muted">{item.encrypted ? "Encrypted" : "Not encrypted"}</div>
                    </td>
                    <td>
                      {verification ? (
                        <>
                          <Check ok={verification.valid} yes="Verified" no="Failed verification" />
                          {verification.problems.length > 0 && (
                            <ul className="nfr-problems">
                              {verification.problems.map((problem) => (
                                <li key={problem}>{problem}</li>
                              ))}
                            </ul>
                          )}
                        </>
                      ) : (
                        <button
                          type="button"
                          className="secondary-button"
                          onClick={() => handleVerify(item.name)}
                          disabled={busy}
                          aria-label={`Verify backup ${item.name}`}
                        >
                          Verify
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <p className="nfr-muted">
          Restoring replaces the live database, so it is done from the server with{" "}
          <code>python restore_backup.py &lt;backup-name&gt; --confirm</code> (a safety backup
          is taken first).
        </p>
      </section>

      <section className="content-card nfr-section" aria-labelledby="nfr-integrity">
        <h3 id="nfr-integrity">Data integrity</h3>
        {integrity ? (
          <>
            <p>
              <Check
                ok={integrity.database_check === "ok"}
                yes={`Database check passed (${integrity.database_engine}).`}
                no={`Database check reported: ${integrity.database_check}`}
              />
            </p>
            <p>
              <Check
                ok={integrity.documents_missing_files.length === 0}
                yes="Every document's stored file is present."
                no={`${integrity.documents_missing_files.length} document(s) are missing their stored file.`}
              />
            </p>
            {integrity.documents_missing_files.length > 0 && (
              <ul className="nfr-problems">
                {integrity.documents_missing_files.map((doc) => (
                  <li key={doc.document_id}>
                    {doc.filename} (document {doc.document_id}, assessment {doc.assessment_id})
                  </li>
                ))}
              </ul>
            )}
          </>
        ) : (
          <p role="status">Loading…</p>
        )}
      </section>

      <section className="content-card nfr-section" aria-labelledby="nfr-security">
        <h3 id="nfr-security">Security configuration</h3>
        {security ? (
          <>
            <ul className="nfr-checklist">
              <li>
                <Check ok={security.all_api_routes_require_authentication} yes="All API routes require sign-in and respect assessment access rules." no="Some API routes are open." />
              </li>
              <li>
                <Check ok={security.admin_actions_logged} yes="Administrative actions are logged." no="Administrative actions are not logged." />
              </li>
              <li>
                <Check ok={security.jwt_secret_configured} yes="Session signing secret is configured outside the code." no="Session signing secret is not configured (sessions reset on restart)." />
              </li>
              <li>
                <Check ok={security.file_encryption_at_rest} yes="Uploaded files are encrypted at rest." no="Uploaded files are not encrypted at rest." />
              </li>
              <li>
                <Check ok={security.https_enforced} yes="HTTPS is enforced." no="HTTPS is not enforced by the application." />
              </li>
              <li>
                <Check ok={security.ai_payload_masking} yes="Sensitive values are masked before being sent to the AI provider." no="AI payload masking is off." />
              </li>
            </ul>
            {security.recommendations.length > 0 && (
              <>
                <h4>Recommended actions</h4>
                <ul>
                  {security.recommendations.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </>
            )}
          </>
        ) : (
          <p role="status">Loading…</p>
        )}
      </section>

      <section className="content-card nfr-section" aria-labelledby="nfr-performance">
        <h3 id="nfr-performance">Response times</h3>
        {performance ? (
          <>
            <p>
              Targets: pages {performance.targets_ms.PAGE} ms, risk calculations{" "}
              {performance.targets_ms.RISK_CALC} ms, other submissions{" "}
              {performance.targets_ms.SUBMIT} ms (95th percentile over the last{" "}
              {performance.window} requests per route, since the server started). Document
              processing and AI analysis run in the background and aren't counted.
            </p>
            <p>
              <Check
                ok={performance.routes_outside_target === 0}
                yes="All measured routes are within target."
                no={`${performance.routes_outside_target} route(s) are outside their target.`}
              />
            </p>
            {performance.routes.length === 0 ? (
              <p className="nfr-muted">No requests measured yet.</p>
            ) : (
              <div className="nfr-table-scroll" tabIndex={0} aria-label="Response times by route">
                <table className="nfr-table">
                  <caption className="sr-only">Response times by route</caption>
                  <thead>
                    <tr>
                      <th scope="col">Route</th>
                      <th scope="col">Type</th>
                      <th scope="col">Requests</th>
                      <th scope="col">p50 (ms)</th>
                      <th scope="col">p95 (ms)</th>
                      <th scope="col">Target (ms)</th>
                      <th scope="col">Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {performance.routes.map((route) => (
                      <tr key={`${route.method} ${route.route}`}>
                        <th scope="row">
                          <code>
                            {route.method} {route.route}
                          </code>
                        </th>
                        <td>{route.category.replace(/_/g, " ").toLowerCase()}</td>
                        <td>{route.samples}</td>
                        <td>{route.p50_ms}</td>
                        <td>{route.p95_ms}</td>
                        <td>{route.target_ms ?? "—"}</td>
                        <td>
                          <Check
                            ok={route.within_target}
                            yes="Within target"
                            no={`Slow (${route.breaches} over)`}
                          />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        ) : (
          <p role="status">Loading…</p>
        )}
      </section>
    </div>
  );
}
