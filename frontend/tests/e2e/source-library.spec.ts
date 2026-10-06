import { expect, test, type Page } from "@playwright/test";
import { api, login, openFromMore, token, unique } from "./helpers";

// Source Library: a maintainer adds a source with a PDF, a compliance reviewer
// (Head of FCRM) approves it, ordinary users only ever see approved sources,
// and nobody can approve what they prepared or without the reviewer role.

/** A tiny valid text PDF, built by hand so the test needs no fixture file. */
function pdf(text: string): Buffer {
  const objects = [
    "<< /Type /Catalog /Pages 2 0 R >>",
    "<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
    "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents 5 0 R >>",
  ];
  const stream = `BT /F1 12 Tf 50 740 Td (${text}) Tj ET`;
  objects.push(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
  let out = "%PDF-1.4\n";
  const offsets: number[] = [];
  objects.forEach((body, index) => {
    offsets.push(out.length);
    out += `${index + 1} 0 obj\n${body}\nendobj\n`;
  });
  const xref = out.length;
  out += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  offsets.forEach((offset) => (out += `${String(offset).padStart(10, "0")} 00000 n \n`));
  out += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(out, "latin1");
}

async function openLibrary(page: Page) {
  await openFromMore(page, "Source Library");
  await expect(page.getByRole("heading", { name: "Source Library", level: 2 })).toBeVisible();
}

test("a maintainer adds a source with a PDF, a reviewer approves it, and analysts see only approved sources", async ({ page, request, browser }) => {
  const title = unique("E2E RBI KYC Direction");
  const marker = `kycmarker${Date.now().toString(36)}`;

  // -- the maintainer (Admin) adds a source with an official link and a PDF
  await login(page, "admin");
  await openLibrary(page);
  await page.getByRole("button", { name: "Add source" }).click();
  const dialog = page.getByRole("dialog", { name: "Add source" });

  // A non-PDF is refused in the form itself.
  await dialog.getByLabel("Upload a PDF document").setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(dialog.getByRole("alert").filter({ hasText: "Only PDF documents (.pdf) can be uploaded." })).toBeVisible();

  // Required fields are validated.
  await dialog.getByRole("button", { name: "Save as draft" }).click();
  await expect(dialog.getByText("Title is required.")).toBeVisible();
  await expect(dialog.getByText("A version label is required.")).toBeVisible();

  await dialog.getByLabel(/^Title/).fill(title);
  await dialog.getByLabel(/^Source authority/).fill("Reserve Bank of India");
  await dialog.getByLabel("Jurisdiction", { exact: true }).fill("India");
  await dialog.getByLabel("Topics").fill("KYC, CDD");
  await dialog.getByLabel("Official URL").fill("https://www.rbi.org.in/example");
  await dialog.getByLabel(/^Version label/).fill("2026.1");
  await dialog.getByLabel("Upload a PDF document").setInputFiles({
    name: "kyc-direction.pdf",
    mimeType: "application/pdf",
    buffer: pdf(`Regulated entities shall identify the beneficial owner ${marker}`),
  });
  await dialog.getByRole("button", { name: "Save as draft" }).click();

  // The detail view opens on the new draft, with the document processed.
  await expect(page.getByRole("heading", { name: title, level: 2 })).toBeVisible();
  await expect(page.getByRole("status").filter({ hasText: "saved as a draft" })).toBeVisible();
  const version = page.getByRole("article", { name: "Version 2026.1" });
  await expect(version).toContainText("Draft");
  await expect(version).toContainText("kyc-direction.pdf");
  await expect(version).toContainText("Malware scan passed");
  await expect(version).toContainText("Text extracted");
  await expect(version).toContainText(/SHA-256 [0-9a-f]{64}/);

  // The maintainer submits it; an Admin cannot approve (no reviewer role).
  await version.getByRole("button", { name: "Submit for review" }).click();
  await expect(version).toContainText("In review");
  await expect(version.getByRole("button", { name: "Approve" })).toHaveCount(0);

  // An ordinary user sees nothing yet.
  const hidden = (await api(request, "analyst", "GET", `/api/source-library/records?q=${encodeURIComponent(title)}`)) as { total: number };
  expect(hidden.total).toBe(0);

  // -- the reviewer (Head of FCRM) approves, with a comment
  const reviewerPage = await browser.newPage();
  await login(reviewerPage, "head");
  await openLibrary(reviewerPage);
  await reviewerPage.getByRole("button", { name: "In review", exact: false }).first().click();
  await reviewerPage.getByRole("searchbox", { name: "Search sources" }).fill(title);
  await reviewerPage.getByRole("button", { name: new RegExp(`Open source .* ${title}`) }).click();
  const reviewVersion = reviewerPage.getByRole("article", { name: "Version 2026.1" });
  await reviewVersion.getByRole("button", { name: "Approve" }).click();
  const approve = reviewerPage.getByRole("dialog", { name: /Approve version 2026.1/ });
  await expect(approve.getByRole("button", { name: "Approve" })).toBeDisabled(); // comment required
  await approve.getByLabel("Comment").fill("Checked against the RBI Direction text.");
  await approve.getByRole("button", { name: "Approve" }).click();
  await expect(reviewerPage.getByRole("status").filter({ hasText: "Version approved." })).toBeVisible();
  await expect(reviewVersion).toContainText("Approved");

  // History and audit timeline show who did what.
  await reviewerPage.getByRole("tab", { name: "Approval history" }).click();
  await expect(reviewerPage.getByText("Checked against the RBI Direction text.")).toBeVisible();
  await expect(reviewerPage.getByText("Submitted for review")).toBeVisible();
  await reviewerPage.getByRole("tab", { name: "Audit timeline" }).click();
  await expect(reviewerPage.getByText("Source created")).toBeVisible();
  await reviewerPage.close();

  // -- analysts now find the approved source and its passage, and can download the PDF
  const found = (await api(request, "analyst", "GET", `/api/source-library/passages?q=${marker}`)) as { title: string; page_start: number }[];
  expect(found[0].title).toBe(title);
  expect(found[0].page_start).toBe(1);

  await page.close();
});

test("ordinary users can search but cannot add, edit or approve", async ({ page }) => {
  await login(page, "analyst");
  await openLibrary(page);
  await expect(page.getByRole("button", { name: "Add source" })).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "Help articles" })).toHaveCount(0);
  await page.getByRole("tab", { name: "Search passages" }).click();
  await expect(page.getByRole("button", { name: "Search", exact: true })).toBeDisabled();
});

