/*
 * Stage 19 (accessibility): shared building blocks for clear,
 * field-level form validation.
 *
 *  - RequiredMarker: visible "*" plus screen-reader text "required".
 *  - FieldError / FieldWarning: inline message under a field; give the
 *    field aria-describedby={id} (and aria-invalid for errors).
 *  - ErrorSummary: role="alert" list shown at the top of a form after a
 *    failed submit; each entry focuses the invalid field.
 */

export function RequiredMarker() {
  return (
    <>
      <span className="required-marker" aria-hidden="true">
        *
      </span>
      <span className="sr-only"> (required)</span>
    </>
  );
}

export function FieldError({ id, message }: { id: string; message?: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <span id={id} className="field-error">
      <span aria-hidden="true">⚠</span>
      <span>
        <span className="sr-only">Error: </span>
        {message}
      </span>
    </span>
  );
}

export function FieldWarning({ id, message }: { id: string; message?: string | null }) {
  if (!message) {
    return null;
  }
  return (
    <span id={id} className="field-warning">
      <span aria-hidden="true">ⓘ</span>
      <span>
        <span className="sr-only">Warning: </span>
        {message}
      </span>
    </span>
  );
}

export interface SummaryItem {
  fieldId: string;
  message: string;
}

export function ErrorSummary({
  id = "form-error-summary",
  title = "Please fix the following before submitting:",
  items,
}: {
  id?: string;
  title?: string;
  items: SummaryItem[];
}) {
  if (items.length === 0) {
    return null;
  }
  return (
    <div id={id} className="error-summary" role="alert" tabIndex={-1}>
      <h3>
        <span aria-hidden="true">⚠ </span>
        {title}
      </h3>
      <ul>
        {items.map((item) => (
          <li key={item.fieldId}>
            <a
              href={`#${item.fieldId}`}
              onClick={(event) => {
                event.preventDefault();
                const field = document.getElementById(item.fieldId);
                field?.scrollIntoView({ block: "center" });
                field?.focus();
              }}
            >
              {item.message}
            </a>
          </li>
        ))}
      </ul>
    </div>
  );
}
