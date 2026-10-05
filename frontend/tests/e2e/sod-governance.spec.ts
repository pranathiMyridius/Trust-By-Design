import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, USERS, api, confirmPendingOverrides, login, openAssessment, openFromMore, stage, toCommittee, toManagerReview, token, unique } from "./helpers";

// P3 (provisional policy -- pending governance approval): SoD exceptions,
// Admin/committee separation, override and challenge-review duties, and
// the server's committee readiness. Users: requestor = analyst, approver
// = head (Head of FCRM / Governance Owner), affected = dualadmin (an Admin
// whose direct manager is `manager`), reviewer = Senior/QA/Challenge.

function window(seconds: number) {
  const now = Date.now();
  return { start_at: new Date(now - 60_000).toISOString(), end_at: new Date(now + seconds * 1000).toISOString() };
}

async function dualRoleRequest(request: APIRequestContext, assessmentId: number, seconds = 7 * 86400) {
  const created = await api(request, "analyst", "POST", "/api/sod-exceptions", {
    exception_type: "ADMIN_COMMITTEE_DUAL_ROLE",
    affected_user_id: await userId(request, "dualadmin"),
    assessment_id: assessmentId,
    business_justification: "Committee short of quorum during the audit period.",
    standard_workflow_reason: "No other eligible member is available before the launch date.",
    risk_level: "LOW",
    compensating_controls: "Chair reviews the vote; another admin performs all case administration.",
    ...window(seconds),
  });
  return created.id as number;
}

async function userId(request: APIRequestContext, who: "dualadmin" | "analyst") {
  const me = await request.get(`${API}/api/auth/me`, { headers: { Authorization: `Bearer ${await token(request, who)}` } });
  return (await me.json()).id as number;
}

/** Submit, then approve by the approver of the tier the server chose (a
 * person's third exception is "repeated" and needs the Committee Chair). */
async function approve(request: APIRequestContext, eid: number, rationale: string) {
  const pending = await api(request, "analyst", "POST", `/api/sod-exceptions/${eid}/submit`);
  const approver = pending.tier === "COMMITTEE" ? "chair" : "head";
  await api(request, approver, "POST", `/api/sod-exceptions/${eid}/decision`, { decision: "APPROVE", rationale });
}

async function raw(request: APIRequestContext, who: Parameters<typeof token>[1], method: "POST" | "PATCH", path: string, data?: unknown) {
  return request.fetch(`${API}${path}`, { method, data, headers: { Authorization: `Bearer ${await token(request, who)}` } });
}

