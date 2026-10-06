import { useId, useRef, useState, type DragEvent } from "react";

import {
  addVersion,
  createRecord,
  updateRecord,
  updateVersion,
  replaceFile,
  type LibraryMeta,
  type RecordFields,
  type SourceRecordDetail,
  type SourceVersion,
  type VersionFields,
} from "../api/sourceLibrary";
import { Modal } from "./SourceModal";
import { formatSize } from "./sourceLibraryUi";

type Mode =
  | { kind: "create" }
  | { kind: "edit"; record: SourceRecordDetail }
  | { kind: "version"; record: SourceRecordDetail }
  | { kind: "editVersion"; record: SourceRecordDetail; version: SourceVersion };

const EMPTY_RECORD: RecordFields = {
  source_code: "",
  title: "",
  authority: "",
  category: "REGULATORY_REQUIREMENT",
  jurisdiction: "",
  applicable_entity: "",
  source_url: "",
  description: "",
  topics: "",
  owner: "",
  review_frequency: "",
};

const EMPTY_VERSION: VersionFields = {
  version_label: "",
  effective_date: "",
  review_date: "",
  retrieved_date: "",
  change_summary: "",
  source_url: "",
};

function validUrl(value: string): boolean {
  if (!value.trim()) return true;
  return /^https?:\/\/\S+$/i.test(value.trim());
}

