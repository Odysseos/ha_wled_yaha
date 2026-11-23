// CI-only rendering checks. Requires: npx playwright install --with-deps chromium
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import { fixtureHtml } from "./fixture.mjs";
const browser = await chromium.launch({ headless: true });
try {
  const page = await browser.newPage({
    viewport: { width: 1280, height: 800 },
  });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setContent(await fixtureHtml());
  await page.locator('[data-row="r5"]').waitFor();
  assert.equal(await page.locator("[data-row]").count(), 5);
  assert.match(
    await page.locator('[data-row="r1"]').textContent(),
    /Нет пресетов/,
  );
  assert.equal(await page.locator("#confirm").textContent(), "Подтвердить");
  const layout = await page.locator("wled-yaha-panel").evaluate((el) => {
    const root = el.shadowRoot;
    const master = root.querySelector(".master").getBoundingClientRect(),
      detail = root.querySelector(".detail").getBoundingClientRect(),
      footer = root.querySelector("footer").getBoundingClientRect();
    return {
      master: {
        left: master.left,
        right: master.right,
        top: master.top,
        bottom: master.bottom,
      },
      detail: { left: detail.left, top: detail.top, bottom: detail.bottom },
      footer: footer.bottom,
      height: innerHeight,
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
  });
  assert.ok(layout.master.right < layout.detail.left);
  assert.equal(layout.master.top, layout.detail.top);
  assert.ok(layout.footer <= layout.height);
  assert.equal(layout.overflow, false);
  await page.locator('[data-select="r2"]').click();
  await page.locator('[data-scene="party"]').selectOption("none");
  await page.locator('[data-select="r3"]').click();
  await page.locator('[data-scene="candle"]').selectOption("id:12");
  await page.locator('[data-select="r2"]').click();
  assert.equal(await page.locator('[data-scene="party"]').inputValue(), "none");
  await page.locator("#save-profile").click();
  await page.locator("#profile-name").fill("Общий");
  await page.locator("#create-profile").click();
  await page.locator("#copy").click();
  await page.locator("#apply-copy").click();
  assert.match(
    await page.locator("#copy-report").textContent(),
    /Изменено назначений/,
  );
  await page.locator("dialog .close").click();
  await page.locator("#confirm").click();
  await page
    .locator("#message")
    .filter({ hasText: "Настройки сохранены." })
    .waitFor();
  await page.locator('[data-scene="party"]').selectOption("id:7");
  await page.locator("#fixture-stale").click();
  assert.equal(await page.locator("#confirm").isEnabled(), false);
  assert.match(await page.locator("#message").textContent(), /другом окне/);
  await page.locator("#cancel").click();
  await mkdir("test-results", { recursive: true });
  await page.screenshot({ path: "test-results/wled-yaha-desktop.png" });
  await page.setViewportSize({ width: 390, height: 844 });
  const mobile = await page.locator("wled-yaha-panel").evaluate((el) => {
    const root = el.shadowRoot;
    return {
      master: root.querySelector(".master").getBoundingClientRect().bottom,
      detail: root.querySelector(".detail").getBoundingClientRect().top,
      footer: root.querySelector("footer").getBoundingClientRect().bottom,
      height: innerHeight,
      overflow: document.documentElement.scrollWidth > innerWidth,
    };
  });
  assert.ok(mobile.master < mobile.detail);
  assert.ok(mobile.footer <= mobile.height);
  assert.equal(mobile.overflow, false);
  await page.locator("#copy").click();
  await page.locator("dialog").waitFor();
  const dialog = await page.locator("dialog").boundingBox();
  assert.ok(dialog.x >= 0 && dialog.x + dialog.width <= 390);
  await page.locator("dialog .close").click();
  await page.screenshot({ path: "test-results/wled-yaha-mobile.png" });
  assert.deepEqual(errors, []);
  console.log(
    "Browser: desktop/mobile layout, editing, profiles, copying, save and stale revision passed.",
  );
} finally {
  await browser.close();
}
