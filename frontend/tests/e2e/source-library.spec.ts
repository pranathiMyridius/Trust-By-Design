import { expect, test } from "@playwright/test";
import { api, login, unique } from "./helpers";

// R5.1: a library admin uploads a policy document, reviews the extracted
// text, saves it as a draft and approves it; analysts then find it.

test("an admin uploads a policy document, approves it, and it becomes searchable", async ({ page, request }) => {
  const title = unique("E2E Sanctions Screening Policy");
  const marker = `screening${Date.now().toString(36)}`;

  await login(page, "admin");
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: "Source Library" }).click();
  await page.getByRole("button", { name: "Add source" }).click();

  // Unsupported files are refused with a clear message.
  await page.getByLabel("Upload a source document").setInputFiles({
    name: "tool.exe",
    mimeType: "application/octet-stream",
    buffer: Buffer.from("MZ"),
  });
  await expect(page.getByRole("alert").filter({ hasText: "Upload a PDF, DOCX, DOC, XLSX, TXT or CSV file." })).toBeVisible();

  const original =
    `Sanctions Screening Policy\n\nAll customers and payments are screened against consolidated sanctions lists ` +
    `(${marker}) before onboarding and before release.\n\nPotential matches are escalated to Compliance within one business day.`;
  await page.getByLabel("Upload a source document").setInputFiles({
    name: "sanctions-screening-policy.txt",
    mimeType: "text/plain",
    buffer: Buffer.from(original),
  });
  await expect(page.getByRole("status").filter({ hasText: "sanctions-screening-policy.txt" })).toBeVisible();

  // The text, title and reference were filled in from the document.
  await expect(page.getByLabel("Text (required)")).toHaveValue(/consolidated sanctions lists/);
  await expect(page.getByLabel("Title (required)")).toHaveValue("sanctions screening policy");
  await expect(page.getByLabel("Link or document reference")).toHaveValue("sanctions-screening-policy.txt");

  await page.getByLabel("Title (required)").fill(title);
  await page.getByLabel("Version (required)").fill("2026.1");
  await page.getByRole("button", { name: "Save as draft" }).click();
  await expect(page.getByRole("status").filter({ hasText: "with its original document" })).toBeVisible();

  const row = page.getByRole("row").filter({ hasText: title });
  await expect(row).toContainText("Draft");

  // The original document is kept and downloads byte for byte.
  await expect(row).toContainText("sanctions-screening-policy.txt");
  const downloading = page.waitForEvent("download");
  await row.getByRole("button", { name: "Download sanctions-screening-policy.txt" }).click();
  const download = await downloading;
  expect(download.suggestedFilename()).toBe("sanctions-screening-policy.txt");
  const fs = await import("node:fs/promises");
  expect(await fs.readFile((await download.path())!, "utf-8")).toBe(original);
  await row.getByRole("button", { name: "Approve" }).click();
  await row.getByRole("textbox", { name: "Reason to approve" }).fill("Reviewed against the current policy.");
  await row.getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("row").filter({ hasText: title })).toContainText("Approved");

  // An analyst now finds the uploaded text.
  const hits = (await api(request, "analyst", "GET", `/api/sources/search?q=${marker}`)) as { source_title: string }[];
  expect(hits.some((hit) => hit.source_title === title)).toBeTruthy();
});
