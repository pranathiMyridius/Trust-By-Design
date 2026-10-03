import { useCallback, useEffect, useId, useRef, useState, type DragEvent } from "react";

import {
  SOURCE_TYPES,
  SOURCE_UPLOAD_ACCEPT,
  approveSource,
  createSource,
  createSourceWithFile,
  downloadSourceFile,
  extractSourceFile,
  listSources,
  retireSource,
  searchSources,
  type ApprovedSource,
  type SourceExtraction,
  type SourceFields,
  type SourceSearchResult,
} from "../api/sources";
import { friendlyError } from "../utils/errorMessages";
import "./SourceLibrary.css";

const EMPTY: SourceFields = {
  title: "",
  source_type: "INTERNAL_POLICY",
  issuer: "",
  version: "",
  effective_date: "",
  review_date: "",
  reference: "",
  content: "",
};

const STATUS_LABEL: Record<ApprovedSource["status"], string> = {
  DRAFT: "Draft",
  APPROVED: "Approved",
  RETIRED: "Retired",
};

/**
 * R5.1/R5.2: the approved evidence-source library. A Policy Admin or Admin
 * adds sources as drafts -- pasted or read from an uploaded document -- and
 * approves or retires them; only approved sources are searched and used as
 * formal assessment evidence.
 */
