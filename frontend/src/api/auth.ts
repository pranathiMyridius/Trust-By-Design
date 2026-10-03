import { API_BASE_URL } from "./config";
import { authFetch, setAuthToken } from "./http";


export interface CurrentUser {
  id: number;
  email: string;
  full_name: string | null;
  role:
    | "BUSINESS_USER"
    | "FCRM_ANALYST"
    | "MANAGER"
    | "COMMITTEE_MEMBER"
    | "ADMIN"
    // R15.1: read-only roles, and the control/policy roles.
    | "AUDITOR"
    | "EXECUTIVE"
    | "CONTROL_OWNER"
    | "POLICY_ADMIN";
  manager_id: number | null;
  is_active: boolean;
  // R15.4: access restrictions; empty means unrestricted.
  scope_legal_entities?: string[];
  scope_business_units?: string[];
  scope_countries?: string[];
  // P3: governance designations held on top of the role (provisional).
  governance_designations?: string[];
  // Status of the designation rules (PROVISIONAL_PENDING_GOVERNANCE_APPROVAL).
  policy_status?: string;
  created_at: string;
}

// R15.1: roles that can read but never change anything.
export const READ_ONLY_ROLES: CurrentUser["role"][] = ["AUDITOR", "EXECUTIVE"];

async function readErrorDetail(response: Response): Promise<string> {
  try {
    const body = await response.json();
    return typeof body?.detail === "string" ? body.detail : "";
  } catch {
    return "";
  }
}

export async function login(
  email: string,
  password: string
): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE_URL}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Incorrect email or password.");
  }

  const data = await response.json();
  setAuthToken(data.access_token);
  return data.user;
}

export function logout(): void {
  setAuthToken(null);
}

export async function getCurrentUser(): Promise<CurrentUser | null> {
  const response = await authFetch(`${API_BASE_URL}/api/auth/me`);

  if (!response.ok) {
    return null;
  }

  return response.json();
}

export async function listUsers(): Promise<CurrentUser[]> {
  const response = await authFetch(`${API_BASE_URL}/api/users`);

  if (!response.ok) {
    throw new Error("Failed to fetch users");
  }

  return response.json();
}

export async function createUser(payload: {
  email: string;
  password: string;
  full_name?: string;
  role: string;
  manager_id?: number | null;
}): Promise<CurrentUser> {
  const response = await authFetch(`${API_BASE_URL}/api/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to create user");
  }

  return response.json();
}

export async function updateUser(
  userId: number,
  payload: Partial<{
    full_name: string;
    role: string;
    manager_id: number | null;
    is_active: boolean;
    password: string;
    scope_legal_entities: string[];
    scope_business_units: string[];
    scope_countries: string[];
  }>
): Promise<CurrentUser> {
  const response = await authFetch(`${API_BASE_URL}/api/users/${userId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });

  if (!response.ok) {
    const detail = await readErrorDetail(response);
    throw new Error(detail || "Failed to update user");
  }

  return response.json();
}
