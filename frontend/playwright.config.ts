import { defineConfig, devices } from "@playwright/test";

// End-to-end tests for the main user journeys (tests/e2e).
//
// Playwright starts two servers:
//   * the real FastAPI backend via backend/tests/e2e_server.py, on
//     127.0.0.1:8000 (the address src/api hard-codes), with a fresh
//     throwaway database, seeded test users and a deterministic fake AI
//     provider (set E2E_REAL_LLM=1 to use the real one);
//   * the Vite dev server.
//
// The backend is never reused: if something is already listening on
// port 8000 (e.g. your own dev backend with the real database) the run
// stops instead of writing test data into it.

const isWindows = process.platform === "win32";
const python =
  process.env.E2E_PYTHON ?? (isWindows ? ".venv\\Scripts\\python.exe" : ".venv/bin/python");
const FRONTEND_PORT = Number(process.env.E2E_FRONTEND_PORT ?? 5173);

export default defineConfig({
  testDir: "./tests/e2e",
  // The journeys share one backend database; keep them sequential so
  // dashboard assertions are deterministic.
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }], ["junit", { outputFile: "test-results/e2e-junit.xml" }]]
    : [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    // For demos: E2E_SLOWMO=800 pauses 800 ms between actions so a
    // headed run can be followed by eye. 0 (default) = full speed.
    launchOptions: { slowMo: Number(process.env.E2E_SLOWMO ?? 0) },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `${python} tests/e2e_server.py`,
      cwd: "../backend",
      url: "http://127.0.0.1:8000/health",
      reuseExistingServer: false,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
    },
    {
      command: `npm run dev -- --port ${FRONTEND_PORT} --strictPort`,
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
    },
  ],
});
