import { expect, test, type APIRequestContext, type Page } from "@playwright/test";
import { API, api, committeeQuorumVotes, confirmPendingOverrides, login, readyForRiskIdentification, toCommittee, token, unique } from "./helpers";

// P6 (Stage 18): an approved assessment is reassessed through the UI; while
// the reassessment runs, the parent stays in force and says so; when the
// reassessment is approved the parent is superseded. The reassessment shows
// the information it carried over, with source and age.

/** A reassessment copies its parent's title, so open a record by its reference. */
async function openByReference(page: Page, request: APIRequestContext, id: number) {
  const record = await api(request, "admin", "GET", `/api/assessments/${id}`);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Assessments", exact: true }).click();
  await page.getByRole("button", { name: `Open assessment ${record.title}` }).filter({ hasText: record.reference_id }).click();
}

async function committeeApprove(request: APIRequestContext, id: number) {
  await committeeQuorumVotes(request, id);
  await api(request, "committee", "POST", `/api/assessments/${id}/committee-decision`, {
    decision: "approve",
    rationale: "Approved by the committee for the E2E reassessment journey.",
  });
}

/** The same pipeline as helpers.toCommittee, for an assessment that already exists (the reassessment). */
async function existingToCommittee(request: APIRequestContext, id: number) {
  await readyForRiskIdentification(request, id);
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  const factors = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as { id: number; applicable: boolean; excluded: boolean }[];
  for (const factor of factors.filter((f) => f.applicable && !f.excluded)) {
    await api(request, "analyst", "PATCH", `/api/assessments/${id}/risk-factors/${factor.id}/rating`, { likelihood: 3, impact: 3, reason: "E2E analyst judgement." });
  }
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/challenge`, { outcome: "ACCEPTED", comment: "Controls reviewed." });
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});
  await api(request, "analyst", "PATCH", `/api/assessments/${id}/fcrm-review`, { justification: "Reviewed.", human_ratings: {} });
  await api(request, "owner", "POST", `/api/assessments/${id}/submit-to-manager`, {});
  const review = (await api(request, "manager", "GET", `/api/assessments/${id}/challenge-review`)) as { findings: { id: number; resolution_status: string }[] };
  for (const finding of review.findings.filter((f) => f.resolution_status === "OPEN")) {
    await api(request, "analyst", "PATCH", `/api/assessments/${id}/challenge-findings/${finding.id}/resolve`, { resolution_note: "Addressed for the E2E journey." });
  }
  await confirmPendingOverrides(request, id);
  await api(request, "reviewer", "POST", `/api/assessments/${id}/challenge-review/review`, { reason: "Independent challenge review completed for the E2E journey." });
  await api(request, "manager", "POST", `/api/assessments/${id}/challenge-review/signoff`, { reason: "Challenge review signed off for the E2E journey." });
  await api(request, "manager", "POST", `/api/assessments/${id}/manager-decision`, { decision: "approve", comment: "Approved for committee." });
}

test.describe.serial("Reassessment lifecycle", () => {
  let parentTitle = "";
  let parentId = 0;
  let childId = 0;

  test("the owner proposes a change on an approved assessment; the parent stays in force under reassessment", async ({ page, request }) => {
    parentTitle = unique("E2E reassessment parent");
    parentId = await toCommittee(request, parentTitle);
    await committeeApprove(request, parentId);

    // A committee member doesn't get the "propose a change" action (server rule).
    const committeeView = await request.get(`${API}/api/assessments/${parentId}/reassessment/status`, {
      headers: { Authorization: `Bearer ${await token(request, "committee")}` },
    });
    expect((await committeeView.json()).actions.propose.allowed).toBe(false);

    await login(page, "owner");
    await openByReference(page, request, parentId);
    const panel = page.getByLabel("Reassessment", { exact: true });
    await expect(panel.getByTestId("reassessment-state")).toContainText("Approval in force");
    await panel.getByRole("button", { name: "Propose a change" }).click();
    await panel.getByRole("textbox", { name: "New countries / jurisdictions" }).fill("Germany, Poland, France");
    await panel.getByRole("textbox", { name: "Reason for the proposed change (required)" }).fill("Expanding acquiring to France.");
    await panel.getByRole("button", { name: "Propose change and open reassessment" }).click();
    await expect(panel).toContainText("Reassessment opened as Assessment #");
    await expect(panel.getByTestId("reassessment-state")).toContainText("Under reassessment");
    await expect(panel.getByTestId("reassessment-state")).toContainText("stays in force");
    const status = await api(request, "owner", "GET", `/api/assessments/${parentId}/reassessment/status`);
    childId = status.in_progress_reassessment_id;
    expect(childId).toBeGreaterThan(0);
    // The new-geography trigger was recorded against the parent and linked to the reassessment.
    await expect(panel.getByRole("table", { name: "Reassessment triggers" })).toContainText("New geography");
  });

  test("the reassessment shows carried-over information and compares with its parent", async ({ page, request }) => {
    await login(page, "analyst");
    await openByReference(page, request, childId);
    const panel = page.getByLabel("Reassessment", { exact: true });
    await expect(panel).toContainText(`This is a reassessment of Assessment #${parentId}`);
    const reused = panel.getByRole("table", { name: "Reused fields" });
    await expect(reused).toContainText("Customer segment");
    await expect(reused).toContainText(`Assessment #${parentId}`);
    await expect(reused).not.toContainText("Countries / jurisdictions"); // proposed, so not reused
    await panel.getByRole("button", { name: "Compare with parent" }).click();
    await expect(panel.getByLabel("Comparison with the parent assessment")).toContainText("countries jurisdictions");
  });

  test("approving the reassessment supersedes the parent", async ({ page, request }) => {
    await existingToCommittee(request, childId);
    await committeeApprove(request, childId);

    await login(page, "owner");
    await openByReference(page, request, parentId);
    const panel = page.getByLabel("Reassessment", { exact: true });
    await expect(panel.getByTestId("reassessment-state")).toContainText(`Superseded by reassessment #${childId}`);
    await expect(panel.getByRole("button", { name: "Propose a change" })).toHaveCount(0);
    await expect(panel).toContainText(`propose changes on that assessment instead`);
    const parent = await api(request, "owner", "GET", `/api/assessments/${parentId}`);
    expect(parent.status).toBe("APPROVED"); // the record of what was decided is unchanged
    expect(parent.reassessment_state).toBe("SUPERSEDED");
  });
});
