import { expect, type APIRequestContext, type Page } from "@playwright/test";
import users from "../../../backend/tests/support/e2e_users.json" with { type: "json" };

// Test-only accounts seeded by backend/tests/e2e_server.py.
export type Who = "owner" | "analyst" | "manager" | "admin" | "committee" | "reviewer" | "head" | "chair" | "dualadmin" | "fcrmrep" | "businessrep";
export const USERS = users as Record<Who, { email: string; password: string; full_name?: string }>;

export const API = "http://127.0.0.1:8000";

/** A unique suffix so repeated runs and duplicate detection don't collide. */
export function unique(label: string): string {
  return `${label} ${Date.now().toString(36)}`;
}

export function intakeRequest(title: string, overrides: Record<string, string> = {}) {
  return {
    title,
    change_type: "NEW_GEOGRAPHY",
    product_or_service_name: "Merchant Acquiring DE",
    description:
      "Card acquiring for German merchants with cross-border settlement to Poland, delivered through an external acquiring processor.",
    evidence: "Merchants receive cross-border settlement; onboarding is remote and data is shared with the processor.",
    business_owner: "Merchant Services",
    legal_entity: "Bank DE GmbH",
    customer_segment: "SME merchants",
    countries_jurisdictions: "Germany, Poland",
    delivery_channels: "Web portal; API",
    expected_transaction_volume: "50k/month",
    expected_transaction_value: "EUR 20m/month",
    transaction_types: "Card acquiring",
    third_party_vendor_usage: "External acquiring processor",
    technology_process_changes: "New acquiring platform",
    expected_launch_date: "2027-03-01",
    ...overrides,
  };
}

/**
 * Risk Identification ends on an "AI prediction" dialog that stays open until
 * the user clicks Continue. It covers the page, so dismiss it when it
 * appears -- but only once it offers Continue (a running or refused move
 * has a different button and closes itself).
 */
export async function dismissStageDialog(page: Page): Promise<void> {
  await page.addLocatorHandler(
    page.locator(".rim-backdrop").filter({ has: page.getByRole("button", { name: "Continue", exact: true }) }),
    async (dialog) => {
      await dialog.getByRole("button", { name: "Continue", exact: true }).click();
    }
  );
}