/** Create / edit a source, add a version, or edit a draft version. Official link and/or PDF upload. */
export default function SourceFormModal({
  mode,
  meta,
  onClose,
  onSaved,
}: {
  mode: Mode;
  meta: LibraryMeta;
  onClose: () => void;
  onSaved: (record: SourceRecordDetail | null, message: string) => void;
}) {
  const id = useId();
  const editingRecord = mode.kind === "edit" ? mode.record : null;
  const editingVersion = mode.kind === "editVersion" ? mode.version : null;
  const showRecordFields = mode.kind === "create" || mode.kind === "edit";
  const showVersionFields = mode.kind !== "edit";
  const showFile = mode.kind !== "edit";

  const [record, setRecord] = useState<RecordFields>(
    editingRecord
      ? {
          source_code: editingRecord.source_code,
          title: editingRecord.title,
          authority: editingRecord.authority,
          category: editingRecord.category,
          jurisdiction: editingRecord.jurisdiction ?? "",
          applicable_entity: editingRecord.applicable_entity ?? "",
          source_url: editingRecord.source_url ?? "",
          description: editingRecord.description ?? "",
          topics: editingRecord.topics.join(", "),
          owner: editingRecord.owner ?? "",
          review_frequency: editingRecord.review_frequency ?? "",
        }
      : EMPTY_RECORD,
  );
  const [version, setVersion] = useState<VersionFields>(
    editingVersion
      ? {
          version_label: editingVersion.version_label,
          effective_date: editingVersion.effective_date ?? "",
          review_date: editingVersion.review_date ?? "",
          retrieved_date: editingVersion.retrieved_date ?? "",
          change_summary: editingVersion.change_summary ?? "",
          source_url: editingVersion.source_url ?? "",
        }
      : EMPTY_VERSION,
  );
  const [changeReason, setChangeReason] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);

  const scopeInUse =
    editingRecord?.current_version != null &&
    (record.category !== editingRecord.category ||
      (record.jurisdiction || "") !== (editingRecord.jurisdiction ?? "") ||
      record.topics.trim() !== editingRecord.topics.join(", "));

  const problems: Record<string, string> = {};
  if (showRecordFields) {
    if (!record.title.trim()) problems.title = "Title is required.";
    if (!record.authority.trim()) problems.authority = "Source authority is required.";
    if (!validUrl(record.source_url)) problems.source_url = "Enter a web address starting with http:// or https://";
  }
  if (showVersionFields) {
    if (!version.version_label.trim()) problems.version_label = "A version label is required.";
    if (!validUrl(version.source_url)) problems.version_url = "Enter a web address starting with http:// or https://";
  }
  if (scopeInUse && changeReason.trim().length < meta.min_comment_length) {
    problems.change_reason = `Give a reason (at least ${meta.min_comment_length} characters) — this source is in use.`;
  }

  function choose(chosen: File | undefined) {
    if (!chosen) return;
    setFileError("");
    if (!chosen.name.toLowerCase().endsWith(".pdf")) {
      setFileError("Only PDF documents (.pdf) can be uploaded.");
      return;
    }
    if (chosen.size > meta.max_upload_mb * 1024 * 1024) {
      setFileError(`${chosen.name} is ${formatSize(chosen.size)}, over the ${meta.max_upload_mb} MB limit.`);
      return;
    }
    setFile(chosen);
  }

  function onDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDragging(false);
    choose(event.dataTransfer.files?.[0]);
  }

  async function save() {
    setTouched(true);
    if (Object.keys(problems).length) return;
    setBusy(true);
    setError("");
    try {
      if (mode.kind === "create") {
        const created = await createRecord(record, version, file);
        onSaved(created, `Source ${created.source_code} saved as a draft. Submit it for review to make it usable.`);
      } else if (mode.kind === "edit") {
        const topics = record.topics.split(/[,;\n]/).map((item) => item.trim()).filter(Boolean);
        const updated = await updateRecord(mode.record.id, {
          title: record.title,
          authority: record.authority,
          category: record.category,
          jurisdiction: record.jurisdiction,
          applicable_entity: record.applicable_entity,
          source_url: record.source_url,
          description: record.description,
          topics,
          owner: record.owner,
          review_frequency: record.review_frequency,
          change_reason: changeReason.trim() || undefined,
        });
        onSaved(updated, "Source details saved.");
      } else if (mode.kind === "version") {
        const updated = await addVersion(mode.record.id, version, file);
        onSaved(updated, "New draft version added. The approved version stays in force until the new one is approved.");
      } else {
        await updateVersion(mode.record.id, mode.version.id, version);
        if (file) await replaceFile(mode.record.id, mode.version.id, file);
        onSaved(null, "Version saved.");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "The source couldn't be saved.");
      setBusy(false);
    }
  }

  function text(
    group: "record" | "version",
    key: string,
    label: string,
    opts: { type?: string; hint?: string; full?: boolean; required?: boolean; problem?: string; readOnly?: boolean } = {},
  ) {
    const values = (group === "record" ? record : version) as unknown as Record<string, string>;
    const update = (value: string) =>
      group === "record" ? setRecord((current) => ({ ...current, [key]: value })) : setVersion((current) => ({ ...current, [key]: value }));
    const shown = touched ? opts.problem : undefined;
    return (
      <div className={`sl-field${opts.full ? " sl-field--full" : ""}`}>
        <label htmlFor={`${id}-${group}-${key}`}>
          {label}
          {opts.required ? " (required)" : ""}
        </label>
        <input
          id={`${id}-${group}-${key}`}
          type={opts.type ?? "text"}
          value={values[key] ?? ""}
          readOnly={opts.readOnly}
          aria-invalid={shown ? true : undefined}
          onChange={(event) => update(event.target.value)}
        />
        {shown ? (
          <small className="sl-error" role="alert">
            {shown}
          </small>
        ) : (
          opts.hint && <small>{opts.hint}</small>
        )}
      </div>
    );
  }

  const title =
    mode.kind === "create"
      ? "Add source"
      : mode.kind === "edit"
        ? `Edit ${mode.record.source_code}`
        : mode.kind === "version"
          ? `Add version — ${mode.record.title}`
          : `Edit version ${mode.version.version_label}`;

  return (
    <Modal
      title={title}
      wide
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <button type="button" className="sl-btn sl-btn--secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="sl-btn sl-btn--primary" onClick={() => void save()} disabled={busy}>
            {busy ? "Saving…" : mode.kind === "edit" || mode.kind === "editVersion" ? "Save changes" : "Save as draft"}
          </button>
        </>
      }
    >
      {mode.kind === "create" && (
        <p className="slg-intro">
          The source is saved as a draft. It becomes usable as assessment evidence only after an authorised compliance reviewer
          ({meta.reviewer_rule}) approves it.
        </p>
      )}

      {showRecordFields && (
        <fieldset className="slg-fieldset">
          <legend>Source details</legend>
          <div className="sl-form-grid">
            {text("record", "title", "Title", { required: true, problem: problems.title, full: true })}
            {text("record", "authority", "Source authority", { required: true, problem: problems.authority, hint: "e.g. Reserve Bank of India, FATF, Group Compliance" })}
            <div className="sl-field">
              <label htmlFor={`${id}-category`}>Category</label>
              <select id={`${id}-category`} value={record.category} onChange={(event) => setRecord({ ...record, category: event.target.value })}>
                {meta.categories.map((category) => (
                  <option key={category.value} value={category.value}>
                    {category.label}
                  </option>
                ))}
              </select>
            </div>
            {text("record", "jurisdiction", "Jurisdiction", { hint: `e.g. India, European Union, Global. Blank applies everywhere.` })}
            {text("record", "applicable_entity", "Applicable entity", { hint: "e.g. Indian Regulated Entity" })}
            {text("record", "owner", "Source owner", { hint: "Accountable for keeping it current" })}
            {text("record", "review_frequency", "Review frequency", { hint: "e.g. Quarterly, or when the publisher updates it" })}
            {mode.kind === "create" && text("record", "source_code", "Source ID", { hint: "Optional, e.g. SRC-RBI-KYC-001. Generated if left blank." })}
            {text("record", "source_url", "Official URL", { type: "url", full: true, problem: problems.source_url, hint: "The publisher's page — the official source of truth." })}
            {text("record", "topics", "Topics", { full: true, hint: "Comma-separated, e.g. KYC, CDD, Sanctions screening" })}
            <div className="sl-field sl-field--full">
              <label htmlFor={`${id}-description`}>Description</label>
              <textarea id={`${id}-description`} rows={3} value={record.description} onChange={(event) => setRecord({ ...record, description: event.target.value })} />
            </div>
            {scopeInUse && (
              <div className="sl-field sl-field--full">
                <label htmlFor={`${id}-reason`}>Reason for changing where this source applies (required)</label>
                <textarea id={`${id}-reason`} rows={2} value={changeReason} aria-invalid={touched && problems.change_reason ? true : undefined} onChange={(event) => setChangeReason(event.target.value)} />
                <small className={touched && problems.change_reason ? "sl-error" : undefined}>
                  An approved version is in use; changing category, jurisdiction or topics changes which assessments see it. Recorded in the audit trail.
                </small>
              </div>
            )}
          </div>
        </fieldset>
      )}

      {showVersionFields && (
        <fieldset className="slg-fieldset">
          <legend>{mode.kind === "create" ? "First version" : "Version"}</legend>
          <div className="sl-form-grid">
            {text("version", "version_label", "Version label", { required: true, problem: problems.version_label, hint: "As the publisher names it, e.g. 2016 (updated 12 Jun 2025)" })}
            {text("version", "effective_date", "Effective date", { type: "date" })}
            {text("version", "review_date", "Review by", { type: "date", hint: "Flagged as outdated after this date." })}
            {text("version", "retrieved_date", "Date captured", { type: "date", hint: "When a controlled copy was downloaded." })}
            {text("version", "source_url", "Version URL", { type: "url", full: true, problem: problems.version_url, hint: "If this version has its own address." })}
            <div className="sl-field sl-field--full">
              <label htmlFor={`${id}-summary`}>What changed</label>
              <textarea id={`${id}-summary`} rows={2} value={version.change_summary} onChange={(event) => setVersion({ ...version, change_summary: event.target.value })} />
            </div>
          </div>
        </fieldset>
      )}

      {showFile && (
        <fieldset className="slg-fieldset">
          <legend>Controlled copy (PDF)</legend>
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
            <strong>{file ? file.name : "Drop a PDF here, or click to choose a file"}</strong>
            <small>
              {file ? `${formatSize(file.size)} — scanned for malware and its text extracted when saved` : `PDF only · up to ${meta.max_upload_mb} MB · optional if you link the official page`}
            </small>
            <input ref={input} type="file" accept=".pdf,application/pdf" aria-label="Upload a PDF document" onChange={(event) => choose(event.target.files?.[0])} />
          </label>
          {fileError && (
            <p className="sl-banner sl-banner--error" role="alert">
              <span aria-hidden="true">✕</span>
              <span>{fileError}</span>
            </p>
          )}
          {file && (
            <button
              type="button"
              className="sl-btn sl-btn--small sl-btn--secondary"
              onClick={() => {
                setFile(null);
                if (input.current) input.current.value = "";
              }}
            >
              Remove document
            </button>
          )}
          {!meta.antivirus_configured && (
            <p className="slg-note">
              {meta.malware_scan_required
                ? "No antivirus engine is configured, so uploads will be refused until one is."
                : "No antivirus engine is configured; only built-in checks are applied to uploads."}
            </p>
          )}
        </fieldset>
      )}

      {error && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
        </p>
      )}
    </Modal>
  );
}

export type { Mode as SourceFormMode };
