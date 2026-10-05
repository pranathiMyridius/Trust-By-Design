import { expect, test } from "@playwright/test";
import {
  api,
  login,
  openAssessment,
  stage,
  toCommittee,
  toHumanReview,
  toManagerReview,
  unique,
} from "./helpers";

// P2 governance records (docs/REMAINING_REQUIREMENTS.md): the mandatory
// challenge-review sign-off, append-only committee votes, independently
// reviewed overrides, and versioned control changes.

const approvals = (page: import("@playwright/test").Page) =>
  page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Approvals", exact: true });

test.describe("Governance records", () => {
  test("the manager cannot approve until the challenge review is reviewed and signed off", async ({ page, request }) => {
    const title = unique("E2E Sign-off");
    const id = await toManagerReview(request, title);
    // P3: the independent review is done by the reviewer (API); the
    // manager signs off in the UI.
    await api(request, "reviewer", "POST", `/api/assessments/${id}/challenge-review/review`, {
      reason: "Independent review: findings accepted with reasons.",
    });

    await login(page, "manager");
    // The manager decides on the assessment's Approval screen, not from the queue.
    await openAssessment(page, title);
    const signoff = page.getByRole("region", { name: "Challenge review (mandatory)" }).first();
    await expect(signoff.getByRole("alert")).toContainText("has not been signed off");

    // Not offered while unsigned: the checklist says why and Approve is disabled.
    await expect(page.getByRole("region", { name: "Committee readiness checklist" })).toContainText("Blocked");
    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeDisabled();

    // A summary is required, then the sign-off is recorded.
    await signoff.getByRole("button", { name: "Sign off challenge review" }).click();
    await expect(signoff.getByText("A summary is required.")).toBeVisible();
    await signoff
      .getByRole("textbox", { name: "Sign off challenge review: summary (required)" })
      .fill("Findings accepted with reasons; profile and evidence reviewed.");
    await signoff.getByRole("button", { name: "Sign off challenge review" }).click();
    await expect(signoff.getByText("Complete.")).toBeVisible();

    await expect(page.getByRole("button", { name: "Approve", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "Approve", exact: true }).click();
    await expect(stage(page, "Approval")).toContainText("Awaiting committee");
  });

  test("a changed committee vote keeps the earlier vote on record", async ({ page, request }) => {
    const title = unique("E2E Votes");
    const id = await toCommittee(request, title);

    await login(page, "committee");
    await approvals(page).click();
    // Other journeys' assessments may be before the committee too.
    const row = page.locator(".assessment-row").filter({ hasText: title });
    await row.getByRole("textbox", { name: "Optional comment for your vote" }).fill("Within appetite.");
    await row.getByRole("button", { name: "Approve", exact: true }).first().click();
    // The full vote history sits in a collapsed section under the member cards.
    await row.getByText(/^Vote history/).click();
    const votes = row.getByRole("list", { name: "Committee votes, including superseded ones" });
    await expect(votes).toContainText("APPROVE (v1, current)");

    // A re-cast needs a reason.
    await row.getByRole("button", { name: "Dissent", exact: true }).click();
    await expect(row.getByRole("alert").filter({ hasText: "Give a reason for changing the vote" })).toBeVisible();
    await row
      .getByRole("textbox", { name: "Reason for changing the vote (required)" })
      .fill("Vendor controls evidence is out of date.");
    await row.getByRole("button", { name: "Dissent", exact: true }).click();
    await expect(votes).toContainText("APPROVE (v1, superseded)");
    await expect(votes).toContainText("DISSENT (v2, current)");
    await expect(votes).toContainText("changed because: Vendor controls evidence is out of date.");

    // The record agrees: both votes kept.
    const history = (await api(request, "committee", "GET", `/api/assessments/${id}/committee-votes?include_history=true`)) as {
      vote: string;
      is_current: boolean;
    }[];
    expect(history.map((v) => [v.vote, v.is_current])).toEqual([
      ["APPROVE", false],
      ["DISSENT", true],
    ]);
  });

  test("an analyst's override is proposed, then confirmed by an independent reviewer", async ({ page, request }) => {
    const title = unique("E2E Override");
    // At the manager's challenge stage the reviewing manager is assigned
    // (and can see the assessment); the analyst can still propose.
    const id = await toManagerReview(request, title);
    // RISK_RATIONALE is non-material (P3), so an independent review alone
    // makes it count; material overrides are covered in sod-governance.spec.ts.
    const factors = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as {
      id: number;
      category: string;
      rationale: string | null;
      applicable: boolean;
    }[];
    const geo = factors.find((f) => f.category === "GEOGRAPHIC_RISK" && f.applicable)!;

    await login(page, "analyst");
    await openAssessment(page, title);
    await stage(page, "Human Review").click();
    await page.getByRole("combobox", { name: "Override section" }).selectOption("RISK_RATIONALE");
    await page.getByRole("combobox", { name: "Risk factor" }).selectOption(String(geo.id));
    await page.getByRole("textbox", { name: "Proposed human value" }).fill("Settlement to Poland creates a sanctions nexus.");
    await page.getByRole("textbox", { name: "Reason for this change (required)" }).fill("AI rationale omits the sanctions angle.");
    await page.getByRole("button", { name: "Propose Override" }).click();

    const ledger = page.getByRole("table", { name: "Override ledger" });
    await expect(ledger).toContainText("Proposed (non-material)");
    // The system value shown is the factor's own rationale, read by the server.
    await expect(ledger).toContainText(geo.rationale ?? "");
    // The proposer can't review their own override.
    await expect(ledger.getByRole("button", { name: "Confirm" })).toHaveCount(0);

    await page.getByRole("button", { name: /^Account:/ }).click();
    await page.getByRole("menuitem", { name: "Sign out" }).click();
    await login(page, "manager");
    await openAssessment(page, title);
    await stage(page, "Human Review").click();
    const proposedRow = ledger.getByRole("row").filter({ hasText: "Settlement to Poland creates a sanctions nexus." });
    await proposedRow.getByRole("textbox", { name: /Review note for override/ }).fill("Agreed: the sanctions nexus is material.");
    await proposedRow.getByRole("button", { name: "Confirm" }).click();
    await expect(proposedRow).toContainText("Done (non-material)");
    await expect(proposedRow).toContainText("Morgan Manager: Agreed: the sanctions nexus is material.");

    // Nothing calculated changed: the factor's own rationale is unchanged.
    const after = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as typeof factors;
    expect(after.find((f) => f.id === geo.id)!.rationale).toBe(geo.rationale);
  });

  test("a control edit is a new version, and unmapping keeps it on record", async ({ page, request }) => {
    const title = unique("E2E Controls");
    const id = await toHumanReview(request, title);
    const factors = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as {
      id: number;
      applicable: boolean;
      excluded: boolean;
    }[];
    const factor = factors.find((f) => f.applicable && !f.excluded)!;
    const control = (await api(request, "analyst", "POST", `/api/assessments/${id}/controls`, {
      risk_factor_id: factor.id,
      control_type: "SANCTIONS_SCREENING",
      owner: "Operations",
    })) as { id: number };

    await login(page, "analyst");
    await openAssessment(page, title);
    await stage(page, "Controls & Residual Risk").click();

    await page.getByRole("button", { name: "Edit / remap" }).click();
    await page.getByLabel("Control owner").fill("Financial Crime Compliance");
    await page.getByRole("button", { name: "Save new version" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "A reason is required" })).toBeVisible();
    await page.getByRole("textbox", { name: "Reason for this control change (required)" }).fill("Ownership moved to FCC.");
    await page.getByRole("button", { name: "Save new version" }).click();
    await expect(page.getByRole("button", { name: "History (v2)" })).toBeVisible();

    await page.getByRole("button", { name: "History (v2)" }).click();
    await expect(page.getByRole("list", { name: "Control change history" })).toContainText(
      "owner: Operations → Financial Crime Compliance. Reason: Ownership moved to FCC."
    );

    await page.getByRole("button", { name: "Unmap" }).click();
    await page.getByRole("textbox", { name: "Reason for this control change (required)" }).fill("Covered at portfolio level.");
    await page.getByRole("button", { name: "Unmap control" }).click();
    await expect(page.getByRole("button", { name: "Edit / remap" })).toHaveCount(0);

    const revisions = (await api(request, "analyst", "GET", `/api/assessments/${id}/controls/${control.id}/revisions`)) as {
      change_type: string;
    }[];
    expect(revisions.map((r) => r.change_type)).toEqual(["EDIT", "UNMAP"]);
  });
});
