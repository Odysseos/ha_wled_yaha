import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { fixture } from "./fixture.mjs";
const dom = new JSDOM("<!doctype html><html><body></body></html>", {
  url: "https://ha.test/",
});
for (const key of ["window", "document", "HTMLElement", "customElements"])
  globalThis[key] = dom.window[key];
dom.window.HTMLDialogElement.prototype.showModal = function () {
  this.open = true;
};
dom.window.HTMLDialogElement.prototype.close = function () {
  this.open = false;
};
await import("../../custom_components/wled_yaha/www/editor.js");
const settle = () => new Promise((resolve) => setImmediate(resolve));
async function panel(data = fixture()) {
  document.body.replaceChildren();
  const el = document.createElement("wled-yaha-panel");
  document.body.append(el);
  const calls = [];
  let revision = data.revision;
  el.hass = {
    locale: { language: "ru" },
    states: Object.fromEntries(
      data.devices.map((d) => [d.entity_id, { state: d.state }]),
    ),
    connection: { subscribeMessage: async () => () => {} },
    callWS: async (msg) => {
      calls.push(msg);
      if (msg.type.endsWith("/get")) return structuredClone(data);
      if (msg.type.endsWith("/save")) {
        if (msg.revision !== revision) throw { code: "stale_editor" };
        data.settings = structuredClone(msg.settings);
        data.revision = revision += "x";
        return structuredClone(data);
      }
      return { devices: [], errors: [], warnings: [] };
    },
  };
  await settle();
  return {
    el,
    calls,
    q: (s) => el.shadowRoot.querySelector(s),
    qa: (s) => [...el.shadowRoot.querySelectorAll(s)],
    external: () => {
      data.revision = "external";
      el.receive(structuredClone(data));
    },
  };
}
function change(el, value) {
  if (el.type === "checkbox") el.checked = value;
  else el.value = value;
  el.dispatchEvent(new dom.window.Event("change", { bubbles: true }));
}
test("all devices stay in one list with live status and explicit empty catalogs", async () => {
  const p = await panel();
  assert.equal(p.qa("[data-row]").length, 5);
  assert.match(p.q('[data-row="r1"]').textContent, /Нет пресетов/);
  assert.match(p.q('[data-row="r2"]').textContent, /Включена/);
  assert.match(p.q('[data-row="r4"]').textContent, /Пресеты не загружены/);
  assert.ok(p.q("[data-empty-presets]"));
  assert.equal(p.q("#confirm").disabled, true);
  p.q('[data-select="r2"]').click();
  assert.equal(p.q("h3").textContent, "Гирлянда WLED2");
  assert.equal(p.qa("[data-scene]").length, 18);
});
test("scene drafts survive switching rows; confirm saves all changes atomically", async () => {
  const p = await panel();
  p.q('[data-select="r3"]').click();
  change(p.q('[data-scene="candle"]'), "id:12");
  p.q('[data-select="r2"]').click();
  change(p.q('[data-scene="party"]'), "none");
  p.q('[data-select="r3"]').click();
  assert.equal(p.q('[data-scene="candle"]').value, "id:12");
  assert.equal(p.q("#confirm").disabled, false);
  p.q("#confirm").click();
  await settle();
  assert.equal(p.el.store.dirty, false);
  const saved = p.calls.find((c) => c.type.endsWith("/save")).settings;
  assert.equal(saved.lights[2].scenes.candle, 12);
  assert.equal(saved.lights[1].scenes.party, null);
  assert.match(p.q("#message").textContent, /сохранены/);
});
test("copy dialog reports matches and preserves draft until global confirm/cancel", async () => {
  const p = await panel();
  p.q('[data-select="r2"]').click();
  p.q("#copy").click();
  p.q("#apply-copy").click();
  assert.match(p.q("#copy-report").textContent, /Изменено назначений: 2/);
  assert.match(p.q("#copy-report").textContent, /Не найдены: 6/);
  assert.equal(p.el.store.record("r3").scenes.candle, 12);
  assert.equal(p.calls.filter((c) => c.type.endsWith("/save")).length, 0);
  p.q("dialog .close").click();
  p.q("#cancel").click();
  assert.equal(p.el.store.dirty, false);
  assert.equal(p.el.store.record("r3").scenes.candle, null);
});
test("profiles can be created, reused and removed within the shared draft", async () => {
  const p = await panel();
  p.q('[data-select="r2"]').click();
  p.q("#save-profile").click();
  p.q("#profile-name").value = "Основной";
  p.q("#create-profile").click();
  assert.equal(p.el.store.profiles.length, 1);
  p.q("#profiles").click();
  p.q("[data-apply-profile]").click();
  assert.match(p.q("#copy-source").value, /profile:/);
  p.q("#apply-copy").click();
  assert.equal(p.el.store.record("r3").scenes.party, 3);
  p.q("dialog .close").click();
  p.q("#profiles").click();
  p.q("[data-delete-profile]").click();
  assert.equal(p.el.store.profiles.length, 0);
});
test("search, empty/offline filters and checkboxes do not change entity order", async () => {
  const p = await panel();
  p.q('[data-filter="empty"]').click();
  assert.deepEqual(
    p.qa("[data-row]").map((e) => e.dataset.row),
    ["r1", "r4", "r5"],
  );
  p.q('[data-filter="all"]').click();
  const input = p.q("#search");
  input.value = "Спальня";
  input.dispatchEvent(new dom.window.Event("input"));
  assert.equal(p.qa("[data-row]").length, 1);
  change(p.q('[data-enabled="r2"]'), false);
  assert.equal(p.el.store.enabledCount, 4);
  input.value = "";
  input.dispatchEvent(new dom.window.Event("input"));
  assert.deepEqual(
    p.qa("[data-row]").map((e) => e.dataset.row),
    ["r1", "r2", "r3", "r4", "r5"],
  );
});
test("external changes and failed saves keep local edits, stale version cannot overwrite", async () => {
  const p = await panel();
  change(p.q("#detail-enabled"), false);
  p.external();
  assert.equal(p.el.store.record("r1").enabled, false);
  assert.equal(p.q("#confirm").disabled, true);
  assert.match(p.q("#message").textContent, /другом окне/);
  p.q("#cancel").click();
  await settle();
  assert.equal(p.el.store.revision, "external");
  assert.equal(p.el.store.dirty, false);
  change(p.q("#detail-enabled"), false);
  p.el._hass.callWS = async () => {
    throw Error("network down");
  };
  p.q("#confirm").click();
  await settle();
  assert.equal(p.el.store.dirty, true);
  assert.equal(p.el.store.record("r1").enabled, false);
  assert.equal(p.q("#confirm").disabled, false);
});
test("state updates preserve editing focus and drafts", async () => {
  const p = await panel();
  p.q('[data-select="r2"]').click();
  const select = p.q('[data-scene="candle"]');
  select.focus();
  const hass = p.el._hass;
  p.el.hass = {
    ...hass,
    states: { ...hass.states, "light.wled2": { state: "off" } },
  };
  assert.equal(p.el.shadowRoot.activeElement.dataset.scene, "candle");
  assert.match(p.q('[data-row="r2"]').textContent, /Выключена/);
});
test("names, preset names and profiles render as text, not executable markup", async () => {
  const data = fixture();
  data.devices[1].name = "<img src=x onerror=alert(1)>";
  data.devices[1].presets[0].name = "<script>bad</script>";
  const p = await panel(data);
  p.q('[data-select="r2"]').click();
  assert.equal(p.qa("img,script").length, 0);
  assert.match(p.q("h3").textContent, /<img/);
  assert.match(p.q('[data-scene="candle"]').textContent, /<script>/);
});
test("preview is read-only for unsaved edits; settings also participate in cancel", async () => {
  const p = await panel();
  p.q("#settings").click();
  change(p.q("#auto-export"), false);
  p.q("dialog .close").click();
  p.q("#preview").click();
  await settle();
  assert.equal(p.q("#export-now").disabled, true);
  assert.equal(p.calls.filter((c) => c.type.endsWith("/save")).length, 0);
  p.q("dialog .close").click();
  p.q("#cancel").click();
  assert.equal(p.el.store.flags.auto_export, true);
  assert.equal(p.el.store.dirty, false);
});
test("Russian shell labels are used even when hass is assigned after connection", async () => {
  const p = await panel();
  assert.equal(p.q("#confirm").textContent, "Подтвердить");
  assert.equal(p.q("#cancel").textContent, "Отмена");
  assert.equal(
    p.q("#search").getAttribute("placeholder"),
    "Поиск по имени, комнате или entity ID",
  );
});
