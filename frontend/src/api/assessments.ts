export interface Assessment {
  id: number;
  title: string;
  change_type: string;
  description: string;
  evidence: string;
  status: string;
  overall_score: number | null;
  risk_level: string | null;
  created_at: string;
  updated_at: string;
}

const API_BASE_URL = "http://127.0.0.1:8000";

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const text = await response.text();

    if (!text) {
      return "";
    }

    try {
      const parsed = JSON.parse(text);
      return parsed?.detail
        ? typeof parsed.detail === "string"
          ? parsed.detail
          : JSON.stringify(parsed.detail)
        : text;
    } catch {
      return text;
    }
  } catch {
    return "";
  }
}

export async function getAssessments(): Promise<Assessment[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch assessments");
  }

  return response.json();
}
export async function analyzeAssessment(
  assessmentId: number
): Promise<Assessment> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/analyze`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    }
  );

  if (!response.ok) {
    throw new Error("Failed to analyze assessment");
  }

  return response.json();
}
export interface RiskResult {
  id: number;
  assessment_id: number;
  dimension: string;
  score: number;
  severity: string;
  reason: string;
}

export async function getRiskResults(
  assessmentId: number
): Promise<RiskResult[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/risk-results`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch risk results");
  }

  return response.json();
}
export async function updateAssessmentStatus(
  assessmentId: number,
  status: string
): Promise<Assessment> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/status?status=${encodeURIComponent(
      status
    )}`,
    {
      method: "PATCH",
      headers: {
        Accept: "application/json",
      },
    }
  );

  if (!response.ok) {
    // Surface the backend's actual reason (e.g. FastAPI 422 listing the
    // allowed enum values) instead of a generic message, so invalid status
    // strings are easy to diagnose from the UI.
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to update assessment status (${response.status}): ${detail}`
        : `Failed to update assessment status (${response.status})`
    );
  }

  return response.json();
}
export interface AssessmentDocument {
  id: number;
  assessment_id: number;
  filename: string;
  file_type: string;
  extracted_text: string;
  created_at: string;
}
export async function getAssessmentDocuments(
  assessmentId: number
): Promise<AssessmentDocument[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/documents`
  );

  if (!response.ok) {
    throw new Error(
      "Failed to fetch assessment documents"
    );
  }

  return response.json();
}
export interface AssessmentIntelligence {
  id: number;
  assessment_id: number;
  business_line: string | null;

  channels: string[];
  countries: string[];
  customer_segments: string[];

  transaction_volume: string | null;
  average_transaction_size: string | null;
  maximum_transaction_limit: string | null;

  third_party_vendors: string[];
  data_shared: string[];
  technologies: string[];
  regulatory_considerations: string[];
  existing_controls: string[];
  additional_risk_factors: string[];

  created_at: string;
}

export async function getAssessmentIntelligence(
  assessmentId: number
): Promise<AssessmentIntelligence | null> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/intelligence`
  );

  // Intelligence is optional for older/manual assessments.
  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Failed to fetch assessment intelligence");
  }

  return response.json();
}
export interface AuditEvent {
  id: number;
  assessment_id: number;
  action: string;
  previous_status: string | null;
  new_status: string | null;
  details: string | null;
  actor: string | null;
  created_at: string;
}
export async function getAssessmentAudit(
  assessmentId: number
): Promise<AuditEvent[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/audit`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch assessment audit");
  }

  return response.json();
}
export async function getAllAuditEvents(): Promise<AuditEvent[]> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/audit/all`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch audit history");
  }

  return response.json();
}
export async function updateAssessment(
  assessmentId: number,
  data: {
    title: string;
    change_type: string;
    description: string;
    evidence: string;
  }
): Promise<Assessment> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
    }
  );

  if (!response.ok) {
    throw new Error("Failed to update assessment");
  }

  return response.json();
}

export async function uploadAssessmentDocument(
  assessmentId: number,
  file: File
): Promise<AssessmentDocument> {
  const formData = new FormData();
  formData.append("file", file);

  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/documents`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
      body: formData,
    }
  );

  if (!response.ok) {
    throw new Error("Failed to upload document");
  }

  return response.json();
}
export interface FcrmReview {
  assessment_id: number;
  justification: string;
  human_ratings: Record<number, string>;
  reviewed_by: string | null;
  updated_at: string | null;
}

export async function getFcrmReview(
  assessmentId: number
): Promise<FcrmReview> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/fcrm-review`
  );

  if (!response.ok) {
    throw new Error("Failed to fetch FCRM review");
  }

  return response.json();
}

export async function saveFcrmReview(
  assessmentId: number,
  data: {
    justification: string;
    human_ratings: Record<number, string>;
  }
): Promise<FcrmReview> {
  const response = await fetch(
    `${API_BASE_URL}/api/assessments/${assessmentId}/fcrm-review`,
    {
      method: "PATCH",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(data),
    }
  );

  if (!response.ok) {
    const detail = await readErrorDetail(response);

    throw new Error(
      detail
        ? `Failed to save FCRM review (${response.status}): ${detail}`
        : `Failed to save FCRM review (${response.status})`
    );
  }

  return response.json();
}
