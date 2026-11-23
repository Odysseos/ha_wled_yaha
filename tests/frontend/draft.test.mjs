import test from "node:test";
import assert from "node:assert/strict";
import {
  DraftStore,
  token,
  untoken,
} from "../../custom_components/wled_yaha/www/editor.js";
import { fixture } from "./fixture.mjs";
const store = () => new DraftStore(fixture());
test("legacy settings stay selected; new discoveries are not silently exported", () => {
  const f = fixture();
  delete f.settings.lights[0].enabled;
  f.settings.lights.pop();
  const s = new DraftStore(f);
  assert.equal(s.enabledCount, 4);
  assert.equal(s.record("r1").enabled, true);
  assert.equal(s.record("r5").enabled, false);
  assert.equal(s.dirty, false);
});
test("one draft spans all rows, profiles and export settings; cancel resets everything", () => {
  const s = store();
  s.setScene("r3", "candle", 12);
  s.setEnabled("r1", false);
  s.addProfile("r2", "Основной");
  s.flags.auto_export = false;
  assert.equal(s.changedDevices, 2);
  assert.equal(s.profiles.length, 1);
  assert.equal(s.dirty, true);
  s.resetAll();
  assert.equal(s.dirty, false);
  assert.equal(s.profiles.length, 0);
  assert.equal(s.record("r2").scenes.party, 7);
});
test("copy by name resolves actual target IDs", () => {
  const s = store();
  const r = s.apply(s.descriptors("r2"), ["r3"]);
  assert.equal(r.changed, 2);
  assert.equal(s.record("r3").scenes.candle, 12);
  assert.equal(s.record("r3").scenes.party, 3);
  assert.equal(s.record("r2").scenes.candle, 1);
});
test("copy by number never guesses a different ID, known empty and unknown catalogs differ", () => {
  const s = store();
  const r = s.apply(s.descriptors("r2"), ["r1", "r3", "r4"], "id");
  assert.equal(r.changed, 2);
  assert.equal(r.pending.length, 2);
  assert.equal(r.unmatched.length, 4);
  assert.equal(s.record("r1").scenes.candle, null);
  assert.equal(s.record("r4").scenes.candle, 1);
});
test("fill-empty preserves existing assignments; replacement uses matching presets only", () => {
  const s = store();
  s.setScene("r3", "candle", 3);
  let r = s.apply(s.descriptors("r2"), ["r3"]);
  assert.equal(s.record("r3").scenes.candle, 3);
  assert.equal(r.conflicts.length, 1);
  r = s.apply(s.descriptors("r2"), ["r3"], "name", true);
  assert.equal(s.record("r3").scenes.candle, 12);
  assert.equal(s.record("r3").scenes.party, 3);
  assert.equal(s.errors().length, 0);
});
test("ambiguous names and duplicates are reported, never silently chosen", () => {
  const s = store();
  s.row("r3").presets.push({ id: 13, name: "СВЕЧА" });
  const r = s.apply(s.descriptors("r2"), ["r3"]);
  assert.equal(r.unmatched.length, 1);
  assert.equal(s.record("r3").scenes.candle, null);
  s.setScene("r2", "alarm", 1);
  assert.equal(s.errors("r2").length, 1);
  assert.throws(() => s.addProfile("r2", "Ошибка"), /duplicate_preset/);
});
test("profiles are independent templates; names are unique; cancel restores deletion", () => {
  const s = store();
  s.addProfile("r2", "Дом");
  assert.throws(() => s.addProfile("r3", "ДОМ"), /duplicate_profile/);
  s.setScene("r2", "candle", null);
  assert.equal(s.profiles[0].scenes.candle.preset_name, "Свеча");
  s.apply(s.profiles[0].scenes, ["r3"]);
  assert.equal(s.record("r3").scenes.candle, 12);
});
test("metadata refresh preserves edits and missing selected rows", () => {
  const s = store();
  s.setScene("r3", "candle", 12);
  const f = fixture();
  f.devices = f.devices.filter((d) => d.registry_id !== "r2");
  s.refresh(f);
  assert.equal(s.record("r3").scenes.candle, 12);
  assert.equal(s.row("r2").missing, true);
  assert.equal(s.changedDevices, 1);
  assert.equal(s.revision, "first");
});
test("token roundtrip preserves typed IDs and names with punctuation", () => {
  for (const value of [null, 1, 250, "Свеча", "id:12", "none", "<script>"])
    assert.equal(untoken(token(value)), value);
});
test("replacement stages a whole row and supports swapping two scene assignments", () => {
  const s = store();
  s.setScene("r3", "candle", 3);
  s.setScene("r3", "party", 12);
  const r = s.apply(s.descriptors("r2"), ["r3"], "name", true);
  assert.equal(r.changed, 2);
  assert.equal(r.conflicts.length, 0);
  assert.equal(s.record("r3").scenes.candle, 12);
  assert.equal(s.record("r3").scenes.party, 3);
});
