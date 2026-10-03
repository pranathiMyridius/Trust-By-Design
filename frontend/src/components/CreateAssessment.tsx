import { useEffect, useRef, useState, type ReactNode } from "react";
import { flushSync } from "react-dom";
import "./CreateAssessment.css";
import {
  CHANGE_TYPES,
  LEGACY_CHANGE_TYPES,
  checkDuplicateAssessments,
  parseMissingFieldsDetail,
  uploadAssessmentDocument,
} from "../api/assessments";
import type {
  Assessment,
  AssessmentRequestFields,
  DuplicateMatch,
} from "../api/assessments";
import NavIcon from "./NavIcons";
import { API_BASE_URL } from "../api/config";
import { authFetch } from "../api/http";
import { friendlyError } from "../utils/errorMessages";
import {
  ErrorSummary,
  FieldError,
  FieldWarning,
  RequiredMarker,
  type SummaryItem,
} from "./FormFeedback";

// Dynamic/conditional data capture: per change_type, the R1.1 fields that
// are especially load-bearing for that kind of request. Every field is
// still shown (mandatory-field validation covers the same full set
// regardless of change_type — see MANDATORY_INTAKE_FIELDS on the
// backend), but these are visually emphasized so the submitter fills
// them in with the right level of detail for the request at hand.
const CHANGE_TYPE_KEY_FIELDS: Record<
  string,
  (keyof AssessmentRequestFields)[]
> = {
  NEW_GEOGRAPHY: ["countries_jurisdictions", "delivery_channels"],
  THIRD_PARTY_INTRODUCTION: ["third_party_vendor_usage"],
  TECHNOLOGY_CHANGE: ["technology_process_changes"],
  NEW_CUSTOMER_SEGMENT: ["customer_segment", "delivery_channels"],
  TRANSACTION_LIMIT_OR_CHANNEL_CHANGE: [
    "delivery_channels",
    "expected_transaction_volume",
    "expected_transaction_value",
  ],
  NEW_PRODUCT: ["product_or_service_name", "transaction_types"],
  NEW_SERVICE: ["product_or_service_name", "transaction_types"],
};

/*
 * Stage 19: mandatory intake fields for a full submit (not a draft).
 * Mirrors MANDATORY_INTAKE_FIELDS on the backend, in on-screen order so
 * the error summary and "focus first invalid field" follow the form.
 */
type IntakeField =
  | "title"
  | "change_type"
  | "description"
  | "product_or_service_name"
  | "business_owner"
  | "legal_entity"
  | "customer_segment"
  | "countries_jurisdictions"
  | "delivery_channels"
  | "expected_transaction_volume"
  | "expected_transaction_value"
  | "transaction_types"
  | "third_party_vendor_usage"
  | "technology_process_changes"
  | "expected_launch_date";

const MANDATORY_INTAKE_FIELDS: { key: IntakeField; label: string }[] = [
  { key: "title", label: "Assessment title" },
  { key: "change_type", label: "Business change type" },
  { key: "product_or_service_name", label: "Product or service name" },
  { key: "description", label: "Business description" },
  { key: "business_owner", label: "Business owner" },
  { key: "legal_entity", label: "Legal entity / business unit" },
  { key: "customer_segment", label: "Customer segment" },
  { key: "countries_jurisdictions", label: "Countries and jurisdictions involved" },
  { key: "delivery_channels", label: "Delivery channels" },
  { key: "expected_transaction_volume", label: "Expected transaction volume" },
  { key: "expected_transaction_value", label: "Expected transaction value" },
  { key: "transaction_types", label: "Transaction types" },
  { key: "third_party_vendor_usage", label: "Use of third parties or vendors" },
  { key: "technology_process_changes", label: "Technology or process changes" },
  { key: "expected_launch_date", label: "Expected launch or implementation date" },
];

function fieldId(field: FormField): string {
  return `intake-${field}`;
}

function fieldErrorId(field: IntakeField): string {
  return `intake-${field}-error`;
}

