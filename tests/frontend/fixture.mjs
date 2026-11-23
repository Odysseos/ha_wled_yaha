import { readFile } from "node:fs/promises";
export const scenes = [
  "alarm",
  "alice",
  "candle",
  "dinner",
  "fantasy",
  "garland",
  "jungle",
  "movie",
  "neon",
  "night",
  "ocean",
  "party",
  "reading",
  "rest",
  "romance",
  "siren",
  "sunrise",
  "sunset",
].map((id, i) => ({
  id,
  ru: [
    "Тревога",
    "Алиса",
    "Свеча",
    "Ужин",
    "Фантазия",
    "Гирлянда",
    "Джунгли",
    "Кино",
    "Неон",
    "Ночь",
    "Океан",
    "Вечеринка",
    "Чтение",
    "Отдых",
    "Романтика",
    "Сирена",
    "Рассвет",
    "Закат",
  ][i],
  en: id,
}));
export function fixture() {
  const devices = [1, 2, 3, 4, 5].map((n) => ({
    registry_id: `r${n}`,
    entity_id: `light.wled${n}`,
    name: `Гирлянда WLED${n}`,
    room: n === 2 ? "Спальня" : n === 3 ? "Гостиная" : null,
    state: n === 2 ? "on" : n === 3 ? "off" : "unavailable",
    presets:
      n === 2
        ? [
            { id: 1, name: "Свеча" },
            { id: 7, name: "Радуга" },
          ]
        : n === 3
          ? [
              { id: 12, name: "Свеча" },
              { id: 3, name: "Радуга" },
            ]
          : [],
    preset_entity: `select.wled${n}_preset`,
    preset_online: n === 2 || n === 3,
    catalog_known: n !== 4,
    warnings: [],
    missing: false,
  }));
  const lights = devices.map((d) => ({
    registry_id: d.registry_id,
    entity_id: d.entity_id,
    enabled: true,
    scenes: d.registry_id === "r2" ? { candle: 1, party: 7 } : {},
    scene_names:
      d.registry_id === "r2" ? { candle: "Свеча", party: "Радуга" } : {},
  }));
  return {
    revision: "first",
    version: "0.2.1",
    devices,
    scenes,
    settings: {
      lights,
      profiles: [],
      auto_export: true,
      connect_yaha: true,
      configured: true,
    },
    export: { status: "ready" },
  };
}
export async function fixtureHtml({ width = null } = {}) {
  const source = await readFile(
    new URL("../../custom_components/wled_yaha/www/editor.js", import.meta.url),
    "utf8",
  );
  return `<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>WLED YaHA · проверка редактора</title><style>html,body{margin:0;height:100%;font-family:Roboto,Arial,sans-serif;background:#fafafa}body{--primary-color:#03a9f4;--primary-background-color:#fafafa;--secondary-background-color:#f0f0f0;--primary-text-color:#212121;--secondary-text-color:#727272;--divider-color:#ddd;--card-background-color:white}#fixture-tools{height:36px;display:flex;align-items:center;gap:12px;padding:0 12px;font-size:12px;background:#eee}#fixture-tools button{cursor:pointer}wled-yaha-panel{height:calc(100% - 36px)!important;${width ? `max-width:${width}px;margin:auto;` : ""}}</style><div id="fixture-tools">Тестовые данные · без подключения к HA<button id="fixture-stale">Изменить в другом окне</button><button id="fixture-offline">Выключить WLED2</button></div><wled-yaha-panel></wled-yaha-panel><script type="module">${source}
let server=${JSON.stringify(fixture()).replaceAll("<", "\\u003c")};let subscriber; const panel=document.querySelector('wled-yaha-panel');const hass={locale:{language:'ru'},states:Object.fromEntries(server.devices.map(d=>[d.entity_id,{state:d.state}])),connection:{subscribeMessage:async(callback)=>{subscriber=callback;return ()=>{subscriber=null}}},callWS:async(msg)=>{if(msg.type.endsWith('/get'))return structuredClone(server);if(msg.type.endsWith('/save')){if(msg.revision!==server.revision)throw {code:'stale_editor'};server.settings=structuredClone(msg.settings);server.revision+='x';return structuredClone(server);}if(msg.type.endsWith('/preview'))return {devices:msg.settings.lights.filter(r=>r.enabled).map(r=>({name:server.devices.find(d=>d.registry_id===r.registry_id).name,light:r.entity_id,scenes:{}})),errors:[],warnings:[]};if(msg.type.endsWith('/export'))return {device_count:5};throw Error('Unknown command');}};panel.hass=hass;document.querySelector('#fixture-stale').onclick=()=>{server.revision+='other';subscriber?.(structuredClone(server));};document.querySelector('#fixture-offline').onclick=()=>{hass.states['light.wled2'].state='unavailable';panel.hass={...hass};};</script></html>`;
}
