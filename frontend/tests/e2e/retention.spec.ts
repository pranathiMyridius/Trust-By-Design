import { expect, test, type APIRequestContext, type Browser, type Page } from "@playwright/test";
import { API, api, committeeQuorumVotes, createAssessment, login, openAssessment, openFromMore, toCommittee, token, unique } from "./helpers";

// P5 (provisional policy -- pending governance approval): versioned
// retention policy with independent approval, legal holds with independent
// release, and the read-only eligibility report. Approver = head (holds the
// FCRM Governance Owner designation). The Compliance Manager and Auditor
// are created and designated through the Admin API, never the database.

const EXTRA = {
  compliance: { email: "compliance@e2e.test", password: "E2E-Compliance-Test-Only-1", full_name: "Cass Compliance", role: "MANAGER" },
  auditor: { email: "auditor@e2e.test", password: "E2E-Auditor-Test-Only-1", full_name: "Avery Auditor", role: "AUDITOR" },
} as const;
type Extra = keyof typeof EXTRA;

const extraTokens = new Map<Extra, string>();

async function extraToken(request: APIRequestContext, who: Extra): Promise<string> {
  const cached = extraTokens.get(who);
  if (cached) return cached;
  const response = await request.post(`${API}/api/auth/login`, { data: { email: EXTRA[who].email, password: EXTRA[who].password } });
  expect(response.ok(), await response.text()).toBeTruthy();
  const value = (await response.json()).access_token as string;
  extraTokens.set(who, value);
  return value;
}

async function ensureUser(request: APIRequestContext, who: Extra): Promise<number> {
  const users = (await api(request, "admin", "GET", "/api/users")) as { id: number; email: string }[];
  const existing = users.find((u) => u.email === EXTRA[who].email);
  if (existing) return existing.id;
  const created = await api(request, "admin", "POST", "/api/users", { ...EXTRA[who] });
  return created.id as number;
}

