import { execSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { expect, type Page, test } from "@playwright/test";

const PY = process.env.KPI_E2E_PYTHON ?? "python";
const REAL_AI = (process.env.KPI_LLM_PROVIDER ?? "fake") === "ollama";

const REQUESTS: Record<string, string> = {
  fr: "Suivre les ventes mensuelles, le panier moyen et les cinq meilleurs produits.",
  en: "Track monthly sales, the average basket and the top five products.",
  ru: "Отслеживать продажи по месяцам, средний чек и пять лучших товаров.",
  "zh-Hans": "跟踪每月销售额、平均订单金额和前五名产品。",
  de: "Monatliche Umsätze, den durchschnittlichen Warenkorb und die fünf besten Produkte verfolgen.",
  es: "Seguir las ventas mensuales, el ticket medio y los cinco mejores productos.",
};

function locale(lang: string): Record<string, Record<string, string>> {
  return JSON.parse(readFileSync(new URL(`../../shared/locales/${lang}.json`, import.meta.url), "utf-8"));
}

function sessionUrl(): string {
  return execSync(`${PY} ../scripts/e2e_server.py token`).toString().trim().replace("http://127.0.0.1:8799", "");
}

async function noRawKeys(page: Page) {
  const body = await page.locator("body").innerText();
  expect(body).not.toMatch(/\b(errors|notes|issues|reasons|questions|dashboard|plan|upload|understand|fix|builder|export)\.[a-z_]+\b/);
}

for (const lang of Object.keys(REQUESTS)) {
  test(`parcours document → demande → KPI → dashboard → filtre → export [${lang}]`, async ({ page }) => {
    const L = locale(lang);
    await page.goto(sessionUrl());
    await expect(page).toHaveURL(/127\.0\.0\.1:8799\/$/); // le secret a été retiré de l'URL
    await page.getByTestId("lang-select").selectOption(lang);
    await expect(page.locator("h1")).toHaveText(L.upload.title);

    await page.getByTestId("example-ventes_demo.csv").click();
    await expect(page.locator("h1")).toHaveText(L.understand.title);
    await expect(page.getByTestId("table-card")).toHaveCount(1);
    await noRawKeys(page);

    await page.getByTestId("request-input").fill(REQUESTS[lang]);
    await page.getByTestId("submit-request").click();
    await expect(page.getByTestId("plan-review")).toBeVisible();

    // attendre la fin de l'interprétation (prêt, questions ou repli)
    const state = page.locator('[data-testid="validate-plan"], [data-testid="questions"], [data-testid="ai-failed"]').first();
    await expect(state).toBeVisible({ timeout: REAL_AI ? 200_000 : 30_000 });
    if (await page.getByTestId("ai-failed").isVisible()) {
      test.info().annotations.push({ type: "repli", description: `IA locale sans plan valide en ${lang} : constructeur guidé utilisé` });
      expect(REAL_AI, "le substitut CI doit toujours produire un plan").toBe(true);
      await page.getByTestId("builder-submit").click();
      await expect(page.locator('[data-testid="validate-plan"], [data-testid="questions"]').first()).toBeVisible();
    }
    for (let round = 0; round < 3 && (await page.getByTestId("questions").isVisible()); round++) {
      for (const fs of await page.locator("fieldset.question").all()) {
        await fs.locator("label.option").first().click();
      }
      await page.getByTestId("submit-answers").click();
      await page.waitForTimeout(500);
    }
    await noRawKeys(page);
    await page.getByTestId("validate-plan").click();

    await expect(page.getByTestId("dashboard")).toBeVisible();
    const firstValue = page.getByTestId("card-value").first();
    await expect(firstValue).not.toHaveText("");
    await expect(firstValue).not.toContainText("NaN");
    const scopeBefore = await page.getByTestId("scope").innerText();

    // filtre de catégorie : s'applique à tout le dashboard
    const dimSummary = page.locator('[data-testid^="dim-"]').first();
    if (await dimSummary.count()) {
      await dimSummary.click();
      await page.locator(".dim-values input[type=checkbox]").first().check();
      await expect(page.getByTestId("scope")).not.toHaveText(scopeBefore);
      await expect(page.getByTestId("reset-filters")).toBeVisible();
    }

    // « Vérifier ce chiffre »
    await page.locator('[data-testid^="verify-"]').first().click();
    await expect(page.getByTestId("evidence")).toBeVisible();
    await noRawKeys(page);
    await page.keyboard.press("Escape");

    // exports
    for (const fmt of ["csv", "pdf", "png"]) {
      const [download] = await Promise.all([page.waitForEvent("download"), page.getByTestId(`export-${fmt}`).click()]);
      const path = await download.path();
      expect(path).toBeTruthy();
      const size = readFileSync(path!).length;
      expect(size, `${fmt} non vide`).toBeGreaterThan(fmt === "csv" ? 50 : 5_000);
    }
    await noRawKeys(page);
  });
}

test("page sans session : accès refusé @mobile", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByTestId("session-required")).toBeVisible();
  const status = await page.evaluate(async () => (await fetch("/api/v1/projects")).status);
  expect(status).toBe(401);
});

test("dashboard lisible sur mobile, sans défilement horizontal @mobile", async ({ page }) => {
  await page.goto(sessionUrl());
  await page.getByTestId("example-ventes_demo.csv").click();
  await page.getByTestId("suggestion-total_amount").click();
  await page.getByTestId("suggestion-amount_by_period").click();
  await page.getByTestId("submit-request").click();
  await expect(page.locator('[data-testid="validate-plan"], [data-testid="questions"]').first()).toBeVisible();
  for (let round = 0; round < 3 && (await page.getByTestId("questions").isVisible().catch(() => false)); round++) {
    for (const fs of await page.locator("fieldset.question").all()) await fs.locator("label.option").first().click();
    await page.getByTestId("submit-answers").click();
    await page.waitForTimeout(500);
  }
  await page.getByTestId("validate-plan").click();
  await expect(page.getByTestId("dashboard")).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
