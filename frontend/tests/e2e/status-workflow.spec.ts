import { expect, test } from "@playwright/test";
import {
  api,
  intakeRequest,
  login,
  openAssessment,
  rateAllFactors,
  readyForRiskIdentification,
  stage,
  unique,
  workflowStatus,
} from "./helpers";

// The status transitions that exist in the UI today:
//   Draft --Submit request--> Submitted (INTAKE)
//   INTAKE --Complete Intake--> EVIDENCE_COLLECTION (Intake Validation)
//   EVIDENCE_COLLECTION --[profile confirmed] Run Risk Identification--> RISK_IDENTIFICATION
//   RISK_IDENTIFICATION --[all factors rated] Confirm Risk Identification--> INHERENT_RISK_ASSESSMENT
// plus the gates that must refuse a move.

test.describe("Status workflow", () => {
  test("a request moves from draft through risk identification, with each gate enforced", async ({ page, request }) => {
    const title = unique("E2E Workflow");
    await api(request, "owner", "POST", "/api/assessments", { ...intakeRequest(title), is_draft: true });

    await login(page, "owner");
    await openAssessment(page, title);

    // Draft -> Submitted
    await expect(workflowStatus(page)).toContainText("Draft");
    await page.getByRole("button", { name: "Submit request" }).click();
    await expect(workflowStatus(page)).toContainText("Submitted");
    await expect(stage(page, "Intake")).toHaveAttribute("aria-current", "step");

    // INTAKE -> EVIDENCE_COLLECTION
    await page.getByRole("button", { name: "Complete Intake & Start Evidence Collection" }).click();
    await expect(stage(page, "Evidence Collection")).toHaveAttribute("aria-current", "step");
    await expect(workflowStatus(page)).toContainText("Intake Validation");

    // Gate: risk identification refuses an unconfirmed business profile.
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "has not been confirmed" })).toBeVisible();
    await expect(stage(page, "Evidence Collection")).toHaveAttribute("aria-current", "step");

    // The owner confirms the profile on the Intake step -- straight away,
    // without re-opening the assessment (regression: it used to appear
    // only after navigating away and back).
    await stage(page, "Intake").click();
    await page.getByRole("button", { name: "Confirm extracted information" }).click();
    await expect(page.getByRole("button", { name: "Confirmed" })).toBeVisible();

    // ...then EVIDENCE_COLLECTION -> RISK_IDENTIFICATION
    await stage(page, "Evidence Collection").click();
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(stage(page, "Risk Identification")).toHaveAttribute("aria-current", "step");
    await expect(workflowStatus(page)).toContainText("Risk Assessment in Progress");

    // Re-opened from the list, it shows where it really is -- no page
    // reload (regression: the list's stale copy used to win).
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Dashboard" }).click();
    await openAssessment(page, title);
    await expect(stage(page, "Risk Identification")).toHaveAttribute("aria-current", "step");
    await expect(workflowStatus(page)).toContainText("Risk Assessment in Progress");
  });

  test("an analyst cannot finalise inherent risk until every factor is rated", async ({ page, request }) => {
    const title = unique("E2E Rating gate");
    const created = await api(request, "owner", "POST", "/api/assessments", { ...intakeRequest(title), is_draft: false });
    // R3.3: risk identification needs the owner's confirmed profile first
    // (P8: the setup used to skip it, and the gate rightly refused).
    await readyForRiskIdentification(request, created.id);
    await api(request, "analyst", "POST", `/api/assessments/${created.id}/analyze`);

    await login(page, "analyst");
    await openAssessment(page, title);
    await expect(workflowStatus(page)).toContainText("RISK_IDENTIFICATION");

    await page.getByRole("button", { name: "Confirm Risk Identification & Continue" }).click();
    // Refused, with the actual reason (regression: it used to blame "no
    // usable risk results").
    const refusal = page.getByRole("alert").filter({ hasText: "must be rated" });
    await expect(refusal).toBeVisible();
    await expect(refusal).not.toContainText("no usable");
    // A refused move is reported beside the button, not left in the dialog.
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(workflowStatus(page)).toContainText("RISK_IDENTIFICATION");

    await rateAllFactors(page, 2, 3);
    await page.getByRole("button", { name: "Confirm Risk Identification & Continue" }).click();
    // Every stage move shows its own steps in the progress dialog.
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText("to Inherent Risk Assessment");
    // The whole lifecycle, each stage once.
    await expect(dialog.getByRole("list", { name: "Pipeline stages" }).locator(":scope > li")).toHaveCount(8);
    await expect(dialog.getByRole("list", { name: "Inherent Risk Assessment steps" })).toContainText(
      "Calculating and freezing inherent risk"
    );
    await expect(workflowStatus(page)).toContainText("INHERENT_RISK_ASSESSMENT");
    await expect(dialog).toHaveCount(0);
  });

  test("a business user cannot rate risk factors", async ({ page, request }) => {
    const title = unique("E2E Owner rating");
    const created = await api(request, "owner", "POST", "/api/assessments", { ...intakeRequest(title), is_draft: false });
    await readyForRiskIdentification(request, created.id); // R3.3 (see above)
    await api(request, "analyst", "POST", `/api/assessments/${created.id}/analyze`);

    await login(page, "owner");
    await openAssessment(page, title);
    await page.getByRole("button", { name: "Rate Factor", exact: true }).first().click();
    await page.getByRole("button", { name: "Save Rating" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "can rate risk factors" })).toBeVisible();

    const factors = await api(request, "analyst", "GET", `/api/assessments/${created.id}/risk-factors`);
    expect(factors.every((factor: { likelihood: number | null }) => factor.likelihood === null)).toBeTruthy();
  });
});