export async function login(page: Page, who: Who): Promise<void> {
  await dismissStageDialog(page);
  await page.goto("/");
  await page.getByLabel("Email").fill(USERS[who].email);
  await page.getByLabel("Password").fill(USERS[who].password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
}

// -- API shortcuts for test setup (the journeys under test use the UI) --

const tokens = new Map<Who, string>();

export async function token(request: APIRequestContext, who: Who): Promise<string> {
  const cached = tokens.get(who);
  if (cached) return cached;
  const response = await request.post(`${API}/api/auth/login`, {
    data: { email: USERS[who].email, password: USERS[who].password },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const value = (await response.json()).access_token as string;
  tokens.set(who, value);
  return value;
}

export async function api(
  request: APIRequestContext,
  who: Who,
  method: "GET" | "POST" | "PATCH" | "PUT",
  path: string,
  data?: unknown,
) {
  const response = await request.fetch(`${API}${path}`, {
    method,
    data,
    headers: { Authorization: `Bearer ${await token(request, who)}` },
  });
  expect(response.ok(), `${method} ${path}: ${response.status()} ${await response.text()}`).toBeTruthy();
  return response.json();
}

/** Owner creates a submitted assessment; returns its id. */
export async function createAssessment(request: APIRequestContext, title: string, overrides = {}) {
  const created = await api(request, "owner", "POST", "/api/assessments", {
    ...intakeRequest(title, overrides),
    is_draft: false,
  });
  return created.id as number;
}

/**
 * Owner completes intake and confirms the structured profile, leaving
 * the assessment in EVIDENCE_COLLECTION ready for risk identification.
 */
export async function readyForRiskIdentification(request: APIRequestContext, id: number) {
  await api(request, "owner", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  // The first attempt creates the structured profile and then refuses
  // until it is confirmed -- that refusal is expected here.
  await request.fetch(`${API}/api/assessments/${id}/advance-stage`, {
    method: "PATCH",
    data: {},
    headers: { Authorization: `Bearer ${await token(request, "owner")}` },
  });
  await api(request, "owner", "POST", `/api/assessments/${id}/intelligence/confirm`, { confirmed_by: "Owner" });
}

/**
 * Drive an assessment through the real pipeline (API) to Human Review:
 * profile confirmed, every applicable factor rated, controls outcome
 * recorded, residual frozen, FCRM review saved. Returns its id.
 */
export async function toHumanReview(request: APIRequestContext, title: string): Promise<number> {
  const id = await createAssessment(request, title);
  await driveToHumanReview(request, id);
  return id;
}

/** toHumanReview for an assessment that already exists (still at INTAKE). */
export async function driveToHumanReview(request: APIRequestContext, id: number): Promise<void> {
  await readyForRiskIdentification(request, id);
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  const factors = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as {
    id: number;
    applicable: boolean;
    excluded: boolean;
  }[];
  for (const factor of factors.filter((f) => f.applicable && !f.excluded)) {
    await api(request, "analyst", "PATCH", `/api/assessments/${id}/risk-factors/${factor.id}/rating`, {
      likelihood: 3,
      impact: 3,
      reason: "E2E analyst judgement.",
    });
  }
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {}); // inherent
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {}); // controls
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/challenge`, {
    outcome: "ACCEPTED",
    comment: "Controls reviewed.",
  });
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {}); // residual
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {}); // human review
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/fcrm-review`, {
    justification: "Reviewed.",
    human_ratings: {},
  });
}

/**
 * Human Review -> Submitted to Manager, with every open challenge finding
 * resolved by the analyst (G-4: high/critical findings can't be accepted;
 * medium ones only by the Committee) -- but the mandatory challenge review
 * NOT yet signed off.
 */
export async function toManagerReview(request: APIRequestContext, title: string): Promise<number> {
  const id = await toHumanReview(request, title);
  await api(request, "owner", "POST", `/api/assessments/${id}/submit-to-manager`, {});
  const review = (await api(request, "manager", "GET", `/api/assessments/${id}/challenge-review`)) as {
    findings: { id: number; resolution_status: string }[];
  };
  for (const finding of review.findings.filter((f) => f.resolution_status === "OPEN")) {
    await api(request, "analyst", "PATCH", `/api/assessments/${id}/challenge-findings/${finding.id}/resolve`, {
      resolution_note: "Addressed for the E2E journey.",
    });
  }
  return id;
}

/** P3: the independent challenge review (reviewer), then the sign-off (manager). */
export async function completeChallenge(request: APIRequestContext, id: number): Promise<void> {
  await api(request, "reviewer", "POST", `/api/assessments/${id}/challenge-review/review`, {
    reason: "Independent challenge review completed for the E2E journey.",
  });
  await api(request, "manager", "POST", `/api/assessments/${id}/challenge-review/signoff`, {
    reason: "Challenge review signed off for the E2E journey.",
  });
}

/**
 * The manager cannot send an assessment to the committee while material
 * overrides await review. The analyst's ratings differ from the AI's, so a
 * few are always pending: the reviewer confirms them and the Head of FCRM
 * approves the ones that need it.
 */
export async function confirmPendingOverrides(request: APIRequestContext, id: number): Promise<void> {
  type Override = { id: number; state: string | null };
  const ledger = async () => (await api(request, "analyst", "GET", `/api/assessments/${id}/overrides`)) as Override[];
  for (const entry of (await ledger()).filter((o) => o.state === "PENDING_REVIEW")) {
    await api(request, "reviewer", "PATCH", `/api/assessments/${id}/overrides/${entry.id}/review`, {
      decision: "CONFIRM",
      note: "Confirmed for the E2E journey.",
    });
  }
  for (const entry of (await ledger()).filter((o) => o.state === "PENDING_APPROVAL")) {
    await api(request, "head", "PATCH", `/api/assessments/${id}/overrides/${entry.id}/approval`, {
      decision: "APPROVE",
      rationale: "Approved for the E2E journey.",
    });
  }
}

/** ... -> Ready for Committee: overrides reviewed, challenge review completed
 * and signed off, approved by the manager. */
export async function toCommittee(request: APIRequestContext, title: string): Promise<number> {
  const id = await toManagerReview(request, title);
  await confirmPendingOverrides(request, id);
  await completeChallenge(request, id);
  await api(request, "manager", "POST", `/api/assessments/${id}/manager-decision`, {
    decision: "approve",
    comment: "Approved for committee.",
  });
  return id;
}

/** G-5 quorum: three eligible members vote, including both representatives. */
export async function committeeQuorumVotes(request: APIRequestContext, id: number, vote = "APPROVE"): Promise<void> {
  for (const member of ["committee", "fcrmrep", "businessrep"] as const) {
    await api(request, member, "POST", `/api/assessments/${id}/committee-votes`, { vote, comment: "E2E committee vote." });
  }
}

/** Open an assessment from the Assessments page by its title. */
export async function openAssessment(page: Page, title: string): Promise<void> {
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Assessments", exact: true }).click();
  await page.getByRole("button", { name: new RegExp(`Open assessment ${escapeRegExp(title)}`) }).first().click();
}

/** Rate every unrated applicable factor through the UI's rating form. */
export async function rateAllFactors(page: Page, likelihood: number, impact: number): Promise<number> {
  const rateButtons = page.getByRole("button", { name: "Rate Factor", exact: true });
  const reRateButtons = page.getByRole("button", { name: "Re-rate", exact: true });
  // Wait for the factor list to render before counting anything.
  await expect(rateButtons.first()).toBeVisible();
  const total = await rateButtons.count();
  const alreadyRated = await reRateButtons.count();
  for (let done = 1; done <= total; done += 1) {
    await rateButtons.first().click();
    await page.getByRole("combobox", { name: "Likelihood", exact: true }).selectOption(String(likelihood));
    await page.getByRole("combobox", { name: "Impact", exact: true }).selectOption(String(impact));
    // R10.3: a rating that differs from the AI's suggestion needs a reason.
    const reason = page.getByRole("textbox", { name: /Reason for differing from the AI suggestion/ });
    if (await reason.isVisible()) {
      await reason.fill("E2E: analyst judgement differs from the AI suggestion.");
    }
    await page.getByRole("button", { name: "Save Rating" }).click();
    // Opening a form hides that factor's "Rate Factor" button, so count
    // completed saves instead: each shows up as a "Re-rate" button once
    // the refreshed list is back.
    await expect(reRateButtons).toHaveCount(alreadyRated + done);
  }
  return total;
}

/** Open a page that lives under the sidebar's "More" menu (the menu closes after the click). */
export async function openFromMore(page: Page, name: string): Promise<void> {
  const nav = page.getByRole("navigation", { name: "Main navigation" });
  await nav.getByRole("button", { name: "More" }).click();
  await nav.getByRole("button", { name, exact: true }).click();
}

export function stage(page: Page, name: string) {
  return page.getByRole("list", { name: "Assessment pipeline stages" }).getByRole("listitem", { name, exact: true });
}

export function workflowStatus(page: Page) {
  return page.getByRole("region", { name: "Workflow status" });
}

export function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