function parseIsoDate(value: string): Date | null {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) {
    return null;
  }
  const date = new Date(`${value}T00:00:00`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function validateIntake(
  values: Record<IntakeField, string>
): Partial<Record<IntakeField, string>> {
  const errors: Partial<Record<IntakeField, string>> = {};

  for (const field of MANDATORY_INTAKE_FIELDS) {
    if (!(values[field.key] ?? "").trim()) {
      errors[field.key] =
        field.key === "third_party_vendor_usage"
          ? `${field.label} is required — enter "None" if no third parties are involved.`
          : `${field.label} is required.`;
    }
  }

  const launch = (values.expected_launch_date ?? "").trim();
  if (launch && !parseIsoDate(launch)) {
    errors.expected_launch_date =
      "Expected launch date must be a real calendar date — use the date picker.";
  }

  return errors;
}

// Warning only (not blocking): a launch date in the past is allowed but
// probably a typo.
function launchDateInPastWarning(value: string): string | null {
  const date = parseIsoDate((value ?? "").trim());
  if (!date) {
    return null;
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return date < today
    ? "This date is in the past. Check it's correct before submitting."
    : null;
}

/*
 * Every form field, including the optional ones outside the mandatory
 * set. Keys double as the input ids (`intake-<key>`).
 */
type FormField = IntakeField | "business_unit" | "evidence" | "shell_company_indicator";

interface FieldSpec {
  key: FormField;
  label: string;
  kind?: "input" | "textarea" | "select" | "date" | "toggle";
  placeholder?: string;
  rows?: number;
  wide?: boolean;
  hint?: string;
}

/* The intake form's collapsible sections, in on-screen order. */
const SECTIONS: { title: string; fields: FieldSpec[] }[] = [
  {
    title: "Product Information",
    fields: [
      { key: "title", label: "Assessment Title", placeholder: "e.g. Launch Fleet Platform in Germany", wide: true },
      { key: "change_type", label: "Business Change Type", kind: "select" },
      { key: "product_or_service_name", label: "Product or Service Name", placeholder: "e.g. Fleet Management Platform" },
      { key: "legal_entity", label: "Legal Entity Name", placeholder: "e.g. Fleet Europe B.V." },
      { key: "business_unit", label: "Business Unit (optional)", placeholder: "e.g. Merchant Services" },
      { key: "business_owner", label: "Business Owner", placeholder: "e.g. Jane Smith, Head of Product" },
      { key: "shell_company_indicator", label: "Shell Company Indicator Status", kind: "toggle" },
    ],
  },
  {
    title: "Context & Background",
    fields: [
      {
        key: "description",
        label: "Business Description",
        kind: "textarea",
        rows: 5,
        wide: true,
        placeholder: "Describe the proposed business change… (optional if saving as a draft)",
      },
      {
        key: "technology_process_changes",
        label: "Technology or Process Changes",
        kind: "textarea",
        rows: 3,
        wide: true,
        placeholder: "Describe any technology or process changes involved.",
      },
      { key: "expected_launch_date", label: "Expected Launch or Implementation Date", kind: "date" },
    ],
  },
  {
    title: "Geography & Jurisdictions",
    fields: [
      {
        key: "countries_jurisdictions",
        label: "Countries and Jurisdictions Involved",
        placeholder: "e.g. Germany, Netherlands",
        wide: true,
      },
    ],
  },
  {
    title: "Customer Profile",
    fields: [{ key: "customer_segment", label: "Customer Segment", placeholder: "e.g. SME fleet operators", wide: true }],
  },
  {
    title: "Channels & Delivery",
    fields: [
      { key: "delivery_channels", label: "Delivery Channels", placeholder: "e.g. Mobile app, Web portal, API", wide: true },
      { key: "transaction_types", label: "Transaction Types", placeholder: "e.g. Card payments, Bank transfers", wide: true },
      { key: "expected_transaction_volume", label: "Expected Transaction Volume", placeholder: "e.g. 10,000 transactions/month" },
      { key: "expected_transaction_value", label: "Expected Transaction Value", placeholder: "e.g. €2M/month" },
    ],
  },
  {
    title: "Third-Party Relationships",
    fields: [
      {
        key: "third_party_vendor_usage",
        label: "Use of Third Parties or Vendors",
        kind: "textarea",
        rows: 3,
        wide: true,
        placeholder: "Describe any third parties or vendors involved, or enter 'None'.",
      },
    ],
  },
  {
    title: "Financial Crime Context",
    fields: [
      {
        key: "evidence",
        label: "Evidence & Known Risk Indicators",
        kind: "textarea",
        rows: 6,
        wide: true,
        hint: "Supporting facts, known impacts, adverse media or sanctions exposure.",
        placeholder: "Provide supporting evidence, facts, or known impacts… (optional if saving as a draft)",
      },
    ],
  },
];

const MANDATORY_KEYS = new Set<FormField>(MANDATORY_INTAKE_FIELDS.map((field) => field.key));

const FIELD_ORDER: FormField[] = SECTIONS.flatMap((section) => section.fields.map((spec) => spec.key));

/* Mandatory fields in on-screen order (for the error summary and focus). */
const MANDATORY_IN_FORM_ORDER = [...MANDATORY_INTAKE_FIELDS].sort(
  (a, b) => FIELD_ORDER.indexOf(a.key) - FIELD_ORDER.indexOf(b.key)
);

function sectionIndexOf(field: FormField): number {
  return SECTIONS.findIndex((section) => section.fields.some((spec) => spec.key === field));
}

interface CreateAssessmentProps {
  /** `assessment` is the created record, or null for a saved draft. */
  onCreated: (assessment: Assessment | null) => void;
  onCancel: () => void;
  /** R1.3: a saved draft to reopen and complete; omitted for a new request. */
  draft?: Assessment | null;
}

interface ExtractedAssessment {
  title: string;
  change_type: string;
  business_description: string;
  evidence: string;
  // R1.1 fields extracted directly from the document.
  product_or_service_name?: string | null;
  business_owner?: string | null;
  legal_entity?: string | null;
  transaction_types?: string | null;
  expected_launch_date?: string | null;
  // R1.5: who the document itself names as the submitter (e.g. a
  // "Submitted By:" line), if any.
  submitted_by?: string | null;
  // R1.1 fields the backend returns as lists/alternate names; the form
  // combines these into single strings below.
  channels?: string[];
  countries?: string[];
  customer_segments?: string[];
  transaction_volume?: string | null;
  average_transaction_size?: string | null;
  maximum_transaction_limit?: string | null;
  third_party_vendors?: string[];
  technologies?: string[];
}

const EMPTY_REQUEST_FIELDS: AssessmentRequestFields = {
  product_or_service_name: "",
  business_owner: "",
  legal_entity: "",
  business_unit: "",
  customer_segment: "",
  countries_jurisdictions: "",
  delivery_channels: "",
  expected_transaction_volume: "",
  expected_transaction_value: "",
  transaction_types: "",
  third_party_vendor_usage: "",
  technology_process_changes: "",
  expected_launch_date: "",
  shell_company_indicator: null,
};

/** The R1.1 request fields of an existing assessment, for editing a draft. */
function requestFieldsOf(assessment: Assessment): AssessmentRequestFields {
  const fields = { ...EMPTY_REQUEST_FIELDS };
  (Object.keys(EMPTY_REQUEST_FIELDS) as (keyof AssessmentRequestFields)[]).forEach((key) => {
    const value = assessment[key];
    (fields as Record<string, unknown>)[key] =
      key === "shell_company_indicator" ? value ?? null : value ?? "";
  });
  return fields;
}

function CreateAssessment({
  onCreated,
  onCancel,
  draft = null,
}: CreateAssessmentProps) {
  const [title, setTitle] = useState(draft?.title ?? "");
  const [changeType, setChangeType] = useState(draft?.change_type ?? "NEW_PRODUCT");
  const [description, setDescription] = useState(draft?.description ?? "");
  const [evidence, setEvidence] = useState(draft?.evidence ?? "");

  // R1.5: the submitter isn't entered here -- the backend records the
  // signed-in user.

  // R1.1: additional request fields.
  const [requestFields, setRequestFields] = useState<AssessmentRequestFields>(() =>
    draft ? requestFieldsOf(draft) : EMPTY_REQUEST_FIELDS
  );

  function updateField(
    field: keyof AssessmentRequestFields,
    value: string
  ) {
    setRequestFields((current) => ({ ...current, [field]: value }));
  }

  // Which accordion sections are expanded (Product Information to start).
  const [openSections, setOpenSections] = useState<Set<number>>(() => new Set([0]));

  function toggleSection(index: number) {
    setOpenSections((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  // Fields filled by document extraction that the user hasn't yet
  // accepted, edited or rejected.
  const [aiFields, setAiFields] = useState<Set<FormField>>(() => new Set());

  function clearAiMark(field: FormField) {
    setAiFields((current) => {
      if (!current.has(field)) return current;
      const next = new Set(current);
      next.delete(field);
      return next;
    });
  }

  function valueOf(field: FormField): string {
    if (field === "evidence") return evidence;
    if (field === "business_unit") return requestFields.business_unit ?? "";
    if (field === "shell_company_indicator") return String(requestFields.shell_company_indicator ?? "");
    return intakeValues[field];
  }

  function setValue(field: FormField, value: string) {
    if (field === "title") setTitle(value);
    else if (field === "change_type") setChangeType(value);
    else if (field === "description") setDescription(value);
    else if (field === "evidence") setEvidence(value);
    else updateField(field, value);
  }

  const fileInputRef = useRef<HTMLInputElement>(null);
  const [dragActive, setDragActive] = useState(false);

  // Stage 19: field-level validation state. Errors show once a field
  // has been blurred, or for every field after a failed submit.
  const [touched, setTouched] = useState<Partial<Record<IntakeField, boolean>>>({});
  const [submitAttempted, setSubmitAttempted] = useState(false);

  const intakeValues: Record<IntakeField, string> = {
    title,
    change_type: changeType,
    description,
    product_or_service_name: requestFields.product_or_service_name ?? "",
    business_owner: requestFields.business_owner ?? "",
    legal_entity: requestFields.legal_entity ?? "",
    customer_segment: requestFields.customer_segment ?? "",
    countries_jurisdictions: requestFields.countries_jurisdictions ?? "",
    delivery_channels: requestFields.delivery_channels ?? "",
    expected_transaction_volume: requestFields.expected_transaction_volume ?? "",
    expected_transaction_value: requestFields.expected_transaction_value ?? "",
    transaction_types: requestFields.transaction_types ?? "",
    third_party_vendor_usage: requestFields.third_party_vendor_usage ?? "",
    technology_process_changes: requestFields.technology_process_changes ?? "",
    expected_launch_date: requestFields.expected_launch_date ?? "",
  };

  const fieldErrors = validateIntake(intakeValues);
  const launchDateWarning = launchDateInPastWarning(intakeValues.expected_launch_date);

  function visibleError(field: IntakeField): string | undefined {
    return touched[field] || submitAttempted ? fieldErrors[field] : undefined;
  }

  function fieldA11y(field: IntakeField) {
    const describedBy = [
      visibleError(field) ? fieldErrorId(field) : "",
      field === "expected_launch_date" && launchDateWarning
        ? `${fieldId(field)}-warning`
        : "",
    ]
      .filter(Boolean)
      .join(" ");

    return {
      id: fieldId(field),
      "aria-required": true,
      "aria-invalid": visibleError(field) ? true : undefined,
      "aria-describedby": describedBy || undefined,
      onBlur: () => setTouched((current) => ({ ...current, [field]: true })),
    } as const;
  }

  const errorSummaryItems: SummaryItem[] = submitAttempted
    ? MANDATORY_IN_FORM_ORDER.flatMap((field) => {
        const message = fieldErrors[field.key];
        return message ? [{ fieldId: fieldId(field.key), message }] : [];
      })
    : [];

  const keyFieldsForChangeType = CHANGE_TYPE_KEY_FIELDS[changeType] ?? [];

  function isKeyField(field: keyof AssessmentRequestFields): boolean {
    return keyFieldsForChangeType.includes(field);
  }

  // Identity search / de-duplication: warn (don't block) when an
  // existing assessment already matches on product name + legal entity
  // or owner, so a duplicate request can be caught before it's created.
  const [duplicates, setDuplicates] = useState<DuplicateMatch[]>([]);
  const [duplicatesDismissed, setDuplicatesDismissed] = useState(false);

  useEffect(() => {
    const product = (requestFields.product_or_service_name ?? "").trim();

    if (!product) {
      setDuplicates([]);
      return;
    }

    let cancelled = false;
    // Debounced so this doesn't fire on every keystroke.
    const timer = setTimeout(() => {
      checkDuplicateAssessments({
        product_or_service_name: product,
        legal_entity: requestFields.legal_entity ?? "",
        business_owner: requestFields.business_owner ?? "",
      })
        .then((matches) => {
          if (!cancelled) {
            // A draft being edited always "matches" itself.
            setDuplicates(matches.filter((match) => match.id !== draft?.id));
            setDuplicatesDismissed(false);
          }
        })
        .catch((err) => {
          console.error("Duplicate check failed", err);
        });
    }, 500);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [
    requestFields.product_or_service_name,
    requestFields.legal_entity,
    requestFields.business_owner,
    draft?.id,
  ]);

  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);

  // Tracks whether the current form values came from an
  // extracted document, independent of which view is showing.
  const [documentAttached, setDocumentAttached] = useState(false);

  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savingAction, setSavingAction] = useState<
    "draft" | "submit" | null
  >(null);

  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");
  const [changeReason, setChangeReason] = useState("");

  async function handleExtract() {
    if (selectedFiles.length === 0) {
      setError("Please select at least one file first.");
      return;
    }

    try {
      setExtracting(true);
      setError("");
      setSuccessMessage("");

      const formData = new FormData();
      selectedFiles.forEach((file) => formData.append("files", file));

      const response = await authFetch(
        `${API_BASE_URL}/api/assessments/analyze-document`,
        {
          method: "POST",
          body: formData,
        }
      );

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(
          errorData.detail || "Unable to analyze the document."
        );
      }

      const data = await response.json();
      const extractedAssessment: ExtractedAssessment = data.assessment;

      setTitle(extractedAssessment.title);
      setChangeType(extractedAssessment.change_type);
      setDescription(extractedAssessment.business_description);
      setEvidence(extractedAssessment.evidence);

      // R1.1: populate every other intake field the document analysis
      // could extract, so the user reviews/completes them instead of
      // re-typing everything the document already stated. Fields with
      // no direct match are combined from the extracted list fields.
      const extractedFields: Record<string, string> = {
        product_or_service_name:
          extractedAssessment.product_or_service_name ?? "",
        business_owner: extractedAssessment.business_owner ?? "",
        legal_entity: extractedAssessment.legal_entity ?? "",
        customer_segment: (extractedAssessment.customer_segments ?? []).join(
          ", "
        ),
        countries_jurisdictions: (extractedAssessment.countries ?? []).join(
          ", "
        ),
        delivery_channels: (extractedAssessment.channels ?? []).join(", "),
        expected_transaction_volume:
          extractedAssessment.transaction_volume ?? "",
        expected_transaction_value:
          extractedAssessment.average_transaction_size ||
          extractedAssessment.maximum_transaction_limit ||
          "",
        transaction_types: extractedAssessment.transaction_types ?? "",
        third_party_vendor_usage: (
          extractedAssessment.third_party_vendors ?? []
        ).join(", "),
        technology_process_changes: (
          extractedAssessment.technologies ?? []
        ).join(", "),
        expected_launch_date: extractedAssessment.expected_launch_date ?? "",
      };
      setRequestFields((current) => ({ ...current, ...extractedFields }));

      // Mark every field the document actually supplied for review.
      const supplied: Record<string, string | null | undefined> = {
        ...extractedFields,
        title: extractedAssessment.title,
        change_type: extractedAssessment.change_type,
        description: extractedAssessment.business_description,
        evidence: extractedAssessment.evidence,
      };
      setAiFields(
        new Set(
          Object.entries(supplied)
            .filter(([, value]) => (value ?? "").trim() !== "")
            .map(([key]) => key as FormField)
        )
      );

      const extractedFilenames: string[] = data.filenames ?? [];

      setSuccessMessage(
        extractedFilenames.length > 1
          ? `Information extracted from ${extractedFilenames.length} documents (${extractedFilenames.join(", ")}). Please review the fields before creating the assessment.`
          : "Information extracted successfully. Review the AI-extracted fields in each section before continuing."
      );

      setDocumentAttached(true);
    } catch (err) {
      console.error(err);

      setError(
        friendlyError(err, "We couldn't read the document. Check the file type and try again, or enter the details manually.")
      );
    } finally {
      setExtracting(false);
    }
  }

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();
    await persistAssessment(false);
  }

  /*
   * Save the request as a draft. A draft only needs a title, so a
   * partially filled-in request can be saved and finished later
   * (R1.3: Save draft requests).
   */
  async function handleSaveDraft() {
    await persistAssessment(true);
  }

  async function persistAssessment(isDraft: boolean) {
    // Stage 19: field-level validation. A draft only needs a title; a
    // full submit checks every mandatory intake field (mirrors the
    // backend's MANDATORY_INTAKE_FIELDS -- the server stays the source
    // of truth and its 422 is still surfaced below).
    // Invalid fields can sit in collapsed sections: expand those first
    // (synchronously) so the field can take focus.
    function revealSections(fields: FormField[]) {
      flushSync(() =>
        setOpenSections((current) => new Set([...current, ...fields.map(sectionIndexOf)]))
      );
    }

    if (isDraft) {
      if (!title.trim()) {
        setTouched((current) => ({ ...current, title: true }));
        setError("Please enter a title before saving the draft.");
        revealSections(["title"]);
        document.getElementById(fieldId("title"))?.focus();
        return;
      }
    } else {
      const invalid = MANDATORY_IN_FORM_ORDER.filter((field) => fieldErrors[field.key]);
      if (invalid.length > 0) {
        setError("");
        setSuccessMessage("");
        // Commit the error summary in the same pass, so it doesn't push
        // the field out of view after focus has scrolled to it.
        flushSync(() => setSubmitAttempted(true));
        revealSections(invalid.map((field) => field.key));
        document.getElementById(fieldId(invalid[0].key))?.focus();
        return;
      }
      setSubmitAttempted(true);
    }

    // R1.4: the full mandatory-field check (all of R1.1's required
    // fields, not just description/evidence) is enforced server-side so
    // there's a single source of truth for what's mandatory; the
    // backend's 422 response is turned into the "missing fields" message
    // below.

    try {
      setSaving(true);
      setSavingAction(isDraft ? "draft" : "submit");
      setError("");
      setSuccessMessage("");

      let response: Response;

      if (draft) {
        // R1.3: completing a saved draft edits it in place (PATCH); with
        // is_draft=false the backend validates and submits it.
        response = await authFetch(`${API_BASE_URL}/api/assessments/${draft.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title,
            change_type: changeType,
            description,
            evidence,
            is_draft: isDraft,
            ...requestFields,
            change_reason: changeReason.trim() || null,
          }),
        });
      } else if (documentAttached && selectedFiles.length > 0) {
        const formData = new FormData();
        formData.append("title", title);
        formData.append("change_type", changeType);
        formData.append("description", description);
        formData.append("evidence", evidence);
        formData.append("is_draft", String(isDraft));
        selectedFiles.forEach((file) => formData.append("files", file));

        Object.entries(requestFields).forEach(([key, value]) => {
          formData.append(key, String(value ?? ""));
        });

        response = await authFetch(
          `${API_BASE_URL}/api/assessments/create-with-document`,
          {
            method: "POST",
            body: formData,
          }
        );
      } else {
        response = await authFetch(
          `${API_BASE_URL}/api/assessments`,
          {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify({
              title,
              change_type: changeType,
              description,
              evidence,
              is_draft: isDraft,
              ...requestFields,
            }),
          }
        );
      }

      if (!response.ok) {
        const errorData = await response.json();
        const missingFields = parseMissingFieldsDetail(errorData.detail);

        const detail = missingFields
          ? `${missingFields.message} Missing: ${missingFields.missing_fields.join(", ")}.`
          : typeof errorData.detail === "string"
          ? errorData.detail
          : Array.isArray(errorData.detail)
          ? errorData.detail
              .map((item: { msg?: string }) => item.msg)
              .filter(Boolean)
              .join(" ")
          : null;

        throw new Error(
          detail ||
            (isDraft
              ? "Failed to save draft."
              : "Failed to create assessment.")
        );
      }

      const created: Assessment | null = await response.json().catch(() => null);

      // Files picked while editing a draft are attached to it as documents.
      if (draft && selectedFiles.length > 0) {
        for (const file of selectedFiles) {
          await uploadAssessmentDocument(draft.id, file);
        }
      }

      setTitle("");
      setChangeType("NEW_PRODUCT");
      setDescription("");
      setEvidence("");
      setRequestFields(EMPTY_REQUEST_FIELDS);
      setSelectedFiles([]);
      setDocumentAttached(false);
      setDuplicates([]);
      setDuplicatesDismissed(false);
      setTouched({});
      setSubmitAttempted(false);
      setAiFields(new Set());

      onCreated(isDraft ? null : created);
    } catch (err) {
      console.error(err);

      setError(
        friendlyError(
          err,
          isDraft
            ? "Your draft couldn't be saved. Your entries are still here — please try again."
            : "The assessment couldn't be created. Your entries are still here — please try again."
        )
      );
    } finally {
      setSaving(false);
      setSavingAction(null);
    }
  }

  function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) {
      return;
    }

    setSelectedFiles(Array.from(files));
    setDocumentAttached(false); // new files selected, must re-extract
    setError("");
    setSuccessMessage("");
  }

  function removeSelectedFile(index: number) {
    setSelectedFiles((current) => current.filter((_, i) => i !== index));
    setDocumentAttached(false); // selection changed, must re-extract
  }

  function renderField(spec: FieldSpec): ReactNode {
    const { key, label } = spec;

    if (spec.kind === "toggle") {
      const flag = requestFields.shell_company_indicator;
      return (
        <div key={key} className="ca-field">
          <span className="ca-label" id={`${fieldId(key)}-label`}>
            {label}
          </span>
          <div className={`ca-toggle-row${flag ? " ca-toggle-on" : ""}`}>
            <span className="ca-toggle-text">
              Flagged as potential shell entity?
              <span className="ca-toggle-state">{flag == null ? "Not answered" : flag ? "Yes" : "No"}</span>
            </span>
            <button
              type="button"
              id={fieldId(key)}
              role="switch"
              aria-checked={flag === true}
              aria-labelledby={`${fieldId(key)}-label`}
              className="ca-switch"
              onClick={() =>
                setRequestFields((current) => ({ ...current, shell_company_indicator: !current.shell_company_indicator }))
              }
            >
              <span className="ca-switch-knob" />
            </button>
          </div>
        </div>
      );
    }
    const required = MANDATORY_KEYS.has(key);
    const intake = required ? (key as IntakeField) : null;
    const id = fieldId(key);
    const ai = aiFields.has(key);
    const error = intake ? visibleError(intake) : undefined;

    const shared = {
      ...(intake ? fieldA11y(intake) : { id }),
      value: valueOf(key),
      onChange: (
        event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>
      ) => {
        setValue(key, event.target.value);
        clearAiMark(key);
      },
    };

    let control: ReactNode;
    if (spec.kind === "select") {
      control = (
        <select {...shared}>
          {CHANGE_TYPES.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}

          {LEGACY_CHANGE_TYPES.some((option) => option.value === changeType) && (
            <optgroup label="Legacy">
              {LEGACY_CHANGE_TYPES.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      );
    } else if (spec.kind === "textarea") {
      control = <textarea {...shared} rows={spec.rows ?? 3} placeholder={spec.placeholder} />;
    } else {
      control = (
        <input
          {...shared}
          type={spec.kind === "date" ? "date" : "text"}
          placeholder={spec.placeholder}
        />
      );
    }

    const keyField = key !== "business_unit" && isKeyField(key as keyof AssessmentRequestFields);

    return (
      <div
        key={key}
        className={`ca-field${spec.wide ? " ca-wide" : ""}${keyField ? " ca-field-key" : ""}${error ? " ca-field-invalid" : ""}`}
      >
        <label htmlFor={id}>
          {label}
          {required && <RequiredMarker />}
          {keyField && <span className="ca-key-hint">Key for this change type</span>}
        </label>
        {spec.hint && <span className="ca-field-hint">{spec.hint}</span>}

        <div
          className={`ca-control${ai ? " ca-control-ai" : ""}${spec.kind === "textarea" ? " ca-control-area" : ""}`}
        >
          {control}
          {ai && (
            <div className="ca-ai">
              <span className="ca-ai-chip">
                <span aria-hidden="true">●</span> AI-Extracted
              </span>
              <button
                type="button"
                className="ca-ai-btn ca-ai-accept"
                title="Accept extracted value"
                aria-label={`Accept extracted ${label}`}
                onClick={() => clearAiMark(key)}
              >
                <NavIcon name="check-circle" size={16} />
              </button>
              <button
                type="button"
                className="ca-ai-btn"
                title="Edit value"
                aria-label={`Edit ${label}`}
                onClick={() => {
                  const element = document.getElementById(id) as HTMLInputElement | null;
                  element?.focus();
                  if (element && "select" in element && spec.kind !== "select") element.select();
                }}
              >
                <NavIcon name="pencil" size={15} />
              </button>
              <button
                type="button"
                className="ca-ai-btn ca-ai-reject"
                title="Reject extracted value"
                aria-label={`Reject extracted ${label}`}
                onClick={() => {
                  setValue(key, key === "change_type" ? "NEW_PRODUCT" : "");
                  clearAiMark(key);
                }}
              >
                <NavIcon name="x-circle" size={16} />
              </button>
            </div>
          )}
        </div>

        {intake && <FieldError id={fieldErrorId(intake)} message={error} />}
        {key === "expected_launch_date" && (
          <FieldWarning id={`${fieldId("expected_launch_date")}-warning`} message={launchDateWarning} />
        )}
      </div>
    );
  }

  const allOpen = openSections.size === SECTIONS.length;

  return (
    <div className="ca">
      {draft && (
        <p className="ca-form-hint" role="status">
          Editing saved draft <strong>{draft.reference_id ?? `#${draft.id}`}</strong>. Save it again, or
          submit it once every mandatory field is complete.
        </p>
      )}

      {/* Document intake */}

      <section
        className={`ca-drop${dragActive ? " ca-drop-active" : ""}`}
        aria-label="Upload documents for extraction"
        onDragOver={(event) => {
          event.preventDefault();
          setDragActive(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragActive(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragActive(false);
          handleFiles(event.dataTransfer.files);
        }}
      >
        <span className="ca-drop-icon">
          <NavIcon name="upload" size={26} />
        </span>
        <h3>Drag and drop business change documents, KYC dossiers, or financial logs here</h3>
        <p>Supports DOCX, DOC, XLSX, PDF, CSV and TXT, or a ZIP of these</p>

        <input
          ref={fileInputRef}
          id="assessment-file"
          className="ca-file-input"
          type="file"
          aria-label="Choose business change document(s) to upload"
          accept=".docx,.doc,.xlsx,.pdf,.csv,.txt,.zip"
          multiple
          onChange={(event) => {
            handleFiles(event.target.files);
            event.target.value = "";
          }}
        />

        {selectedFiles.length > 0 && (
          <ul className="ca-files" aria-label="Selected files">
            {selectedFiles.map((file, index) => (
              <li key={`${file.name}-${index}`}>
                <NavIcon name="file" size={14} />
                {file.name}
                <button
                  type="button"
                  onClick={() => removeSelectedFile(index)}
                  aria-label={`Remove ${file.name}`}
                  title="Remove"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}

        <div className="ca-drop-actions">
          <button type="button" className="ca-btn" onClick={() => fileInputRef.current?.click()}>
            Browse Documents
          </button>
          <button
            type="button"
            className="ca-btn ca-btn-ai"
            onClick={handleExtract}
            disabled={selectedFiles.length === 0 || extracting}
          >
            <NavIcon name="sparkle" size={15} />
            {extracting ? "Extracting…" : "Extract Information"}
          </button>
        </div>
      </section>

      {error && <div className="form-error" role="alert">{error}</div>}

      {successMessage && (
        <div className="form-success" role="status" aria-live="polite">{successMessage}</div>
      )}

      {documentAttached && !successMessage && (
        <div className="form-success">A document is attached and will be saved with this assessment.</div>
      )}

      {/* Intake form */}

      <form onSubmit={handleSubmit} noValidate aria-label="Assessment intake request" className="ca-form">
        <div className="ca-form-bar">
          <p className="ca-form-hint" id="intake-required-hint">
            Fields marked <span className="required-marker" aria-hidden="true">*</span>
            <span className="sr-only">with an asterisk</span> are required to submit. A draft only needs a title.
          </p>
          <button
            type="button"
            className="ca-link"
            onClick={() => setOpenSections(allOpen ? new Set() : new Set(SECTIONS.map((_, index) => index)))}
          >
            {allOpen ? "Collapse all" : "Expand all"}
          </button>
        </div>

        <ErrorSummary id="intake-error-summary" items={errorSummaryItems} />

        {SECTIONS.map((section, index) => {
          const open = openSections.has(index);
          const requiredKeys = section.fields
            .map((spec) => spec.key)
            .filter((key): key is IntakeField => MANDATORY_KEYS.has(key));
          const filled = requiredKeys.filter((key) => !fieldErrors[key]).length;
          const errors = requiredKeys.filter((key) => visibleError(key)).length;
          const aiCount = section.fields.filter((spec) => aiFields.has(spec.key)).length;

          return (
            <section key={section.title} className={`ca-section${open ? " ca-section-open" : ""}`}>
              <h3 className="ca-section-heading">
                <button
                  type="button"
                  className="ca-section-toggle"
                  aria-expanded={open}
                  aria-controls={`ca-section-${index}`}
                  onClick={() => toggleSection(index)}
                >
                  <span className="ca-step" aria-hidden="true">
                    {index + 1}
                  </span>
                  <span className="ca-section-title">{section.title}</span>
                  <span className="ca-section-meta">
                    {aiCount > 0 && (
                      <span className="ca-meta ca-meta-ai">{aiCount} AI-extracted to review</span>
                    )}
                    {errors > 0 ? (
                      <span className="ca-meta ca-meta-error">
                        {errors} to fix
                      </span>
                    ) : (
                      requiredKeys.length > 0 &&
                      (filled === requiredKeys.length ? (
                        <span className="ca-meta ca-meta-done">✓ Complete</span>
                      ) : (
                        <span className="ca-meta">
                          {filled}/{requiredKeys.length} required
                        </span>
                      ))
                    )}
                  </span>
                  <span className="ca-chevron">
                    <NavIcon name="chevron-down" size={18} />
                  </span>
                </button>
              </h3>

              <div id={`ca-section-${index}`} className="ca-section-body" hidden={!open}>
                <div className="ca-grid">
                  {section.fields.map(renderField)}

                  {index === 0 && duplicates.length > 0 && !duplicatesDismissed && (
                    <div className="form-warning ca-wide" role="status">
                      <strong>
                        {duplicates.length === 1
                          ? "A similar assessment already exists:"
                          : `${duplicates.length} similar assessments already exist:`}
                      </strong>
                      <ul style={{ margin: "8px 0 8px 20px", padding: 0 }}>
                        {duplicates.map((match) => (
                          <li key={match.id}>
                            {match.reference_id ? `${match.reference_id} — ` : ""}
                            {match.title} ({match.status}
                            {match.is_draft ? ", draft" : ""})
                            {match.legal_entity ? ` · ${match.legal_entity}` : ""}
                          </li>
                        ))}
                      </ul>
                      <button
                        type="button"
                        className="secondary-button"
                        onClick={() => setDuplicatesDismissed(true)}
                      >
                        This is not a duplicate — continue
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </section>
          );
        })}

        <p className="ca-form-hint">
          Supporting documents can also be added later from the assessment's details page. Not ready to
          submit? Save a draft and finish it anytime from the assessments list.
        </p>

        {draft && (
          // P4 (R3.4): every change is versioned with old and new values;
          // once the business profile is validated, a reason is required.
          <label className="ca-form-hint" style={{ display: "block" }}>
            Reason for this change (required if the business profile has already been confirmed)
            <input
              type="text"
              value={changeReason}
              onChange={(event) => setChangeReason(event.target.value)}
              style={{ display: "block", width: "100%", marginTop: 4 }}
              aria-label="Reason for this change"
            />
          </label>
        )}

        <div className="ca-footer">
          <div className="ca-footer-left">
            <button type="button" className="ca-btn" onClick={handleSaveDraft} disabled={saving}>
              {savingAction === "draft" ? "Saving Draft…" : draft ? "Save Draft Changes" : "Save Draft"}
            </button>
            <button type="button" className="ca-btn ca-btn-ghost" onClick={onCancel}>
              Cancel
            </button>
          </div>

          <button type="submit" className="ca-btn ca-btn-primary" disabled={saving}>
            {savingAction === "submit"
              ? draft
                ? "Submitting…"
                : "Creating…"
              : draft
              ? "Submit Request"
              : "Continue to Profile Confirm"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default CreateAssessment;