async function loginExtra(page: Page, who: Extra) {
  await page.goto("/");
  await page.getByLabel("Email").fill(EXTRA[who].email);
  await page.getByLabel("Password").fill(EXTRA[who].password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
}

async function raw(request: APIRequestContext, bearer: string, method: "GET" | "POST" | "PATCH", path: string, data?: unknown) {
  return request.fetch(`${API}${path}`, { method, data, headers: { Authorization: `Bearer ${bearer}` } });
}

async function openRetention(page: Page) {
  await openFromMore(page, "Retention & Legal Holds");
  await expect(page.getByRole("heading", { name: "Retention & Legal Holds" })).toBeVisible();
}

async function freshPage(browser: Browser): Promise<Page> {
  const context = await browser.newContext();
  return context.newPage();
}

async function reportRow(request: APIRequestContext, bearer: string, id: number) {
  const response = await raw(request, bearer, "GET", "/api/retention/eligibility?page_size=200");
  expect(response.ok()).toBeTruthy();
  const body = (await response.json()) as { items: { record_id: number; eligibility_status: string; legal_hold: boolean }[] };
  return body.items.find((row) => row.record_id === id);
}

test.describe.serial("Retention policy, legal holds and eligibility", () => {
  test.beforeAll(async ({ request }) => {
    const complianceId = await ensureUser(request, "compliance");
    await api(request, "admin", "PUT", `/api/users/${complianceId}/designations`, {
      designations: ["COMPLIANCE_MANAGER"],
      reason: "E2E: compliance manager for legal-hold release.",
    });
    await ensureUser(request, "auditor");
  });

  test("an Admin proposes, cannot approve, and an independent Governance Owner approves a new version", async ({ page, browser, request }) => {
    // 1. An Admin proposes through the UI.
    await login(page, "admin");
    await openRetention(page);
    await expect(page.getByRole("note").first()).toContainText("Pending Governance Approval");
    await expect(page.getByRole("note").first()).toContainText("not compliance-approved");
    await expect(page.getByTestId("active-retention-days")).toHaveText("2555 days");
    await page.getByRole("spinbutton", { name: "Proposed retention days" }).fill("3000");
    await page.getByRole("button", { name: "Submit for independent approval" }).click();
    await expect(page.getByRole("alert")).toContainText("at least 20 characters");
    await page.getByRole("textbox", { name: "Change justification" }).fill("E2E: retention schedule review by the governance forum.");
    await page.getByRole("button", { name: "Submit for independent approval" }).click();
    const pending = page.getByLabel("Pending proposal");
    await expect(pending).toContainText("3000 days");
    await expect(pending).toContainText("2555 days");

    // 2. The proposer can't approve it (UI explains; the server refuses).
    await expect(pending).toContainText("You can't decide this proposal: You proposed this change");
    await expect(pending.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);
    const listing = await api(request, "admin", "GET", "/api/retention/policies");
    const proposalId = listing.record_types[0].pending.id as number;
    const refused = await raw(request, await token(request, "admin"), "POST", `/api/retention/policies/${proposalId}/decision`, {
      decision: "APPROVE",
      reason: "self-approval attempt",
    });
    expect(refused.status()).toBe(403);
    await expect(page.getByTestId("active-retention-days")).toHaveText("2555 days");

    // 3. An eligible, independent Governance Owner approves it.
    const approver = await freshPage(browser);
    await login(approver, "head");
    await openRetention(approver);
    await approver.getByRole("textbox", { name: "Decision reason" }).fill("Consistent with the retention schedule review.");
    await approver.getByRole("button", { name: "Approve proposal" }).click();
    // 4. The new version is in force; the earlier version is kept unchanged.
    await expect(approver.getByTestId("active-retention-days")).toHaveText("3000 days");
    const history = approver.getByRole("table", { name: "ASSESSMENT version history" });
    await expect(history.getByRole("row").filter({ hasText: "v1" })).toContainText("Superseded");
    await expect(history.getByRole("row").filter({ hasText: "v1" })).toContainText("2555 days");
    await expect(history.getByRole("row").filter({ hasText: "v1" })).toContainText("not compliance-approved");
    await approver.context().close();

    // Back to the provisional default through the same governed route.
    const restore = await api(request, "admin", "POST", "/api/retention/policies", {
      retention_days: 2555,
      change_reason: "E2E: restore the provisional default period.",
    });
    await api(request, "head", "POST", `/api/retention/policies/${restore.id}/decision`, { decision: "APPROVE", reason: "E2E restore." });
  });

  test("a legal hold blocks eligibility and is released only by a different authorized user", async ({ page, browser, request }) => {
    const title = unique("E2E retention hold");
    // Retention runs from the final decision, so the panel appears on the
    // Approval screen once the committee has decided.
    const id = await toCommittee(request, title);
    await committeeQuorumVotes(request, id);
    await api(request, "committee", "POST", `/api/assessments/${id}/committee-decision`, {
      decision: "approve",
      rationale: "Approved by the committee for the E2E retention journey.",
    });

    // 5. An Admin places a hold through the assessment's Retention panel.
    await login(page, "admin");
    await openAssessment(page, title);
    const panel = page.getByLabel("Retention and legal hold");
    await panel.getByRole("textbox", { name: "Reason for legal hold" }).fill("E2E: litigation preservation notice.");
    await panel.getByRole("textbox", { name: "Matter reference" }).fill("LIT-E2E-1");
    await panel.getByRole("button", { name: "Place legal hold" }).click();
    await expect(panel).toContainText("Legal hold: ACTIVE");
    await expect(panel.getByTestId("retention-eligibility")).toContainText("legal hold");
    // 6. The person who set it can't release it.
    await expect(panel).toContainText("Release unavailable: You placed this legal hold");
    await expect(panel.getByRole("button", { name: "Release legal hold" })).toHaveCount(0);
    const refused = await raw(request, await token(request, "admin"), "PATCH", `/api/assessments/${id}/retention/legal-hold`, {
      hold: false,
      reason: "self release attempt",
    });
    expect(refused.status()).toBe(403);

    // 8 (before). The report shows the hold overriding eligibility.
    await openRetention(page);
    await page.getByRole("combobox", { name: "Eligibility status filter" }).selectOption("held");
    await expect(page.getByTestId(`eligibility-row-${id}`)).toContainText("On legal hold");

    // 7. An independent Compliance Manager releases it, with a reason.
    const releaser = await freshPage(browser);
    await loginExtra(releaser, "compliance");
    await openAssessment(releaser, title);
    const releasePanel = releaser.getByLabel("Retention and legal hold");
    await releasePanel.getByRole("textbox", { name: "Reason for releasing the legal hold" }).fill("E2E: preservation notice withdrawn.");
    await releasePanel.getByRole("button", { name: "Release legal hold" }).click();
    await expect(releasePanel).toContainText("Legal hold: None");
    const history = releasePanel.getByRole("list", { name: "Legal hold history" });
    await expect(history).toContainText("Placed by Default Admin: E2E: litigation preservation notice. (matter LIT-E2E-1)");
    await expect(history).toContainText("Released by Cass Compliance: E2E: preservation notice withdrawn.");
    await releaser.context().close();

    // 8 (after). The report reflects the new status.
    const row = await reportRow(request, await token(request, "admin"), id);
    expect(row?.legal_hold).toBe(false);
    // A decided assessment is back to its normal retention once the hold is released.
    expect(row?.eligibility_status).toBe("RETAINED");
  });

  test("an Auditor reads the report within scope but changes nothing; others are refused", async ({ page, request }) => {
    const auditorId = await ensureUser(request, "auditor");
    await api(request, "admin", "PATCH", `/api/users/${auditorId}`, { scope_legal_entities: ["Bank DE GmbH"] });
    const inside = await createAssessment(request, unique("E2E retention in scope"));
    const outside = await createAssessment(request, unique("E2E retention out of scope"), { legal_entity: "Bank FR SA" });
    extraTokens.delete("auditor");
    const auditor = await extraToken(request, "auditor");

    // 9. Read-only report access.
    await loginExtra(page, "auditor");
    await openRetention(page);
    await expect(page.getByRole("region", { name: "Retention eligibility report" })).toBeVisible();
    await expect(page.getByText("Proposing a change needs: Admin.")).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit for independent approval" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "Approve proposal" })).toHaveCount(0);
    for (const [method, path, data] of [
      ["POST", "/api/retention/policies", { retention_days: 3000, change_reason: "Auditor attempt to change policy." }],
      ["PATCH", `/api/assessments/${inside}/retention/legal-hold`, { hold: true, reason: "Auditor attempt" }],
      ["POST", `/api/assessments/${inside}/soft-delete`, { reason: "Auditor attempt" }],
    ] as const) {
      expect((await raw(request, auditor, method, path, data)).status()).toBe(403);
    }

    // 10. Entity scope applies to the report; other roles are refused.
    expect(await reportRow(request, auditor, inside)).toBeTruthy();
    expect(await reportRow(request, auditor, outside)).toBeUndefined();
    expect((await raw(request, await token(request, "analyst"), "GET", "/api/retention/eligibility")).status()).toBe(403);
    expect((await raw(request, await token(request, "manager"), "GET", "/api/retention/eligibility")).status()).toBe(403);

    const analyst = page;
    await analyst.context().clearCookies();
    await analyst.evaluate(() => window.localStorage.clear());
    await login(analyst, "analyst");
    const nav = analyst.getByRole("navigation", { name: "Main navigation" });
    await nav.getByRole("button", { name: "More" }).click();
    await expect(nav.getByRole("button", { name: "Retention & Legal Holds" })).toHaveCount(0);
  });
});
