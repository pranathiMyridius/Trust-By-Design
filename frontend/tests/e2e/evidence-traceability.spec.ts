import { expect, test, type APIRequestContext } from "@playwright/test";
import {
  API,
  api,
  intakeRequest,
  login,
  openAssessment,
  readyForRiskIdentification,
  stage,
  token,
  unique,
} from "./helpers";

// P4 evidence traceability (docs/REMAINING_REQUIREMENTS.md): field
// provenance, expired-evidence acknowledgement, intake history, fixed
// Stage 4 rules, analyst indicator edits and the R5.4 evidence categories.

const BRIEF =
  "Project Name: Merchant Acquiring DE\n" +
  "Target Destination Jurisdictions: Germany, Poland\n" +
  "Primary Vendor: Acme Processing Ltd (card processor)\n";

async function createWithDocument(request: APIRequestContext, title: string): Promise<number> {
  const response = await request.post(`${API}/api/assessments/create-with-document`, {
    headers: { Authorization: `Bearer ${await token(request, "owner")}` },
    multipart: {
      ...intakeRequest(title),
      is_draft: "false",
      files: { name: "brief.txt", mimeType: "text/plain", buffer: Buffer.from(BRIEF) },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).id as number;
}

async function uploadExpired(request: APIRequestContext, id: number): Promise<number> {
  const yesterday = new Date(Date.now() - 2 * 86_400_000).toISOString().slice(0, 10);
  const response = await request.post(`${API}/api/assessments/${id}/documents`, {
    headers: { Authorization: `Bearer ${await token(request, "owner")}` },
    multipart: {
      expiry_date: yesterday,
      file: { name: "vendor-policy-2024.txt", mimeType: "text/plain", buffer: Buffer.from("Vendor control policy for the processor.") },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).id as number;
}

test.describe("Evidence traceability", () => {
  test("an expired document must be decided on before risk identification, and the profile shows its provenance", async ({
    page,
    request,
  }) => {
    const title = unique("E2E Expired evidence");
    const id = await createWithDocument(request, title);
    await readyForRiskIdentification(request, id);
    await uploadExpired(request, id);

    await login(page, "owner");
    await openAssessment(page, title);

    // Refused while the expired document is undecided.
    await expect(stage(page, "Intake & Evidence")).toHaveAttribute("aria-current", "step");
    await expect(page.getByText(/vendor-policy-2024\.txt/).first()).toBeVisible();
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Expired evidence must be acknowledged" }).first()).toBeVisible();
    await expect(stage(page, "Intake & Evidence")).toHaveAttribute("aria-current", "step");

    // The owner decides, with a reason.
    const decision = page.getByRole("textbox", { name: "Reason for vendor-policy-2024.txt" });
    await decision.fill("Policy unchanged; the renewal is in progress.");
    await page.getByRole("button", { name: "Use as evidence" }).click();
    await expect(page.getByText(/Used as evidence by/)).toBeVisible();

    // Intake step: provenance and the versioned history.
    await stage(page, "Intake & Evidence").click();
    const provenance = page.getByTestId("field-provenance");
    await provenance.locator("summary").click();
    await expect(provenance).toContainText("brief.txt");
    await expect(provenance).toContainText("Countries");
    const history = page.getByTestId("intake-history");
    await history.locator("summary").click();
    await expect(history).toContainText("Profile confirmed");
    await expect(history).toContainText("Request v1");

    await stage(page, "Intake & Evidence").click();
    await page.getByRole("button", { name: "Run Risk Identification & Continue" }).click();
    await expect(stage(page, "Risk Identification")).toHaveAttribute("aria-current", "step");
  });

  test("the analyst sees rule-required factors, edits indicators with a reason, and reads evidence categories", async ({
    page,
    request,
  }) => {
    const title = unique("E2E Stage 4 rules");
    const id = await createWithDocument(request, title);
    await readyForRiskIdentification(request, id);
    await api(request, "analyst", "PATCH", `/api/assessments/${id}/advance-stage`, {});

    const factors = (await api(request, "analyst", "GET", `/api/assessments/${id}/risk-factors`)) as {
      id: number;
      category: string;
      rule_triggers: { rule_id: string }[];
    }[];
    const channel = factors.find((f) => f.category === "DELIVERY_CHANNEL_RISK")!;
    expect(channel.rule_triggers.map((t) => t.rule_id)).toContain("S4-02-REMOTE-DIGITAL-CHANNEL");

    await login(page, "analyst");
    await openAssessment(page, title);
    await expect(stage(page, "Risk Identification")).toHaveAttribute("aria-current", "step");
    const triggers = page.getByTestId(`rule-triggers-${channel.id}`);
    await expect(triggers).toContainText("Remote digital delivery channel");
    await expect(triggers).toContainText("pending business validation");

    const card = page.locator(".risk-result-card").filter({ has: triggers });
    await card.getByRole("button", { name: "Edit indicators" }).click();
    await card.getByLabel("Data and monitoring limitations").check();
    await expect(card.getByRole("button", { name: "Save indicators" })).toBeDisabled();
    await card.getByRole("textbox", { name: "Reason for the indicator change" }).fill("Channel logs are not fed to monitoring yet.");
    await card.getByRole("button", { name: "Save indicators" }).click();
    await expect(card.getByRole("button", { name: "Edit indicators" })).toBeVisible();
    await expect(card).toContainText("Data and monitoring limitations");

    const ledger = (await api(request, "analyst", "GET", `/api/assessments/${id}/overrides`)) as {
      section: string;
      field_name: string;
      reason: string;
    }[];
    expect(ledger.some((row) => row.section === "RISK_CATEGORY" && row.field_name === "indicators")).toBeTruthy();

    // R5.4: every statement says what it rests on.
    const statements = (await api(request, "analyst", "GET", `/api/assessments/${id}/explain/statements`)) as {
      evidence_category_counts: Record<string, number>;
    };
    expect(statements.evidence_category_counts.DIRECT_EVIDENCE).toBeGreaterThan(0);
    expect(statements.evidence_category_counts.EXTRACTED_INFORMATION).toBeGreaterThan(0);
  });
});
