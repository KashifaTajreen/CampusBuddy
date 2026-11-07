import { defineConfig, devices } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const repository = process.env.CAMPUS_TEST_REPOSITORY_ROOT || path.resolve("..");
fs.mkdirSync(path.join(repository, ".scratch"), {recursive:true});
const fixtureDirectory = process.env.CAMPUS_TEST_DIRECTORY || fs.mkdtempSync(path.join(repository,".scratch/e2e-"));
process.env.CAMPUS_TEST_DIRECTORY = fixtureDirectory;

function fixturePort(name: string, fallback: number): number {
  const value = Number(process.env[name] || fallback);
  if (!Number.isInteger(value) || value < 1 || value > 65535) {
    throw new Error(`Invalid local test port: ${name}`);
  }
  return value;
}
const frontendPort = fixturePort("CAMPUS_TEST_FRONTEND_PORT", 3000);
const apiPort = fixturePort("CAMPUS_TEST_API_PORT", 8765);
const authPort = fixturePort("CAMPUS_TEST_AUTH_PORT", 8766);
const frontendUrl = `http://127.0.0.1:${frontendPort}`;
const apiUrl = `http://127.0.0.1:${apiPort}`;
const authUrl = `http://127.0.0.1:${authPort}`;
export default defineConfig({
  testDir: "./tests",
  // This suite needs separately installed E5/OCR assets and generated files.
  // scripts.verify_events_browser invokes its dedicated config explicitly.
  testIgnore: ["**/events-phase2.spec.ts"],
  fullyParallel: false,
  workers: 1,
  timeout: 60000,
  expect: { timeout: 15000 },
  reporter: [
    ["list"],
    ["json", { outputFile: "../artifacts/browser-tests.json" }],
  ],
  use: {
    baseURL: frontendUrl,
    channel: process.env.PLAYWRIGHT_CHANNEL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    {
      name: "desktop",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 1000 },
      },
    },
    {
      name: "mobile",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 390, height: 844 },
      },
    },
    {
      name: "tablet",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 768, height: 1024 },
      },
    },
  ],
  webServer: [
    {
      command:
        process.platform === "win32"
          ? ".venv\\Scripts\\python.exe -m backend.tests.e2e_server"
          : ".venv/bin/python -m backend.tests.e2e_server",
      cwd: process.env.CAMPUS_TEST_REPOSITORY_ROOT || "..",
      url: `${apiUrl}/health`,
      reuseExistingServer: false,
      env: {
        CAMPUS_TEST_DIRECTORY: fixtureDirectory,
        CAMPUS_TEST_API_PORT: String(apiPort),
        CAMPUS_TEST_AUTH_PORT: String(authPort),
        CAMPUS_TEST_FRONTEND_PORT: String(frontendPort),
      },
      timeout: 120000,
    },
    {
      command: process.env.CAMPUS_TEST_PRODUCTION_FRONTEND === "1"
        ? `npm run start -- --hostname 127.0.0.1 --port ${frontendPort}`
        : `npm run dev -- --port ${frontendPort}`,
      url: frontendUrl,
      reuseExistingServer: false,
      timeout: 120000,
      env: {
        NEXT_PUBLIC_API_URL: apiUrl,
        NEXT_PUBLIC_SUPABASE_URL: authUrl,
        NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "test-only-publishable-key",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
