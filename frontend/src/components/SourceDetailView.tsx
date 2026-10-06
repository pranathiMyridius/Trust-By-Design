import { useCallback, useEffect, useState } from "react";

import {
  actOnVersion,
  downloadVersionFile,
  getApprovals,
  getAudit,
  getChunks,
  getRecord,
  reprocessVersion,
  retireRecord,
  type ApprovalEntry,
  type AuditPage,
  type ChunkPage,
  type LibraryMeta,
  type SourceRecordDetail,
  type SourceVersion,
  type VersionAction,
} from "../api/sourceLibrary";
import { friendlyError } from "../utils/errorMessages";
import SourceFormModal, { type SourceFormMode } from "./SourceFormModal";
import { CommentDialog } from "./SourceModal";
import {
  ACTION_LABEL,
  EMBEDDING_LABEL,
  EVENT_LABEL,
  PROCESSING_LABEL,
  SCAN_LABEL,
  STATUS_LABEL,
  formatDate,
  formatDateTime,
  formatSize,
} from "./sourceLibraryUi";

type Tab = "versions" | "approvals" | "audit";

type Pending =
  | { kind: "form"; mode: SourceFormMode }
  | { kind: "decision"; action: "approve" | "reject" | "withdraw" | "retire"; version: SourceVersion }
  | { kind: "retireRecord" }
  | null;

function StatusPill({ status }: { status: string }) {
  return <span className={`sl-pill sl-pill--${status.toLowerCase().replace("_", "-")}`}>{STATUS_LABEL[status as keyof typeof STATUS_LABEL] ?? status}</span>;
}

