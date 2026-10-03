import { expect, test, type APIRequestContext } from "@playwright/test";
import { API, api, createAssessment, driveToHumanReview, login, openAssessment, stage, token, unique } from "./helpers";

// P8: role- and object-level access through the UI (R15.1, R15.3, R15.5).
// The full route x role matrix is backend/tests/api/test_p8_permission_sweep.py;
// these journeys prove the UI shows the same rules.

const CARD = "4111 1111 1111 1111";

async function uploadConfidential(request: APIRequestContext, id: number): Promise<number> {
  const response = await request.post(`${API}/api/assessments/${id}/documents`, {
    headers: { Authorization: `Bearer ${await token(request, "owner")}` },
    multipart: {
      confidentiality: "CONFIDENTIAL",
      document_type: "CUSTOMER_INFORMATION",
      file: {
        name: "merchant-list.txt",
        mimeType: "text/plain",
        buffer: Buffer.from(`Merchant settlement card ${CARD} for the pilot merchant.`),
      },
    },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()).id as number;
}

async function fileStatus(request: APIRequestContext, who: "owner" | "manager", documentId: number): Promise<number> {
  const response = await request.get(`${API}/api/assessments/documents/${documentId}/file`, {
    headers: { Authorization: `Bearer ${await token(request, who)}` },
  });
  return response.status();
}

test.describe("Access control", () => {
  test("a reviewing manager sees a confidential document masked and cannot open the original", async ({ page, request }) => {
    const title = unique("E2E Confidential");
    const id = await createAssessment(request, title);
    const documentId = await uploadConfidential(request, id);
    // The manager reviews it once submitted to them, so it is visible to
    // them: a refusal below is the classification rule, not visibility.
    await driveToHumanReview(request, id);
    await api(request, "owner", "POST", `/api/assessments/${id}/submit-to-manager`, {});
    await api(request, "manager", "GET", `/api/assessments/${id}`);

    // The owner may open the original; the reviewing manager may not.
    expect(await fileStatus(request, "owner", documentId)).toBe(200);
    expect(await fileStatus(request, "manager", documentId)).toBe(403);

    await login(page, "manager");
    await openAssessment(page, title);
    await stage(page, "Evidence Collection").click();
    await expect(page.getByText("Confidential — sensitive values are masked for your role").first()).toBeVisible();
    await expect(page.getByText(/the original file is not available to you/).first()).toBeVisible();
    await expect(page.getByRole("button", { name: "Download" }).first()).toBeDisabled();
    // The card number never reaches the manager's screen.
    await expect(page.getByText(CARD)).toHaveCount(0);

    // The refused download is on the audit trail.
    const audit = (await api(request, "owner", "GET", `/api/assessments/${id}/audit`)) as { action: string }[];
    expect(audit.map((event) => event.action)).toContain("ACCESS_DENIED");

    // Leave no work in the manager's queue for other journeys (the
    // Approvals page tests expect only their own case there).
    await api(request, "manager", "POST", `/api/assessments/${id}/manager-decision`, {
      decision: "return",
      comment: "E2E clean-up: returned to the owner.",
    });
  });

  test("an auditor can look at everything but change nothing", async ({ page, request }) => {
    const email = `auditor.${Date.now().toString(36)}@e2e.test`;
    const password = "E2E-Auditor-Test-Only-1";
    await api(request, "admin", "POST", "/api/users", { email, password, full_name: "Ada Auditor", role: "AUDITOR" });
    const title = unique("E2E Auditor view");
    const id = await createAssessment(request, title);

    await page.goto("/");
    await page.getByLabel("Email").fill(email);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    const nav = page.getByRole("navigation", { name: "Main navigation" });
    await expect(nav).toBeVisible();
    await expect(page.getByRole("note").filter({ hasText: "Read-only access (Auditor)" })).toBeVisible();
    await expect(nav.getByRole("button", { name: "New Assessment" })).toHaveCount(0);

    await openAssessment(page, title);
    await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();

    // Every write is refused by the server, whatever the UI offers.
    const login = await request.post(`${API}/api/auth/login`, { data: { email, password } });
    const auditorToken = (await login.json()).access_token as string;
    const headers = { Authorization: `Bearer ${auditorToken}` };
    for (const [method, path, data] of [
      ["POST", "/api/assessments", { title: "x" }],
      ["PATCH", `/api/assessments/${id}/advance-stage`, {}],
      ["POST", `/api/assessments/${id}/intelligence/confirm`, {}],
    ] as const) {
      const response = await request.fetch(`${API}${path}`, { method, data, headers });
      expect(response.status(), `${method} ${path}`).toBe(403);
    }
    const assessment = await api(request, "owner", "GET", `/api/assessments/${id}`);
    expect(assessment.status).toBe("INTAKE");
  });
});
