// AW.7: approval delegation. See backend/app/services/delegation.py
// (rules) and backend/app/api/delegations.py (endpoints).
import { authFetch } from "./http";
import { API_BASE_URL } from "./config";


export type DelegationAuthority = "MANAGER_APPROVAL" | "COMMITTEE_SIGN_OFF";
export type DelegationScope = "ALL" | "ASSESSMENT";
export type DelegationState = "SCHEDULED" | "ACTIVE" | "EXPIRED" | "REVOKED";

export const AUTHORITY_LABELS: Record<DelegationAuthority, string> = {
  MANAGER_APPROVAL: "Manager approval",
  COMMITTEE_SIGN_OFF: "Committee sign-off",
};

export interface Delegation {
  id: number;
  delegator_id: number;
  delegator_name: string | null;
  delegate_id: number;
  delegate_name: string | null;
  authority: DelegationAuthority;
  scope_type: DelegationScope;
  scope_assessment_id: number | null;
  start_at: string;
  end_at: string;
  reason: string;
  created_by_id: number;
  created_at: string;
  revoked_at: string | null;
  revoked_by_id: number | null;
  revoke_reason: string | null;
  state: DelegationState;
}

export interface DelegationUserOption {
  id: number;
  full_name: string | null;
  email: string;
  role: string;
}

export interface DelegationOptions {
  delegates: DelegationUserOption[];
  delegators: DelegationUserOption[];
  max_days: number;
}

export interface DelegationInput {
  delegate_id: number;
  authority: DelegationAuthority;
  scope_type: DelegationScope;
  scope_assessment_id: number | null;
  start_at: string;
  end_at: string;
  reason: string;
  delegator_id?: number;
}

async function send<T>(path: string, init: RequestInit, fallback: string): Promise<T> {
  const response = await authFetch(`${API_BASE_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...init.headers },
  });
  if (!response.ok) {
    let message = fallback;
    try {
      const detail = (await response.json())?.detail;
      if (typeof detail === "string") {
        message = detail;
      } else if (Array.isArray(detail)) {
        message = detail.map((item) => item?.msg ?? String(item)).join(" ");
      }
    } catch {
      // keep the fallback
    }
    throw new Error(message);
  }
  return response.json();
}

export function listDelegations(): Promise<Delegation[]> {
  return send("/api/delegations", { method: "GET" }, "Delegations couldn't be loaded.");
}

export function getDelegationOptions(): Promise<DelegationOptions> {
  return send("/api/delegations/options", { method: "GET" }, "Delegation options couldn't be loaded.");
}

export function createDelegation(input: DelegationInput): Promise<Delegation> {
  return send(
    "/api/delegations",
    { method: "POST", body: JSON.stringify(input) },
    "The delegation couldn't be created."
  );
}

export function revokeDelegation(id: number, reason: string): Promise<Delegation> {
  return send(
    `/api/delegations/${id}/revoke`,
    { method: "POST", body: JSON.stringify({ reason }) },
    "The delegation couldn't be revoked."
  );
}

// The delegations `userId` holds right now, as delegate.
export function activeDelegationsFor(delegations: Delegation[], userId: number): Delegation[] {
  return delegations.filter((d) => d.delegate_id === userId && d.state === "ACTIVE");
}

export function formatWindow(delegation: Delegation): string {
  const format = (value: string) =>
    new Date(value).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
  return `${format(delegation.start_at)} – ${format(delegation.end_at)}`;
}
