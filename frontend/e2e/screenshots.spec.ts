import { execSync } from "node:child_process";
import { expect, test } from "@playwright/test";

// Captures de contrôle visuel (données synthétiques uniquement). Lancement : KPI_SCREENSHOTS=1 npx playwright test screenshots
test.skip(!process.env.KPI_SCREENSHOTS, "captures à la demande");

const PY = process.env.KPI_E2E_PYTHON ?? "python";

for (const lang of ["fr", "ru", "zh-Hans"]) {
  test(`capture dashboard ${lang} @mobile`, async ({ page }, info) => {
    const url = execSync(`${PY} ../scripts/e2e_server.py token`).toString().trim().replace("http://127.0.0.1:8799", "");
    await page.goto(url);
    await page.getByTestId("lang-select").selectOption(lang);
    await page.getByTestId("example-ventes_demo.csv").click();
    for (const s of ["total_amount", "amount_by_period", "avg_basket", "top_products", "margin", "order_count"]) {
      await page.getByTestId(`suggestion-${s}`).click();
    }
    await page.getByTestId("submit-request").click();
    await expect(page.locator('[data-testid="validate-plan"], [data-testid="questions"]').first()).toBeVisible();
    for (let round = 0; round < 3 && (await page.getByTestId("questions").isVisible()); round++) {
      for (const fs of await page.locator("fieldset.question").all()) await fs.locator("label.option").first().click();
      await page.getByTestId("submit-answers").click();
      await page.waitForTimeout(400);
    }
    await page.getByTestId("validate-plan").click();
    await expect(page.getByTestId("dashboard")).toBeVisible();
    await page.waitForTimeout(800);
    await page.screenshot({ path: `../docs/screenshots/dashboard-${lang}-${info.project.name}.png`, fullPage: true });
  });
}
