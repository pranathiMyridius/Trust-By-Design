import { expect, test } from "@playwright/test";
import { api, intakeRequest, login, unique } from "./helpers";

test.describe("Create assessment", () => {
  test("a business user submits an assessment and sees it on the dashboard", async ({ page }) => {
    const title = unique("E2E Merchant acquiring");
    await login(page, "owner");

    await page.getByRole("button", { name: "New Assessment", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Create New Assessment" })).toBeVisible();

    const form = page.getByRole("form", { name: "Assessment intake request" });
    await form.getByRole("button", { name: "Expand all" }).click();
    for (const [field, value] of Object.entries(intakeRequest(title))) {
      const input = form.locator(`#intake-${field}`);
      if (field === "change_type") {
        await input.selectOption(value);
      } else {
        await input.fill(value);
      }
    }
    await form.getByRole("button", { name: "Continue to Profile Confirm" }).click();

    // Submitting opens the new assessment (profile confirmation happens
    // on its Intake step); it is also listed on the dashboard.
    await expect(page.getByRole("heading", { name: "Create New Assessment" })).toHaveCount(0);
    await expect(page.getByText(title).first()).toBeVisible();
    await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Dashboard" }).click();
    const row = page.getByRole("button", { name: `Open assessment ${title}` });
    await expect(row).toBeVisible();
    await expect(row).toContainText("NEW_GEOGRAPHY");
    await expect(row.getByRole("img", { name: "Risk level: Unrated" })).toBeVisible();
  });

  test("missing mandatory fields are flagged and nothing is created", async ({ page, request }) => {
    const title = unique("E2E Incomplete");
    await login(page, "owner");
    await page.getByRole("button", { name: "New Assessment", exact: true }).click();

    const form = page.getByRole("form", { name: "Assessment intake request" });
    await form.locator("#intake-title").fill(title);
    await form.getByRole("button", { name: "Continue to Profile Confirm" }).click();

    await expect(form.locator("#intake-description-error")).toBeVisible();
    // Focus moves to the first invalid field in on-screen order.
    await expect(form.locator("#intake-product_or_service_name")).toBeFocused();
    await expect(page.getByRole("heading", { name: "Create New Assessment" })).toBeVisible();

    const matches = await api(request, "owner", "GET", `/api/assessments?search=${encodeURIComponent(title)}`);
    expect(matches).toHaveLength(0);
  });

  test("a request can be saved as a draft with only a title", async ({ page }) => {
    const title = unique("E2E Draft idea");
    await login(page, "owner");
    await page.getByRole("button", { name: "New Assessment", exact: true }).click();

    const form = page.getByRole("form", { name: "Assessment intake request" });
    await form.locator("#intake-title").fill(title);
    await form.getByRole("button", { name: "Save Draft" }).click();

    const row = page.getByRole("button", { name: `Open assessment ${title}` });
    await expect(row).toBeVisible();
    await expect(row).toContainText("Draft");
  });
});