export default function SourceLibraryPage({ canManage }: { canManage: boolean }) {
  const [sources, setSources] = useState<ApprovedSource[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SourceSearchResult[] | null>(null);
  const [searching, setSearching] = useState(false);

  const load = useCallback(() => {
    listSources()
      .then(setSources)
      .catch((err) => setError(friendlyError(err, "The source library couldn't be loaded.")));
  }, []);

  useEffect(load, [load]);

  async function act(action: () => Promise<unknown>, success: string) {
    setError("");
    setMessage("");
    try {
      await action();
      setMessage(success);
      load();
    } catch (err) {
      setError(friendlyError(err, "That change couldn't be saved."));
    }
  }

  return (
    <div className="sl-page">
      <header className="sl-header">
        <div>
          <h2>Approved Source Library</h2>
          <p>
            Internal policies, procedures, control standards, regulatory guidance, frameworks, previous
            assessments and vendor-control documentation. Only approved sources are used as assessment evidence;
            a source past its review date is flagged as outdated.
          </p>
        </div>
        {canManage && !adding && (
          <button type="button" className="sl-btn sl-btn--primary" onClick={() => setAdding(true)}>
            <span aria-hidden="true">＋</span> Add source
          </button>
        )}
      </header>

      {error && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
        </p>
      )}
      {message && (
        <p className="sl-banner sl-banner--ok" role="status">
          <span aria-hidden="true">✓</span>
          <span>{message}</span>
        </p>
      )}

      {adding && (
        <SourceForm
          onCancel={() => setAdding(false)}
          onSave={(fields, file) =>
            act(async () => {
              await (file ? createSourceWithFile(fields, file) : createSource(fields));
              setAdding(false);
            }, file
              ? "Source added as a draft with its original document. Approve it to make it available as evidence."
              : "Source added as a draft. Approve it to make it available as evidence.")
          }
        />
      )}

      <section className="sl-card" aria-labelledby="sl-search-title">
        <h3 className="sl-card__title" id="sl-search-title">
          Search approved sources
        </h3>
        <p className="sl-card__subtitle">Find the passages that support a risk or a control.</p>
        <form
          className="sl-search"
          onSubmit={(event) => {
            event.preventDefault();
            if (!query.trim()) return;
            setSearching(true);
            searchSources(query.trim())
              .then(setResults)
              .catch((err) => setError(friendlyError(err, "The search failed.")))
              .finally(() => setSearching(false));
          }}
        >
          <input
            aria-label="Search approved sources"
            placeholder="e.g. beneficial ownership, sanctions screening"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <button type="submit" className="sl-btn sl-btn--secondary" disabled={searching || !query.trim()}>
            {searching ? "Searching…" : "Search"}
          </button>
        </form>
        {results &&
          (results.length === 0 ? (
            <p className="sl-empty">No approved source matches “{query}”.</p>
          ) : (
            <ul className="sl-results">
              {results.map((result, index) => (
                <li key={index} className="sl-result">
                  <div className="sl-result__meta">
                    <strong>
                      {result.source_title} v{result.source_version}
                    </strong>
                    <span>{result.source_type_label}</span>
                    {result.effective_date && <span>· effective {result.effective_date}</span>}
                    {result.outdated && <span className="sl-pill sl-pill--outdated">⚠ Past review date</span>}
                  </div>
                  <blockquote>“{result.passage}”</blockquote>
                </li>
              ))}
            </ul>
          ))}
      </section>

      <section className="sl-card" aria-labelledby="sl-list-title">
        <h3 className="sl-card__title" id="sl-list-title">
          Sources ({sources.length})
        </h3>
        <p className="sl-card__subtitle">Drafts become evidence only after approval; retired sources stay on record.</p>
        {sources.length === 0 ? (
          <p className="sl-empty">The library is empty{canManage ? " — add or upload the first source." : "."}</p>
        ) : (
          <div className="sl-table-wrap">
            <table className="sl-table">
              <thead>
                <tr>
                  <th scope="col">Source</th>
                  <th scope="col">Type</th>
                  <th scope="col">Version</th>
                  <th scope="col">Effective</th>
                  <th scope="col">Review by</th>
                  <th scope="col">Status</th>
                  {canManage && <th scope="col">Actions</th>}
                </tr>
              </thead>
              <tbody>
                {sources.map((source) => (
                  <tr key={source.id}>
                    <td>
                      <div className="sl-source-title">{source.title}</div>
                      {source.issuer && <div className="sl-sub">{source.issuer}</div>}
                      {source.reference && <div className="sl-sub">{source.reference}</div>}
                      {source.has_file && source.original_filename && (
                        <OriginalFile source={source} onError={(text) => setError(text)} />
                      )}
                    </td>
                    <td>{source.source_type_label}</td>
                    <td>{source.version}</td>
                    <td>{source.effective_date ?? "—"}</td>
                    <td>
                      {source.review_date ?? "—"}
                      {source.outdated && (
                        <div className="sl-sub">
                          <span className="sl-pill sl-pill--outdated">⚠ Outdated</span>
                        </div>
                      )}
                    </td>
                    <td>
                      <span className={`sl-pill sl-pill--${source.status.toLowerCase()}`}>
                        {STATUS_LABEL[source.status]}
                      </span>
                      {source.approved_by && source.status === "APPROVED" && (
                        <div className="sl-sub">by {source.approved_by}</div>
                      )}
                    </td>
                    {canManage && (
                      <td>
                        <div className="sl-row-actions">
                          <ReasonAction
                            label={source.status === "DRAFT" ? "Approve" : null}
                            tone="primary"
                            onConfirm={(reason) =>
                              act(() => approveSource(source.id, reason), `“${source.title}” approved.`)
                            }
                          />
                          <ReasonAction
                            label={source.status !== "RETIRED" ? "Retire" : null}
                            tone="danger"
                            onConfirm={(reason) =>
                              act(() => retireSource(source.id, reason), `“${source.title}” retired.`)
                            }
                          />
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

function formatSize(bytes: number | null | undefined): string {
  if (!bytes) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The original uploaded document of a source, with a download button. */
function OriginalFile({ source, onError }: { source: ApprovedSource; onError: (text: string) => void }) {
  const [busy, setBusy] = useState(false);
  const name = source.original_filename ?? "document";
  return (
    <div className="sl-original">
      <span className="sl-original__name" title={source.file_sha256 ? `SHA-256 ${source.file_sha256}` : undefined}>
        <span aria-hidden="true">📎 </span>
        {name}
        {source.file_size ? <span className="sl-original__size"> · {formatSize(source.file_size)}</span> : null}
      </span>
      <button
        type="button"
        className="sl-btn sl-btn--small sl-btn--secondary"
        aria-label={`Download ${name}`}
        disabled={busy}
        onClick={async () => {
          setBusy(true);
          try {
            await downloadSourceFile(source.id, name);
          } catch (err) {
            onError(friendlyError(err, "The document couldn't be downloaded."));
          } finally {
            setBusy(false);
          }
        }}
      >
        {busy ? "Downloading…" : "Download"}
      </button>
    </div>
  );
}

function ReasonAction({
  label,
  tone,
  onConfirm,
}: {
  label: string | null;
  tone: "primary" | "danger";
  onConfirm: (reason: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  if (!label) return null;
  if (!open) {
    return (
      <button
        type="button"
        className={`sl-btn sl-btn--small ${tone === "danger" ? "sl-btn--danger" : "sl-btn--secondary"}`}
        onClick={() => setOpen(true)}
      >
        {label}
      </button>
    );
  }
  return (
    <div className="sl-reason">
      <input
        aria-label={`Reason to ${label.toLowerCase()}`}
        placeholder="Reason (required)"
        value={reason}
        autoFocus
        onChange={(event) => setReason(event.target.value)}
      />
      <div className="sl-reason__buttons">
        <button
          type="button"
          className={`sl-btn sl-btn--small ${tone === "danger" ? "sl-btn--danger" : "sl-btn--primary"}`}
          disabled={!reason.trim()}
          onClick={() => {
            onConfirm(reason.trim());
            setOpen(false);
            setReason("");
          }}
        >
          {label}
        </button>
        <button type="button" className="sl-btn sl-btn--small sl-btn--secondary" onClick={() => setOpen(false)}>
          Cancel
        </button>
      </div>
    </div>
  );
}

function SourceForm({
  onSave,
  onCancel,
}: {
  onSave: (fields: SourceFields, file: File | null) => void;
  onCancel: () => void;
}) {
  // The original document, kept with the source when it is saved.
  const [file, setFile] = useState<File | null>(null);
  const [fields, setFields] = useState<SourceFields>(EMPTY);
  const [touched, setTouched] = useState(false);
  const [upload, setUpload] = useState<SourceExtraction | null>(null);
  const [reading, setReading] = useState(false);
  const [uploadError, setUploadError] = useState("");
  const [dragging, setDragging] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const id = useId();
  const missing = (["title", "version", "content"] as const).filter((key) => !fields[key]?.trim());

  async function readFile(chosen: File | undefined) {
    if (!chosen) return;
    setReading(true);
    setUploadError("");
    try {
      const extracted = await extractSourceFile(chosen);
      setUpload(extracted);
      setFile(chosen);
      setFields((current) => ({
        ...current,
        content: extracted.content,
        title: current.title?.trim() ? current.title : extracted.title,
        reference: current.reference?.trim() ? current.reference : extracted.filename,
      }));
    } catch (err) {
      setUploadError(friendlyError(err, "That document couldn't be read."));
    } finally {
      setReading(false);
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    void readFile(event.dataTransfer.files?.[0]);
  }

  function field(key: keyof SourceFields, label: string, type = "text", hint?: string) {
    const invalid = touched && missing.includes(key as (typeof missing)[number]);
    return (
      <div className="sl-field">
        <label htmlFor={`${id}-${key}`}>{label}</label>
        <input
          id={`${id}-${key}`}
          type={type}
          value={fields[key] ?? ""}
          aria-invalid={invalid || undefined}
          onChange={(event) => setFields((current) => ({ ...current, [key]: event.target.value }))}
        />
        {invalid ? <small className="sl-error" role="alert">Required.</small> : hint && <small>{hint}</small>}
      </div>
    );
  }

  return (
    <section className="sl-card" aria-labelledby={`${id}-heading`}>
      <h3 className="sl-card__title" id={`${id}-heading`}>
        Add a source
      </h3>
      <p className="sl-card__subtitle">
        Upload a document to read its text, or paste the text below. It is saved as a draft; approve it to use it as
        evidence.
      </p>

      <label
        className={`sl-dropzone${dragging ? " sl-dropzone--active" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <span className="sl-dropzone__icon" aria-hidden="true">
          📄
        </span>
        <strong>{reading ? "Reading the document…" : "Drop a document here, or click to choose a file"}</strong>
        <small>PDF, Word (DOCX / DOC), Excel (XLSX), TXT or CSV · up to 50 MB</small>
        <input
          ref={fileInput}
          type="file"
          accept={SOURCE_UPLOAD_ACCEPT}
          aria-label="Upload a source document"
          disabled={reading}
          onChange={(event) => void readFile(event.target.files?.[0])}
        />
      </label>

      {uploadError && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{uploadError}</span>
        </p>
      )}
      {upload && (
        <div className="sl-file-chip" role="status">
          <span>
            ✓ Read <strong>{upload.filename}</strong> · {upload.characters.toLocaleString()} characters. Review the
            text below; the original file is kept with the source.
          </span>
          <button
            type="button"
            className="sl-btn sl-btn--small sl-btn--secondary"
            onClick={() => {
              setUpload(null);
              setFile(null);
              setFields((current) => ({ ...current, content: "" }));
            }}
          >
            Remove
          </button>
        </div>
      )}

      <div className="sl-form-grid">
        {field("title", "Title (required)")}
        <div className="sl-field">
          <label htmlFor={`${id}-type`}>Type</label>
          <select
            id={`${id}-type`}
            value={fields.source_type}
            onChange={(event) => setFields((current) => ({ ...current, source_type: event.target.value }))}
          >
            {SOURCE_TYPES.map((type) => (
              <option key={type.value} value={type.value}>
                {type.label}
              </option>
            ))}
          </select>
        </div>
        {field("issuer", "Issuer", "text", "e.g. Group Compliance, FATF")}
        {field("version", "Version (required)")}
        {field("effective_date", "Publication / effective date", "date")}
        {field("review_date", "Review by", "date", "The source is flagged as outdated after this date.")}
        <div className="sl-field sl-field--full">
          <label htmlFor={`${id}-reference`}>Link or document reference</label>
          <input
            id={`${id}-reference`}
            value={fields.reference ?? ""}
            onChange={(event) => setFields((current) => ({ ...current, reference: event.target.value }))}
          />
        </div>
        <div className="sl-field sl-field--full">
          <label htmlFor={`${id}-content`}>Text (required)</label>
          <textarea
            id={`${id}-content`}
            rows={10}
            value={fields.content}
            aria-invalid={(touched && missing.includes("content")) || undefined}
            placeholder="Paste the policy or guidance, or upload a document above."
            onChange={(event) => setFields((current) => ({ ...current, content: event.target.value }))}
          />
          {touched && missing.includes("content") ? (
            <small className="sl-error" role="alert">Required.</small>
          ) : (
            <small>It is searched passage by passage; keep paragraphs separated by blank lines.</small>
          )}
        </div>
      </div>

      <div className="sl-actions">
        <button type="button" className="sl-btn sl-btn--secondary" onClick={onCancel}>
          Cancel
        </button>
        <button
          type="button"
          className="sl-btn sl-btn--primary"
          disabled={reading}
          onClick={() => {
            setTouched(true);
            if (missing.length) return;
            onSave({
              ...fields,
              issuer: fields.issuer?.trim() || null,
              effective_date: fields.effective_date || null,
              review_date: fields.review_date || null,
              reference: fields.reference?.trim() || null,
            }, file);
          }}
        >
          Save as draft
        </button>
      </div>
    </section>
  );
}
