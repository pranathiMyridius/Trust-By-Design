import { useId, useState } from "react";

import { addControlCondition } from "../api/assessments";
import { friendlyError } from "../utils/errorMessages";

/**
 * R7.7: record a required control enhancement or condition against one
 * mapped control. Open conditions are carried into the assessment draft
 * and tracked to completion.
 */
export default function ControlConditionForm({
  assessmentId,
  controlId,
  onAdded,
}: {
  assessmentId: number;
  controlId: number;
  onAdded: () => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [description, setDescription] = useState("");
  const [owner, setOwner] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [touched, setTouched] = useState(false);
  const id = useId();
  const missing = !description.trim();

  async function save() {
    setTouched(true);
    if (missing) return;
    setSaving(true);
    setError("");
    try {
      await addControlCondition(assessmentId, controlId, {
        description: description.trim(),
        owner: owner.trim() || null,
        due_date: dueDate || null,
      });
      setDescription("");
      setOwner("");
      setDueDate("");
      setTouched(false);
      setOpen(false);
      await onAdded();
    } catch (err) {
      setError(friendlyError(err, "The condition couldn't be added."));
    } finally {
      setSaving(false);
    }
  }

  if (!open) {
    return (
      <button type="button" className="secondary-button" style={{ marginTop: 6 }} onClick={() => setOpen(true)}>
        + Add condition / enhancement
      </button>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, marginTop: 6 }}>
      <label htmlFor={`${id}-description`}>Required enhancement or condition</label>
      <textarea
        id={`${id}-description`}
        rows={2}
        value={description}
        onChange={(event) => setDescription(event.target.value)}
        aria-invalid={touched && missing}
        aria-describedby={touched && missing ? `${id}-description-error` : undefined}
      />
      {touched && missing && (
        <small id={`${id}-description-error`} role="alert" style={{ color: "#b91c1c" }}>
          Describe the condition.
        </small>
      )}
      <label htmlFor={`${id}-owner`}>Owner (optional)</label>
      <input id={`${id}-owner`} value={owner} onChange={(event) => setOwner(event.target.value)} />
      <label htmlFor={`${id}-due`}>Due date (optional)</label>
      <input id={`${id}-due`} type="date" value={dueDate} onChange={(event) => setDueDate(event.target.value)} />
      {error && (
        <p role="alert" style={{ color: "#b91c1c" }}>
          {error}
        </p>
      )}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="button" className="primary-button" onClick={save} disabled={saving}>
          {saving ? "Saving..." : "Save condition"}
        </button>
        <button type="button" className="secondary-button" onClick={() => setOpen(false)} disabled={saving}>
          Cancel
        </button>
      </div>
    </div>
  );
}
