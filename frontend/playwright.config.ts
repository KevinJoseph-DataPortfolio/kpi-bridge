import { defineConfig, devices } from "@playwright/test";

// Le serveur de test lance l'application compilée (API + frontend sur la même origine) et le worker,
// avec un répertoire de données jetable. KPI_LLM_PROVIDER=fake en CI ; =ollama pour le test du vrai chemin IA.
export default defineConfig({
  testDir: "./e2e",
  timeout: 240_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: "http://127.0.0.1:8799",
    acceptDownloads: true,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] }, grep: /@mobile/ },
  ],
  webServer: {
    command: `${process.env.KPI_E2E_PYTHON ?? "python"} ../scripts/e2e_server.py`,
    url: "http://127.0.0.1:8799/api/v1/health",
    reuseExistingServer: false,
    timeout: 120_000,
    env: { KPI_LLM_PROVIDER: process.env.KPI_LLM_PROVIDER ?? "fake" },
  },
});