test("the library can be searched, filtered and paged", async ({ page, request }) => {
  const tag = Date.now().toString(36);
  for (let index = 1; index <= 3; index += 1) {
    await request.fetch("http://127.0.0.1:8000/api/source-library/records", {
      method: "POST",
      multipart: {
        title: `Paged ${tag} ${index}`,
        authority: index === 1 ? "FATF" : "RBI",
        category: index === 1 ? "SANCTIONS_RESOURCE" : "REGULATORY_REQUIREMENT",
        version_label: "1",
        jurisdiction: index === 1 ? "Global" : "India",
      },
      headers: { Authorization: `Bearer ${await token(request, "admin")}` },
    });
  }
  await login(page, "admin");
  await openLibrary(page);
  await page.getByRole("searchbox", { name: "Search sources" }).fill(`Paged ${tag}`);
  await expect(page.getByRole("heading", { name: /^Sources \(3\)$/ })).toBeVisible();
  await page.getByLabel("Filter by authority").selectOption("RBI");
  await expect(page.getByRole("heading", { name: /^Sources \(2\)$/ })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await page.getByRole("searchbox", { name: "Search sources" }).fill(`Paged ${tag}`);
  await page.getByLabel("Per page").selectOption("10");
  await expect(page.getByText("Showing 1–3 of 3")).toBeVisible();
});
