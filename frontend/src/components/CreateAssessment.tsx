import { useState } from "react";

interface CreateAssessmentProps {
  onCreated: () => void;
  onCancel: () => void;
}

interface ExtractedAssessment {
  title: string;
  change_type: string;
  business_description: string;
  evidence: string;
}

function CreateAssessment({
  onCreated,
  onCancel,
}: CreateAssessmentProps) {
  const [mode, setMode] = useState<"manual" | "upload">("manual");

  const [title, setTitle] = useState("");
  const [changeType, setChangeType] = useState("NEW_PRODUCT");
  const [description, setDescription] = useState("");
  const [evidence, setEvidence] = useState("");

  const [selectedFile, setSelectedFile] = useState<File | null>(null);

  // Tracks whether the current form values came from an
  // extracted document, independent of which view is showing.
  const [documentAttached, setDocumentAttached] = useState(false);

  const [extracting, setExtracting] = useState(false);
  const [saving, setSaving] = useState(false);

  const [error, setError] = useState("");
  const [successMessage, setSuccessMessage] = useState("");

  async function handleExtract() {
    if (!selectedFile) {
      setError("Please select a file first.");
      return;
    }

    try {
      setExtracting(true);
      setError("");
      setSuccessMessage("");

      const formData = new FormData();
      formData.append("file", selectedFile);

      const response = await fetch(
        "http://127.0.0.1:8000/api/assessments/analyze-document",
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

      setSuccessMessage(
        "Information extracted successfully. Please review the fields before creating the assessment."
      );

      setDocumentAttached(true);
      setMode("manual"); // switch view to show the reviewable form
    } catch (err) {
      console.error(err);

      setError(
        err instanceof Error
          ? err.message
          : "Unable to analyze the document."
      );
    } finally {
      setExtracting(false);
    }
  }

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>
  ) {
    event.preventDefault();

    try {
      setSaving(true);
      setError("");
      setSuccessMessage("");

      let response: Response;

      if (documentAttached && selectedFile) {
        const formData = new FormData();
        formData.append("title", title);
        formData.append("change_type", changeType);
        formData.append("description", description);
        formData.append("evidence", evidence);
        formData.append("file", selectedFile);

        response = await fetch(
          "http://127.0.0.1:8000/api/assessments/create-with-document",
          {
            method: "POST",
            body: formData,
          }
        );
      } else {
        response = await fetch(
          "http://127.0.0.1:8000/api/assessments",
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
            }),
          }
        );
      }

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(
          errorData.detail || "Failed to create assessment."
        );
      }

      setTitle("");
      setChangeType("NEW_PRODUCT");
      setDescription("");
      setEvidence("");
      setSelectedFile(null);
      setDocumentAttached(false);

      onCreated();
    } catch (err) {
      console.error(err);

      setError(
        err instanceof Error
          ? err.message
          : "Unable to create assessment."
      );
    } finally {
      setSaving(false);
    }
  }

  function handleFileChange(
    event: React.ChangeEvent<HTMLInputElement>
  ) {
    const file = event.target.files?.[0];

    if (!file) {
      return;
    }

    setSelectedFile(file);
    setDocumentAttached(false); // new file selected, must re-extract
    setError("");
    setSuccessMessage("");
  }

  return (
    <div className="form-card">
      <div className="form-header">
        <div>
          <h2>Create Assessment</h2>
          <p>Submit a proposed business change for risk assessment.</p>
        </div>

        <button
          type="button"
          className="secondary-button"
          onClick={onCancel}
        >
          Cancel
        </button>
      </div>

      {/* Creation mode selector */}

      <div className="creation-mode">
        <button
          type="button"
          className={mode === "manual" ? "mode-button active" : "mode-button"}
          onClick={() => {
            setMode("manual");
            setError("");
            setSuccessMessage("");
          }}
        >
          ✍ Manual Entry
        </button>

        <button
          type="button"
          className={mode === "upload" ? "mode-button active" : "mode-button"}
          onClick={() => {
            setMode("upload");
            setError("");
            setSuccessMessage("");
          }}
        >
          📄 Upload Document
        </button>
      </div>

      {error && <div className="form-error">{error}</div>}

      {successMessage && (
        <div className="form-success">{successMessage}</div>
      )}

      {/* Upload mode */}

      {mode === "upload" && (
        <div className="upload-section">
          <div className="upload-box">
            <div className="upload-icon">📄</div>

            <h3>Upload Business Change Document</h3>

            <p>
              Upload a document and we'll extract the assessment
              information automatically.
            </p>

            <p className="upload-formats">
              Supported: DOCX, XLSX, PDF, CSV, TXT
            </p>

            <input
              id="assessment-file"
              type="file"
              accept=".docx,.xlsx,.pdf,.csv,.txt"
              onChange={handleFileChange}
            />

            {selectedFile && (
              <div className="selected-file">
                <strong>Selected file:</strong> {selectedFile.name}
              </div>
            )}

            <button
              type="button"
              className="primary-button"
              onClick={handleExtract}
              disabled={!selectedFile || extracting}
            >
              {extracting ? "Extracting..." : "Extract Information"}
            </button>
          </div>
        </div>
      )}

      {documentAttached && mode === "manual" && (
        <div className="form-success" style={{ marginBottom: 16 }}>
          A document is attached and will be saved with this assessment.
        </div>
      )}

      {/* Assessment form */}

      <form onSubmit={handleSubmit}>
        <div className="form-group">
          <label>Assessment Title</label>

          <input
            type="text"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="e.g. Launch Fleet Platform in Germany"
            required
          />
        </div>

        <div className="form-group">
          <label>Change Type</label>

          <select
            value={changeType}
            onChange={(event) => setChangeType(event.target.value)}
          >
            <option value="NEW_PRODUCT">New Product</option>
            <option value="MATERIAL_CHANGE">Material Change</option>
            <option value="NEW_GEOGRAPHY">New Geography</option>
            <option value="THIRD_PARTY">Third Party / Vendor</option>
          </select>
        </div>

        <div className="form-group">
          <label>Business Description</label>

          <textarea
            value={description}
            onChange={(event) => setDescription(event.target.value)}
            placeholder="Describe the proposed business change..."
            rows={5}
            required
          />
        </div>

        <div className="form-group">
          <label>Evidence</label>

          <textarea
            value={evidence}
            onChange={(event) => setEvidence(event.target.value)}
            placeholder="Provide supporting evidence, facts, or known impacts..."
            rows={8}
            required
          />
        </div>

        <div className="form-actions">
          <button
            type="button"
            className="secondary-button"
            onClick={onCancel}
          >
            Cancel
          </button>

          <button
            type="submit"
            className="primary-button"
            disabled={saving}
          >
            {saving ? "Creating..." : "Create Assessment"}
          </button>
        </div>
      </form>
    </div>
  );
}

export default CreateAssessment;