import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";
import {
  createAssessment,
  login,
  openAssessment,
  rateAllFactors,
  readyForRiskIdentification,
  stage,
  unique,
  workflowStatus,
} from "./helpers";

// The E2E backend uses the deterministic fake AI provider (see
// backend/tests/support/fake_llm.py), so the identified factors are
// stable: for this intake it flags product/service, geographic,
// delivery-channel and third-party risk. AI *quality* is measured by the
// DeepEval suite, not here -- these tests prove the journey works.

function inherentRiskPanel(page: Page) {
  return page.locator("section", { has: page.getByRole("heading", { name: "Inherent Risk Calculation (Stage 6)" }) });
}

test.describe("Analyze assessment", () => {
  test("an analyst runs risk identification and sees factors, reasoning, score and level", async ({ page, request }) => {
    const title = unique("E2E Analyze");
    const id = await createAssessment(request, title);
    await readyForRiskIdentification(request, id);

    await login(page, "analyst");
    await openAssessment(page, title);

    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(page.getByRole("heading", { name: "Risk Factor Categories" })).toBeVisible();
    await expect(stage(page, "Risk Identification")).toHaveAttribute("aria-current", "step");

    // Risk dimensions with reasoning and verified evidence.
    for (const category of ["Geographic Risk", "Third-Party or Vendor Risk"]) {
      await expect(page.getByRole("heading", { name: category, exact: true })).toBeVisible();
    }
    await expect(page.getByText("APPLIES").first()).toBeVisible();
    await expect(page.getByText("NOT APPLICABLE").first()).toBeVisible();
    await expect(page.getByText("Rationale:").first()).toBeVisible();
    await expect(page.getByText("Potential misuse:").first()).toBeVisible();
    await expect(page.getByText("Evidence verified").first()).toBeVisible();

    // Nothing is scored until an analyst rates the factors.
    const summary = page.getByRole("complementary").filter({ hasText: "OVERALL INHERENT RISK" });
    await expect(summary).toContainText("NOT ANALYZED");

    const rated = await rateAllFactors(page, 4, 4);
    expect(rated).toBeGreaterThan(0);

    // 4 x 4 on a 5 x 5 scale = 64 for every factor -> weighted mean 64 -> HIGH.
    const calculation = inherentRiskPanel(page);
    await expect(calculation).toContainText("64.0");
    await expect(calculation).toContainText("HIGH");
    await expect(calculation).not.toContainText("Provisional");

    // The official result is also what the dashboard shows.
    // Closing the assessment refreshes the lists -- no page reload needed.
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Dashboard" }).click();
    const row = page.getByRole("button", { name: `Open assessment ${title}` });
    await expect(row.getByLabel("Risk score 64")).toBeVisible();
    await expect(row.getByRole("img", { name: "Risk level: High" })).toBeVisible();
  });

  test("the overall inherent risk summary updates after rating", async ({ page, request }) => {
    // Regression: the sidebar used to keep showing "—" / NOT ANALYZED
    // after rating until the assessment was re-opened.
    const title = unique("E2E Sidebar");
    const id = await createAssessment(request, title);
    await readyForRiskIdentification(request, id);

    await login(page, "analyst");
    await openAssessment(page, title);
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await rateAllFactors(page, 4, 4);
    await expect(inherentRiskPanel(page)).toContainText("64.0");

    const summary = page.getByRole("complementary").filter({ hasText: "OVERALL INHERENT RISK" });
    await expect(summary.locator(".overall-risk-score")).toHaveText("64", { timeout: 3_000 });
  });

  test("an analyst can analyze straight from the dashboard", async ({ page, request }) => {
    const title = unique("E2E Dashboard analyze");
    const id = await createAssessment(request, title);
    // R3.3: the dashboard Analyze action is refused on an unconfirmed
    // profile; the owner confirms it first (P8: the setup used to skip it).
    await readyForRiskIdentification(request, id);

    await login(page, "analyst");
    const row = page.getByRole("button", { name: `Open assessment ${title}` });
    await row.getByRole("button", { name: "Analyze" }).click();
    await expect(row).toContainText("Risk Assessment in Progress");
  });

  test("an AI outage yields a labelled rules-only result a reviewer can acknowledge and progress", async ({ page, request }) => {
    const title = unique("E2E Outage");
    const id = await createAssessment(request, title, {
      description: "SIMULATE_AI_OUTAGE: new card product in Germany with customer data shared with a processor.",
    });
    await readyForRiskIdentification(request, id);

    await login(page, "analyst");
    await openAssessment(page, title);
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();

    await expect(page.getByRole("heading", { name: "Risk Factor Categories" })).toBeVisible();
    await expect(page.getByText("Rule engine (AI unavailable)").first()).toBeVisible();
    await expect(page.getByText("AI Identified", { exact: true })).toHaveCount(0);

    // The degraded mode is stated plainly, and blocks progress until acknowledged.
    const banner = page.getByRole("region", { name: "Provisional analysis" });
    await expect(banner).toContainText("Provisional — rules-only result");
    await expect(banner).toContainText("deterministic risk rules only");

    await rateAllFactors(page, 3, 3);
    await page.getByRole("button", { name: "Confirm Risk Identification & Continue" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "must acknowledge" })).toBeVisible();

    await banner.getByRole("button", { name: "Acknowledge provisional result" }).click();
    await expect(banner).toContainText("Acknowledged by");

    await page.getByRole("button", { name: "Confirm Risk Identification & Continue" }).click();
    await expect(workflowStatus(page)).toContainText("INHERENT_RISK_ASSESSMENT");
  });

  test("unrated factors are not presented as low risk", async ({ page, request }) => {
    // Regression: the Active Risk Indicator Map and Risk Dimensions panels
    // used to show every applicable-but-unrated category as LOW / 0.
    const title = unique("E2E Unrated");
    const id = await createAssessment(request, title);
    await readyForRiskIdentification(request, id);

    await login(page, "analyst");
    await openAssessment(page, title);
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(page.getByRole("heading", { name: "Active Risk Indicator Map" })).toBeVisible();

    const indicatorMap = page.locator("section", { has: page.getByRole("heading", { name: "Active Risk Indicator Map" }) });
    await expect(indicatorMap.getByRole("heading", { name: "GEOGRAPHIC_RISK" })).toBeVisible();
    await expect(indicatorMap.getByText("UNRATED").first()).toBeVisible();
    await expect(indicatorMap.locator(".risk-score-badge.low")).toHaveCount(0);

    const dimensions = page.locator("section", { has: page.getByRole("heading", { name: "Risk Dimensions" }) });
    await expect(dimensions.getByLabel("Not yet rated").first()).toBeVisible();

    // Once rated, the real level replaces the placeholder.
    await rateAllFactors(page, 1, 2);
    await expect(indicatorMap.getByText("UNRATED")).toHaveCount(0);
    await expect(indicatorMap.locator(".risk-score-badge.low").first()).toBeVisible();
  });
});