/** The detailed view of one source: metadata, versions, approval history and audit timeline. */
export default function SourceDetailView({
  recordId,
  meta,
  onBack,
  onChanged,
  notice = "",
}: {
  recordId: string;
  meta: LibraryMeta;
  onBack: () => void;
  onChanged: () => void;
  notice?: string;
}) {
  const [record, setRecord] = useState<SourceRecordDetail | null>(null);
  const [tab, setTab] = useState<Tab>("versions");
  const [error, setError] = useState("");
  const [message, setMessage] = useState(notice);
  const [pending, setPending] = useState<Pending>(null);
  const [approvals, setApprovals] = useState<ApprovalEntry[] | null>(null);
  const [audit, setAudit] = useState<AuditPage | null>(null);
  const [auditPage, setAuditPage] = useState(1);
  const [busy, setBusy] = useState(false);
  const canSeeHistory = meta.can_view_all;

  const load = useCallback(() => {
    getRecord(recordId)
      .then((loaded) => {
        setRecord(loaded);
        setError("");
      })
      .catch((err) => setError(friendlyError(err, "The source couldn't be loaded.")));
    if (canSeeHistory) {
      getApprovals(recordId).then(setApprovals).catch(() => setApprovals([]));
      getAudit(recordId, auditPage).then(setAudit).catch(() => setAudit(null));
    }
  }, [recordId, canSeeHistory, auditPage]);

  useEffect(load, [load]);

  async function run(work: () => Promise<unknown>, success: string) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await work();
      setMessage(success);
      load();
      onChanged();
    } catch (err) {
      setError(friendlyError(err, "That action couldn't be completed."));
    } finally {
      setBusy(false);
    }
  }

  if (!record) {
    return (
      <div className="sl-card">
        <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={onBack}>
          ← Back to sources
        </button>
        {error ? (
          <p className="sl-banner sl-banner--error" role="alert">
            <span aria-hidden="true">✕</span>
            <span>{error}</span>
          </p>
        ) : (
          <p className="sl-empty">Loading source…</p>
        )}
      </div>
    );
  }

  const retired = record.status === "RETIRED";
  const open = record.versions.find((v) => ["DRAFT", "IN_REVIEW", "REJECTED"].includes(v.status));

  return (
    <div className="slg-detail">
      <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={onBack}>
        ← Back to sources
      </button>

      {error && (
        <p className="sl-banner sl-banner--error slg-top" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
        </p>
      )}
      {message && (
        <p className="sl-banner sl-banner--ok slg-top" role="status">
          <span aria-hidden="true">✓</span>
          <span>{message}</span>
        </p>
      )}

      <section className="sl-card slg-head" aria-labelledby="slg-title">
        <div className="slg-head__row">
          <div>
            <p className="slg-code">{record.source_code}</p>
            <h2 id="slg-title">{record.title}</h2>
            <p className="sl-sub">{record.authority}</p>
          </div>
          <div className="slg-head__side">
            <StatusPill status={record.library_status} />
            {record.outdated && <span className="sl-pill sl-pill--outdated">⚠ Past review date</span>}
            {record.pending_version && record.library_status === "APPROVED" && (
              <span className="sl-pill sl-pill--draft">New version {STATUS_LABEL[record.pending_version.status].toLowerCase()}</span>
            )}
          </div>
        </div>

        <dl className="slg-meta">
          <div><dt>Category</dt><dd>{record.category_label}</dd></div>
          <div><dt>Jurisdiction</dt><dd>{record.jurisdiction ?? "All (not restricted)"}</dd></div>
          <div><dt>Applicable entity</dt><dd>{record.applicable_entity ?? "—"}</dd></div>
          <div><dt>Source owner</dt><dd>{record.owner ?? "—"}</dd></div>
          <div><dt>Review frequency</dt><dd>{record.review_frequency ?? "—"}</dd></div>
          <div>
            <dt>Official URL</dt>
            <dd>
              {record.source_url ? (
                <a href={record.source_url} target="_blank" rel="noopener noreferrer">
                  {record.source_url}
                </a>
              ) : (
                "—"
              )}
            </dd>
          </div>
          <div>
            <dt>Version in force</dt>
            <dd>
              {record.current_version
                ? `${record.current_version.version_label} (approved ${formatDate(record.current_version.decided_at)} by ${record.current_version.decided_by ?? "—"})`
                : "None — not usable as evidence"}
            </dd>
          </div>
          <div><dt>Review by</dt><dd>{formatDate(record.current_version?.review_date)}</dd></div>
        </dl>
        {record.topics.length > 0 && (
          <ul className="slg-topics" aria-label="Topics">
            {record.topics.map((topic) => (
              <li key={topic}>{topic}</li>
            ))}
          </ul>
        )}
        {record.description && <p className="slg-desc">{record.description}</p>}

        {(meta.can_maintain || meta.can_review) && !retired && (
          <div className="slg-actions">
            {meta.can_maintain && (
              <>
                <button type="button" className="sl-btn sl-btn--secondary" onClick={() => setPending({ kind: "form", mode: { kind: "edit", record } })}>
                  Edit details
                </button>
                <button
                  type="button"
                  className="sl-btn sl-btn--secondary"
                  disabled={Boolean(open)}
                  title={open ? "Finish or withdraw the open version first" : undefined}
                  onClick={() => setPending({ kind: "form", mode: { kind: "version", record } })}
                >
                  Add version
                </button>
              </>
            )}
            <button type="button" className="sl-btn sl-btn--danger" onClick={() => setPending({ kind: "retireRecord" })}>
              Retire source
            </button>
          </div>
        )}
      </section>

      <div className="slg-tabs" role="tablist" aria-label="Source details">
        {(
          [
            ["versions", `Versions (${record.versions.length})`],
            ...(canSeeHistory ? [["approvals", "Approval history"], ["audit", "Audit timeline"]] : []),
          ] as [Tab, string][]
        ).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={`slg-tab${tab === key ? " slg-tab--active" : ""}`} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>

      {tab === "versions" && (
        <div className="slg-versions">
          {record.versions.map((version) => (
            <VersionCard
              key={version.id}
              record={record}
              version={version}
              meta={meta}
              busy={busy}
              onDecision={(action) => setPending({ kind: "decision", action, version })}
              onEdit={() => setPending({ kind: "form", mode: { kind: "editVersion", record, version } })}
              onSubmit={() => void run(() => actOnVersion(record.id, version.id, "submit"), "Submitted for review.")}
              onReprocess={() => void run(() => reprocessVersion(record.id, version.id), "Document reprocessed.")}
              onError={setError}
            />
          ))}
        </div>
      )}

      {tab === "approvals" && (
        <section className="sl-card" aria-label="Approval history">
          {!approvals ? (
            <p className="sl-empty">Loading…</p>
          ) : approvals.length === 0 ? (
            <p className="sl-empty">No submissions or decisions yet.</p>
          ) : (
            <ol className="slg-timeline">
              {approvals.map((entry) => (
                <li key={entry.id} className={`slg-timeline__item slg-timeline__item--${entry.action.toLowerCase()}`}>
                  <div className="slg-timeline__title">
                    <strong>{ACTION_LABEL[entry.action] ?? entry.action}</strong>
                    <span>
                      version {entry.version_label ?? entry.version_number}
                      {entry.from_status ? ` · ${STATUS_LABEL[entry.from_status as keyof typeof STATUS_LABEL] ?? entry.from_status} → ${STATUS_LABEL[entry.to_status as keyof typeof STATUS_LABEL] ?? entry.to_status}` : ""}
                    </span>
                  </div>
                  <p className="sl-sub">
                    {entry.actor} · {formatDateTime(entry.created_at)}
                  </p>
                  {entry.comment && <blockquote>{entry.comment}</blockquote>}
                </li>
              ))}
            </ol>
          )}
        </section>
      )}

      {tab === "audit" && (
        <section className="sl-card" aria-label="Audit timeline">
          {!audit ? (
            <p className="sl-empty">Loading…</p>
          ) : audit.items.length === 0 ? (
            <p className="sl-empty">No events recorded.</p>
          ) : (
            <>
              <ol className="slg-timeline">
                {audit.items.map((entry) => (
                  <li key={entry.id} className="slg-timeline__item">
                    <div className="slg-timeline__title">
                      <strong>{EVENT_LABEL[entry.event_type] ?? entry.event_type}</strong>
                      {entry.version_label && <span>version {entry.version_label}</span>}
                    </div>
                    <p className="sl-sub">
                      {entry.actor} · {formatDateTime(entry.created_at)}
                    </p>
                    {entry.details && <p className="slg-detail-text">{entry.details}</p>}
                  </li>
                ))}
              </ol>
              <Pager page={audit.page} pageSize={audit.page_size} total={audit.total} onPage={setAuditPage} />
            </>
          )}
        </section>
      )}

      {pending?.kind === "form" && (
        <SourceFormModal
          mode={pending.mode}
          meta={meta}
          onClose={() => setPending(null)}
          onSaved={(_saved, text) => {
            setPending(null);
            setMessage(text);
            load();
            onChanged();
          }}
        />
      )}

      {pending?.kind === "decision" && (
        <CommentDialog
          title={
            pending.action === "approve"
              ? `Approve version ${pending.version.version_label}`
              : pending.action === "reject"
                ? `Reject version ${pending.version.version_label}`
                : pending.action === "withdraw"
                  ? `Withdraw version ${pending.version.version_label} from review`
                  : `Retire version ${pending.version.version_label}`
          }
          intro={
            pending.action === "approve" ? (
              <>
                Approving makes this version the one used in risk assessments
                {record.current_version ? `, and supersedes version ${record.current_version.version_label}` : ""}. You cannot approve a version you
                prepared or submitted.
              </>
            ) : pending.action === "reject" ? (
              "The version returns to the maintainer for rework; the reason is recorded."
            ) : undefined
          }
          label={pending.action === "retire" ? "Reason" : "Comment"}
          confirmLabel={{ approve: "Approve", reject: "Reject", withdraw: "Withdraw", retire: "Retire" }[pending.action]}
          tone={pending.action === "approve" ? "primary" : "danger"}
          minLength={pending.action === "withdraw" ? 0 : meta.min_comment_length}
          onClose={() => setPending(null)}
          onConfirm={async (comment) => {
            await actOnVersion(record.id, pending.version.id, pending.action as VersionAction, comment);
            setPending(null);
            setMessage(
              { approve: "Version approved.", reject: "Version rejected.", withdraw: "Version withdrawn from review.", retire: "Version retired." }[pending.action],
            );
            load();
            onChanged();
          }}
        />
      )}

      {pending?.kind === "retireRecord" && (
        <CommentDialog
          title={`Retire ${record.source_code}`}
          intro="Every version stops being usable. This cannot be undone; add a new source if it applies again."
          label="Reason"
          confirmLabel="Retire source"
          tone="danger"
          minLength={meta.min_comment_length}
          onClose={() => setPending(null)}
          onConfirm={async (reason) => {
            await retireRecord(record.id, reason);
            setPending(null);
            setMessage("Source retired.");
            load();
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function Pager({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return null;
  return (
    <div className="slg-pager">
      <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Previous
      </button>
      <span>
        Page {page} of {pages}
      </span>
      <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
    </div>
  );
}

function VersionCard({
  record,
  version,
  meta,
  busy,
  onDecision,
  onEdit,
  onSubmit,
  onReprocess,
  onError,
}: {
  record: SourceRecordDetail;
  version: SourceVersion;
  meta: LibraryMeta;
  busy: boolean;
  onDecision: (action: "approve" | "reject" | "withdraw" | "retire") => void;
  onEdit: () => void;
  onSubmit: () => void;
  onReprocess: () => void;
  onError: (text: string) => void;
}) {
  const [passages, setPassages] = useState<ChunkPage | null>(null);
  const [passagePage, setPassagePage] = useState(1);
  const [showPassages, setShowPassages] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const editable = version.status === "DRAFT" || version.status === "REJECTED";
  const retired = record.status === "RETIRED";

  useEffect(() => {
    if (!showPassages) return;
    getChunks(record.id, version.id, passagePage)
      .then(setPassages)
      .catch((err) => onError(friendlyError(err, "The extracted text couldn't be loaded.")));
  }, [showPassages, passagePage, record.id, version.id, onError]);

  return (
    <article className={`sl-card slg-version slg-version--${version.status.toLowerCase().replace("_", "-")}`} aria-label={`Version ${version.version_label}`}>
      <div className="slg-version__head">
        <div>
          <h3>
            Version {version.version_label} <StatusPill status={version.status} />
            {version.outdated && <span className="sl-pill sl-pill--outdated"> ⚠ Past review date</span>}
          </h3>
          <p className="sl-sub">
            #{version.version_number} · created {formatDateTime(version.created_at)} by {version.created_by ?? "—"}
            {version.migrated ? " · migrated from the earlier library" : ""}
          </p>
        </div>
        <div className="slg-actions">
          {meta.can_maintain && !retired && editable && (
            <>
              <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={onEdit} disabled={busy}>
                Edit
              </button>
              <button type="button" className="sl-btn sl-btn--small sl-btn--primary" onClick={onSubmit} disabled={busy || version.status === "REJECTED"} title={version.status === "REJECTED" ? "Edit the version first to reopen it" : undefined}>
                Submit for review
              </button>
            </>
          )}
          {version.status === "IN_REVIEW" && !retired && (
            <>
              {meta.can_review && (
                <>
                  <button type="button" className="sl-btn sl-btn--small sl-btn--primary" onClick={() => onDecision("approve")} disabled={busy}>
                    Approve
                  </button>
                  <button type="button" className="sl-btn sl-btn--small sl-btn--danger" onClick={() => onDecision("reject")} disabled={busy}>
                    Reject
                  </button>
                </>
              )}
              {meta.can_maintain && (
                <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={() => onDecision("withdraw")} disabled={busy}>
                  Withdraw
                </button>
              )}
            </>
          )}
          {version.status === "APPROVED" && !retired && (meta.can_maintain || meta.can_review) && (
            <button type="button" className="sl-btn sl-btn--small sl-btn--danger" onClick={() => onDecision("retire")} disabled={busy}>
              Retire version
            </button>
          )}
        </div>
      </div>

      <dl className="slg-meta slg-meta--tight">
        <div><dt>Effective</dt><dd>{formatDate(version.effective_date)}</dd></div>
        <div><dt>Review by</dt><dd>{formatDate(version.review_date)}</dd></div>
        <div><dt>Captured</dt><dd>{formatDate(version.retrieved_date)}</dd></div>
        <div>
          <dt>URL</dt>
          <dd>
            {version.source_url ? (
              <a href={version.source_url} target="_blank" rel="noopener noreferrer">
                {version.source_url}
              </a>
            ) : (
              "—"
            )}
          </dd>
        </div>
        {version.submitted_at && <div><dt>Submitted</dt><dd>{formatDateTime(version.submitted_at)} by {version.submitted_by}</dd></div>}
        {version.decided_at && <div><dt>Decision</dt><dd>{formatDateTime(version.decided_at)} by {version.decided_by}</dd></div>}
      </dl>
      {version.change_summary && <p className="slg-desc">{version.change_summary}</p>}
      {version.decision_comment && (
        <blockquote className="slg-decision">
          <strong>Reviewer comment:</strong> {version.decision_comment}
        </blockquote>
      )}

      {version.has_file ? (
        <div className="slg-doc">
          <div className="slg-doc__main">
            <span aria-hidden="true">📎</span>
            <strong>{version.original_filename}</strong>
            <span className="sl-sub">
              {formatSize(version.file_size)}
              {version.page_count ? ` · ${version.page_count} page${version.page_count === 1 ? "" : "s"}` : ""}
            </span>
            <button
              type="button"
              className="sl-btn sl-btn--small sl-btn--secondary"
              aria-label={`Download ${version.original_filename}`}
              disabled={downloading}
              onClick={async () => {
                setDownloading(true);
                try {
                  await downloadVersionFile(record.id, version.id, version.original_filename ?? "document.pdf");
                } catch (err) {
                  onError(friendlyError(err, "The document couldn't be downloaded."));
                } finally {
                  setDownloading(false);
                }
              }}
            >
              {downloading ? "Downloading…" : "Download"}
            </button>
          </div>
          <ul className="slg-chips" aria-label="Document processing">
            <li className={version.scan_status === "CLEAN" ? "ok" : version.scan_status === "NOT_SCANNED" ? "" : "bad"}>{SCAN_LABEL[version.scan_status]}</li>
            <li className={version.processing_status === "PROCESSED" ? "ok" : version.processing_status === "FAILED" ? "bad" : ""}>{PROCESSING_LABEL[version.processing_status]}</li>
            {version.chunk_count > 0 && <li>{version.chunk_count} passages</li>}
            <li className={version.embedding_status === "COMPLETE" ? "ok" : version.embedding_status === "NONE" ? "" : "warn"}>{EMBEDDING_LABEL[version.embedding_status]}</li>
          </ul>
          {version.file_sha256 && <p className="slg-hash" title={version.file_sha256}>SHA-256 {version.file_sha256}</p>}
          {version.processing_error && (
            <p className="sl-banner sl-banner--error" role="alert">
              <span aria-hidden="true">✕</span>
              <span>{version.processing_error}</span>
            </p>
          )}
          <div className="slg-actions">
            {meta.can_maintain && editable && version.processing_status === "FAILED" && (
              <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={onReprocess} disabled={busy}>
                Reprocess
              </button>
            )}
            {meta.can_view_all && version.chunk_count > 0 && (
              <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" aria-expanded={showPassages} onClick={() => setShowPassages((value) => !value)}>
                {showPassages ? "Hide extracted passages" : "View extracted passages"}
              </button>
            )}
          </div>
          {showPassages && (
            <div className="slg-passages">
              {!passages ? (
                <p className="sl-empty">Loading…</p>
              ) : (
                <>
                  {passages.items.map((chunk) => (
                    <div key={chunk.id} className="slg-passage">
                      <p className="sl-sub">
                        {chunk.page_start ? `Page ${chunk.page_start}${chunk.page_end !== chunk.page_start ? `–${chunk.page_end}` : ""}` : "No page"}
                        {chunk.section ? ` · ${chunk.section}` : ""}
                      </p>
                      <p>{chunk.text}</p>
                    </div>
                  ))}
                  <Pager page={passages.page} pageSize={passages.page_size} total={passages.total} onPage={setPassagePage} />
                </>
              )}
            </div>
          )}
        </div>
      ) : (
        <p className="sl-sub">No uploaded document — linked to the official page only.</p>
      )}
    </article>
  );
}
