import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** An accessible modal dialog: Esc and the backdrop close it, focus moves in and is restored on close. */
export function Modal({
  title,
  onClose,
  children,
  footer,
  wide = false,
}: {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  wide?: boolean;
}) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const first = panel.current?.querySelector<HTMLElement>(
      "input:not([type=hidden]):not([disabled]), select, textarea, button:not([disabled])",
    );
    (first ?? panel.current)?.focus();
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      // Keep Tab inside the dialog.
      const items = Array.from(
        panel.current.querySelectorAll<HTMLElement>(
          "a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (items.length === 0) return;
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      if (event.shiftKey && document.activeElement === firstItem) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && document.activeElement === lastItem) {
        event.preventDefault();
        firstItem.focus();
      }
    }
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
    // Mount-only: the dialog's focus handling must not restart when the parent re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div
      className="slg-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        className={`slg-modal${wide ? " slg-modal--wide" : ""}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        ref={panel}
      >
        <header className="slg-modal__header">
          <h3 id={titleId}>{title}</h3>
          <button type="button" className="slg-modal__close" aria-label="Close dialog" onClick={onClose}>
            ×
          </button>
        </header>
        <div className="slg-modal__body">{children}</div>
        {footer && <footer className="slg-modal__footer">{footer}</footer>}
      </div>
    </div>
  );
}

/** A dialog that asks for a comment/reason (approve, reject, withdraw, retire ...). */
export function CommentDialog({
  title,
  intro,
  label,
  confirmLabel,
  minLength,
  tone = "primary",
  onConfirm,
  onClose,
}: {
  title: string;
  intro?: ReactNode;
  label: string;
  confirmLabel: string;
  minLength: number;
  tone?: "primary" | "danger";
  onConfirm: (comment: string) => Promise<void>;
  onClose: () => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const id = useId();
  const long = text.trim().length >= minLength;

  async function submit() {
    setBusy(true);
    setError("");
    try {
      await onConfirm(text.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "That didn't work. Please try again.");
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <button type="button" className="sl-btn sl-btn--secondary" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className={`sl-btn ${tone === "danger" ? "sl-btn--danger" : "sl-btn--primary"}`}
            disabled={!long || busy}
            onClick={() => void submit()}
          >
            {busy ? "Working…" : confirmLabel}
          </button>
        </>
      }
    >
      {intro && <div className="slg-intro">{intro}</div>}
      <div className="sl-field">
        <label htmlFor={`${id}-comment`}>{label}</label>
        <textarea id={`${id}-comment`} rows={4} value={text} onChange={(event) => setText(event.target.value)} />
        <small>
          {long ? "Recorded in the approval history and audit trail." : `At least ${minLength} characters (${text.trim().length} so far).`}
        </small>
      </div>
      {error && (
        <p className="sl-banner sl-banner--error" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
        </p>
      )}
    </Modal>
  );
}