test.describe("SoD and governance enforcement", () => {
  test("a request is submitted, self-approval is refused, and an independent approver approves it", async ({ page, request }) => {
    const id = await toCommittee(request, unique("E2E SoD request"));

    // 1. Submit an SoD exception request through the UI.
    await login(page, "analyst");
    await openFromMore(page, "SoD Exceptions");
    await expect(page.getByRole("note")).toContainText("Pending Governance Approval");
    await page.getByRole("combobox", { name: "Exception type" }).selectOption("ADMIN_COMMITTEE_DUAL_ROLE");
    await page.getByRole("combobox", { name: "Affected person" }).selectOption({ label: USERS.dualadmin.full_name! });
    await page.getByRole("combobox", { name: "Assessment" }).selectOption(String(id));
    await page.getByRole("button", { name: "Save draft request" }).click();
    await expect(page.getByRole("alert")).toContainText("Enter the business justification.");
    await page.getByRole("textbox", { name: "Business justification" }).fill("Committee short of quorum this week.");
    await page.getByRole("textbox", { name: "Why the standard workflow can't be followed" }).fill("No other member before launch.");
    await page.getByRole("textbox", { name: "Compensating controls" }).fill("Chair reviews; other admin handles the case.");
    await page.getByRole("button", { name: "Save draft request" }).click();

    const table = page.getByRole("table", { name: "SoD exceptions" });
    await expect(table).toContainText("Draft");
    await page.getByRole("button", { name: "Submit for approval" }).click();
    await expect(table).toContainText("Pending approval");

    // 2. Self-approval is refused: the button is disabled and says why...
    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();
    await page.getByText("Why some actions are unavailable to you").click();
    await expect(page.getByText("You requested this exception and cannot decide it.")).toBeVisible();
    // ...and the server refuses it regardless of the UI.
    const exceptions = (await api(request, "analyst", "GET", "/api/sod-exceptions?scope=mine")) as { id: number; assessment_id: number }[];
    const eid = exceptions.find((e) => e.assessment_id === id)!.id;
    const selfApproval = await raw(request, "analyst", "POST", `/api/sod-exceptions/${eid}/decision`, { decision: "APPROVE", rationale: "mine" });
    expect(selfApproval.status()).toBe(403);
    // The affected admin's direct manager is refused too.
    const managerApproval = await raw(request, "manager", "POST", `/api/sod-exceptions/${eid}/decision`, { decision: "APPROVE", rationale: "x" });
    expect(managerApproval.status()).toBe(403);

    // 3. An independent, authorized approver approves it.
    await page.getByRole("button", { name: /^Account:/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await login(page, "head");
    await openFromMore(page, "SoD Exceptions");
    await page.getByRole("tab", { name: "Awaiting my approval" }).click();
    await page.getByRole("table", { name: "SoD exceptions" }).getByRole("button", { name: /^SOD-/ }).last().click();
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await page.getByRole("textbox", { name: "Approve: rationale (required)" }).fill("Compensating controls are adequate.");
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(page.getByRole("table", { name: "SoD exceptions" })).toContainText("Approved");
    await expect(page.getByRole("list", { name: /^History of SOD-/ })).toContainText("APPROVED by Hana Head");
  });

  test("an expired exception no longer authorizes anything", async ({ request }) => {
    test.setTimeout(90_000);
    const id = await toCommittee(request, unique("E2E SoD expiry"));
    const eid = await dualRoleRequest(request, id, 25);
    await approve(request, eid, "Short cover.");
    await api(request, "dualadmin", "POST", `/api/sod-exceptions/${eid}/declaration`, { rationale: "No interest in this case." });

    // In force: the admin may vote.
    expect((await raw(request, "dualadmin", "POST", `/api/assessments/${id}/committee-votes`, { vote: "ABSTAIN" })).status()).toBe(200);

    // 4. After the end date, checked at the point of use.
    await new Promise((resolve) => setTimeout(resolve, 27_000));
    const late = await raw(request, "dualadmin", "POST", `/api/assessments/${id}/committee-votes`, {
      vote: "APPROVE",
      recast_reason: "Changed view",
    });
    expect(late.status()).toBe(403);
    const detail = await api(request, "head", "GET", `/api/sod-exceptions/${eid}`);
    expect(detail.status).toBe("EXPIRED");
  });

  test("a dual-role admin cannot perform same-case administration", async ({ request }) => {
    const id = await toCommittee(request, unique("E2E SoD same-case"));
    const eid = await dualRoleRequest(request, id);
    await approve(request, eid, "Cover.");
    await api(request, "dualadmin", "POST", `/api/sod-exceptions/${eid}/declaration`, { rationale: "No interest." });
    expect((await raw(request, "dualadmin", "POST", `/api/assessments/${id}/committee-votes`, { vote: "APPROVE" })).status()).toBe(200);

    // 5. While acting as a committee member, case administration is refused.
    const assign = await raw(request, "dualadmin", "POST", `/api/assessments/${id}/workflow/assign`, { user_id: 1, reason: "x" });
    expect(assign.status()).toBe(403);
    expect(await assign.text()).toContain("another administrator");
    const config = await raw(request, "dualadmin", "PATCH", "/api/challenge-triggers", { residual_risk_tolerance: 50 });
    expect(config.status()).toBe(403);
    // Revoke so the account doesn't affect other journeys.
    await api(request, "admin", "POST", `/api/sod-exceptions/${eid}/revoke`, { rationale: "Journey finished." });
  });

  test("material overrides and the challenge review gate the committee, and readiness clears only when done", async ({ page, request }) => {
    const title = unique("E2E Readiness");
    const id = await toManagerReview(request, title);

    // 6. A material override (a jurisdiction the AI missed) is proposed.
    const proposed = await api(request, "analyst", "POST", `/api/assessments/${id}/overrides`, {
      section: "INTAKE_FIELD",
      field_name: "countries_jurisdictions",
      human_value: "Germany, Poland, Ukraine",
      reason: "The contract adds Ukraine.",
    });
    expect(proposed.materiality).toBe("MATERIAL");

    await login(page, "manager");
    // The manager decides on the assessment's Approval screen.
    await openAssessment(page, title);
    const readiness = page.getByRole("region", { name: "Committee readiness checklist" });
    await expect(readiness).toContainText("Blocked");
    await expect(readiness).toContainText("await independent review");
    await expect(readiness).toContainText("independent challenge review has not been completed");
    // Approving is not offered while anything blocks it.
    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();

    // The independent review and the approval (different people).
    await api(request, "reviewer", "PATCH", `/api/assessments/${id}/overrides/${proposed.id}/review`, { decision: "CONFIRM", note: "Contract confirms Ukraine." });
    await api(request, "head", "PATCH", `/api/assessments/${id}/overrides/${proposed.id}/approval`, { decision: "APPROVE", rationale: "Material and justified." });
    // The analyst's ratings differ from the AI's, so those overrides await review too.
    await confirmPendingOverrides(request, id);

    // 7. The independent challenge review, in the UI, by the reviewer.
    await page.getByRole("button", { name: /^Account:/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await login(page, "reviewer");
    await openAssessment(page, title);
    await stage(page, "Human Review").click();
    const challenge = page.getByRole("region", { name: "Challenge review (mandatory)" });
    await challenge.getByRole("textbox", { name: "Complete independent review: summary (required)" }).fill("Challenged scope, evidence and the new jurisdiction.");
    await challenge.getByRole("button", { name: "Complete independent review" }).click();
    await expect(challenge).toContainText("1. Independent review: ✓ Rae Reviewer");
    // The reviewer can't also sign off.
    await expect(challenge.getByRole("button", { name: "Sign off challenge review" })).toHaveCount(0);

    // ...and the manager signs it off.
    await page.getByRole("button", { name: /^Account:/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await login(page, "manager");
    await openAssessment(page, title);
    // The reviewer left this browser on Human Review; the manager signs off on the Approval screen.
    await stage(page, "Approval").click();
    const managerChallenge = page.getByRole("region", { name: "Challenge review (mandatory)" }).first();
    await managerChallenge.getByRole("textbox", { name: "Sign off challenge review: summary (required)" }).fill("Reviewed and agreed.");
    await managerChallenge.getByRole("button", { name: "Sign off challenge review" }).click();

    // 8. Readiness clears only now -- and the approval goes through.
    await expect(page.getByRole("region", { name: "Committee readiness checklist" })).toContainText("All readiness checks passed");
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(stage(page, "Approval")).toContainText("Awaiting committee");
  });
});
