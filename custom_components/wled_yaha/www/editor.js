// SPDX-License-Identifier: GPL-3.0-only
// Visual styling and draft interactions follow Odysseos / Entity ID Manager.
const clone = (value) => structuredClone(value);
const equal = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const norm = (value) =>
  String(value || "")
    .normalize("NFKC")
    .toLocaleLowerCase()
    .replaceAll("ё", "е")
    .replace(/[^\p{L}\p{N}]/gu, "");
const escape = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
export const token = (value) =>
  value == null
    ? "none"
    : typeof value === "number"
      ? `id:${value}`
      : `name:${value}`;
export const untoken = (value) =>
  value === "none"
    ? null
    : value.startsWith("id:")
      ? Number(value.slice(3))
      : value.slice(5);

export class DraftStore {
  constructor(snapshot) {
    this.revision = snapshot.revision;
    this.devices = clone(snapshot.devices);
    this.scenes = clone(snapshot.scenes);
    this.export = clone(snapshot.export || {});
    this.baseline = new Map();
    const records = new Map(
      snapshot.settings.lights.map((r) => [r.registry_id, r]),
    );
    for (const device of this.devices)
      this.baseline.set(
        device.registry_id,
        this.normalizeRecord(device, records.get(device.registry_id)),
      );
    this.records = new Map(
      [...this.baseline].map(([id, record]) => [id, clone(record)]),
    );
    this.savedProfiles = clone(snapshot.settings.profiles || []);
    this.profiles = clone(this.savedProfiles);
    this.savedFlags = {
      auto_export: snapshot.settings.auto_export,
      connect_yaha: snapshot.settings.connect_yaha,
    };
    this.flags = clone(this.savedFlags);
  }
  normalizeRecord(device, source) {
    const scenes = Object.fromEntries(
      this.scenes.map((s) => [s.id, source?.scenes?.[s.id] ?? null]),
    );
    return {
      registry_id: device.registry_id,
      entity_id: source?.entity_id || device.entity_id,
      enabled: source ? source.enabled !== false : false,
      scenes,
      scene_names: Object.fromEntries(
        Object.entries(source?.scene_names || {}).filter(
          ([key]) => scenes[key] != null,
        ),
      ),
    };
  }
  row(id) {
    return this.devices.find((d) => d.registry_id === id);
  }
  record(id) {
    return this.records.get(id);
  }
  changed(id) {
    return !equal(this.records.get(id), this.baseline.get(id));
  }
  get changedDevices() {
    return this.devices.filter((d) => this.changed(d.registry_id)).length;
  }
  get dirty() {
    return Boolean(
      this.changedDevices ||
        !equal(this.profiles, this.savedProfiles) ||
        !equal(this.flags, this.savedFlags),
    );
  }
  get enabledCount() {
    return [...this.records.values()].filter((r) => r.enabled).length;
  }
  setEnabled(id, enabled) {
    this.record(id).enabled = enabled;
  }
  setScene(id, scene, value) {
    const record = this.record(id),
      device = this.row(id);
    record.scenes[scene] = value;
    const name =
      typeof value === "number"
        ? device.presets.find((p) => p.id === value)?.name
        : value;
    if (name) record.scene_names[scene] = name;
    else delete record.scene_names[scene];
  }
  presetLabel(id, value) {
    if (value == null) return "—";
    const device = this.row(id),
      record = this.record(id);
    const preset = device.presets.find((p) =>
      typeof value === "number" ? p.id === value : p.name === value,
    );
    const cached = Object.entries(record.scenes).find(
      ([, v]) => v === value,
    )?.[0];
    const name =
      preset?.name ||
      record.scene_names[cached] ||
      (typeof value === "string" ? value : "");
    return typeof value === "number"
      ? `#${value}${name ? ` · ${name}` : ""}`
      : name;
  }
  clear(id) {
    for (const scene of this.scenes) this.setScene(id, scene.id, null);
  }
  reset(id) {
    this.records.set(id, clone(this.baseline.get(id)));
  }
  resetAll() {
    this.records = new Map([...this.baseline].map(([id, r]) => [id, clone(r)]));
    this.profiles = clone(this.savedProfiles);
    this.flags = clone(this.savedFlags);
  }
  descriptors(id) {
    const record = this.record(id),
      device = this.row(id),
      result = {};
    for (const [scene, choice] of Object.entries(record.scenes)) {
      if (choice == null) continue;
      const preset = device.presets.find((p) =>
        typeof choice === "number" ? p.id === choice : p.name === choice,
      );
      result[scene] = {
        preset_id: typeof choice === "number" ? choice : (preset?.id ?? null),
        preset_name:
          preset?.name ||
          record.scene_names[scene] ||
          (typeof choice === "string" ? choice : null),
      };
    }
    return result;
  }
  addProfile(id, name) {
    name = name.trim();
    if (!name || name.length > 100) throw new Error("profile_name");
    if (this.profiles.some((p) => norm(p.name) === norm(name)))
      throw new Error("duplicate_profile");
    if (this.errors(id).length) throw new Error("duplicate_preset");
    const profile = {
      id:
        globalThis.crypto?.randomUUID?.() ||
        `p-${Date.now()}-${Math.random().toString(36).slice(2)}`,
      name,
      scenes: this.descriptors(id),
    };
    this.profiles.push(profile);
    return profile;
  }
  apply(descriptors, targets, mode = "name", overwrite = false) {
    const report = {
      changed: 0,
      matched: 0,
      unmatched: [],
      conflicts: [],
      pending: [],
      targets: [],
    };
    for (const id of targets) {
      const device = this.row(id),
        record = this.record(id);
      if (!device || device.missing) continue;
      const candidates = new Map();
      for (const [scene, source] of Object.entries(descriptors)) {
        if (
          !source ||
          !Object.hasOwn(record.scenes, scene) ||
          (!overwrite && record.scenes[scene] != null)
        )
          continue;
        let value,
          name,
          pending = false;
        if (mode === "id" && source.preset_id != null) {
          const match = device.presets.find((p) => p.id === source.preset_id);
          if (match) {
            value = match.id;
            name = match.name;
          } else if (!device.catalog_known) {
            value = source.preset_id;
            name = source.preset_name;
            pending = true;
          }
        } else if (source.preset_name) {
          const matches = device.presets.filter(
            (p) => norm(p.name) === norm(source.preset_name),
          );
          if (matches.length === 1) {
            const match = matches[0];
            value = match.id ?? match.name;
            name = match.name;
          }
        }
        if (value == null) {
          report.unmatched.push({
            id,
            scene,
            preset: source.preset_name || source.preset_id,
          });
          continue;
        }
        candidates.set(scene, { value, name, pending });
      }
      // Validate the whole proposed row, so replacing two swapped presets works.
      let rejected;
      do {
        const used = new Map();
        rejected = new Set();
        for (const [scene, oldValue] of Object.entries(record.scenes)) {
          const proposal = candidates.get(scene),
            value = proposal ? proposal.value : oldValue;
          if (value == null) continue;
          const name = proposal ? proposal.name : record.scene_names[scene];
          const identity = name ? `name:${norm(name)}` : token(value);
          const group = used.get(identity) || [];
          group.push(scene);
          used.set(identity, group);
        }
        for (const group of used.values())
          if (group.length > 1)
            for (const scene of group)
              if (candidates.has(scene)) rejected.add(scene);
        for (const scene of rejected) {
          candidates.delete(scene);
          report.conflicts.push({ id, scene });
        }
      } while (rejected.size);
      let changed = 0;
      for (const [scene, { value, name, pending }] of candidates) {
        report.matched++;
        if (
          record.scenes[scene] !== value ||
          (name && record.scene_names[scene] !== name)
        ) {
          record.scenes[scene] = value;
          if (name) record.scene_names[scene] = name;
          else delete record.scene_names[scene];
          changed++;
          report.changed++;
        }
        if (pending) report.pending.push({ id, scene });
      }
      report.targets.push({ id, changed });
    }
    return report;
  }
  errors(id = null) {
    const errors = [];
    for (const device of this.devices) {
      if (id && id !== device.registry_id) continue;
      const record = this.record(device.registry_id),
        used = new Set();
      for (const [scene, value] of Object.entries(record.scenes)) {
        if (value == null) continue;
        const identity = record.scene_names[scene]
          ? `name:${norm(record.scene_names[scene])}`
          : token(value);
        if (used.has(identity))
          errors.push({
            id: device.registry_id,
            scene,
            code: "duplicate_preset",
          });
        used.add(identity);
      }
    }
    return errors;
  }
  toSettings() {
    return {
      lights: this.devices.map((d) => clone(this.record(d.registry_id))),
      profiles: clone(this.profiles),
      ...this.flags,
      configured: true,
    };
  }
  refresh(snapshot) {
    this.export = clone(snapshot.export || {});
    const old = new Map(this.devices.map((d) => [d.registry_id, d]));
    this.devices = clone(snapshot.devices);
    for (const device of this.devices) {
      if (!this.records.has(device.registry_id)) {
        const record = this.normalizeRecord(device, null);
        this.records.set(device.registry_id, record);
        this.baseline.set(device.registry_id, clone(record));
      }
      old.delete(device.registry_id);
    }
    // Keep a disappeared object's draft visible until the user explicitly removes it.
    for (const device of old.values())
      this.devices.push({ ...device, state: "missing", missing: true });
  }
}

const WORDS = {
  ru: {
    export_pending: "Экспорт ожидает обновления",
    export_ready: "Экспорт сохранён",
    export_error: "Ошибка экспорта · открой «Просмотр и экспорт»",
    export_reload_required: "Экспорт сохранён · перечитай YaHA",
    export_warnings: "предупреждений",
    title: "WLED YaHA",
    subtitle: "Гирлянды и сцены Алисы",
    search: "Поиск по имени, комнате или entity ID",
    all: "Все",
    enabled: "В YaHA",
    empty: "Без пресетов",
    offline: "Недоступные",
    changed: "Изменённые",
    presets: "Пресеты",
    none: "Нет пресетов",
    unknownCatalog: "Пресеты не загружены",
    scenes: "Сцены Алисы",
    exportDevice: "Показывать в YaHA",
    clear: "Очистить сцены",
    reset: "Сбросить изменения",
    copy: "Копировать / применить профиль",
    saveProfile: "Сохранить как профиль",
    profiles: "Профили",
    settings: "Параметры экспорта",
    refresh: "Обновить",
    preview: "Просмотр и экспорт",
    confirm: "Подтвердить",
    cancel: "Отмена",
    close: "Закрыть",
    choose: "Выберите гирлянду слева",
    noDevices: "Светильники штатной интеграции WLED ещё не найдены.",
    noResults: "Ничего не найдено",
    saved: "Настройки сохранены.",
    failed: "Не удалось сохранить. Черновик остаётся в открытом редакторе.",
    stale:
      "Настройки изменились в другом окне. Черновик сохранён. Нажмите «Отмена», чтобы загрузить актуальные данные.",
    duplicate_preset:
      "Один пресет нельзя назначить нескольким сценам гирлянды.",
    profile_name: "Введите имя профиля (до 100 символов).",
    duplicate_profile: "Профиль с таким именем уже существует.",
    auto: "Автоматически обновлять экспорт после сохранения",
    connect: "Автоматически подключить экспорт к YaHA",
    connectHint:
      "Будет добавлен отдельный пакет YaHA; перед изменением существующего YAML сохраняется резервная копия.",
    saveHint: "«Подтвердить» сохраняет общий черновик всех гирлянд и профилей.",
    noPresetHint:
      "Пресеты не найдены или каталог ещё не загружен. Гирлянду можно включить в YaHA без сцен. Назначения доступны, когда появится каталог или сохранённые соответствия.",
    cached: "Последний известный каталог",
    state_on: "Включена",
    state_off: "Выключена",
    state_unavailable: "Недоступна",
    state_unknown: "Состояние неизвестно",
    state_not_loaded: "Ещё не загружена",
    state_missing: "Удалена или отключена в HA",
    changedCount: "Изменено гирлянд",
    selectedCount: "В YaHA",
    name: "Имя профиля",
    create: "Создать профиль",
    delete: "Удалить",
    noProfiles:
      "Профилей пока нет. Сохраните назначения выбранной гирлянды как профиль.",
    source: "Источник",
    targets: "Гирлянды-получатели",
    byName: "По имени пресета",
    byId: "По номеру пресета",
    copyMode: "Сопоставление",
    copyHint:
      "По имени: только однозначные совпадения. По номеру: одинаковые номера на контроллерах; для неизвестного каталога назначение будет проверено позже.",
    emptyOnly: "Заполнить только пустые сцены",
    replace: "Заменить совпавшие назначения",
    applyDraft: "Внести в черновик",
    copyReport: "Изменено назначений",
    unmatched: "Не найдены",
    conflicts: "Конфликты",
    pending: "Ждут каталога",
    profileHint:
      "Профиль — шаблон для копирования. После применения назначения каждой гирлянды остаются независимыми.",
    exportNow: "Экспортировать сохранённые настройки",
    exportSaved: "Экспорт сохранён. Обновите устройства в «Доме с Алисой».",
    previewHint:
      "Показан текущий черновик. Для экспорта сначала подтвердите изменения.",
    savedPreview: "Показаны сохранённые настройки.",
    warnings: "Предупреждения",
    errors: "Ошибки",
    loadError: "Не удалось загрузить редактор",
    retry: "Повторить",
    profileChanged: "Изменены профили",
    globalChanged: "Изменены параметры экспорта",
  },
  en: {
    export_pending: "Export pending",
    export_ready: "Export saved",
    export_error: "Export failed · open Preview and export",
    export_reload_required: "Export saved · reload YaHA",
    export_warnings: "warnings",
    title: "WLED YaHA",
    subtitle: "Garlands and Yandex scenes",
    search: "Search name, room or entity ID",
    all: "All",
    enabled: "In YaHA",
    empty: "No presets",
    offline: "Unavailable",
    changed: "Changed",
    presets: "Presets",
    none: "No presets",
    unknownCatalog: "Presets not loaded",
    scenes: "Yandex scenes",
    exportDevice: "Expose in YaHA",
    clear: "Clear scenes",
    reset: "Reset changes",
    copy: "Copy / apply profile",
    saveProfile: "Save as profile",
    profiles: "Profiles",
    settings: "Export settings",
    refresh: "Refresh",
    preview: "Preview and export",
    confirm: "Confirm",
    cancel: "Cancel",
    close: "Close",
    choose: "Select a garland on the left",
    noDevices: "No native WLED lights were found yet.",
    noResults: "No matches",
    saved: "Settings saved.",
    failed: "Could not save. Your draft remains in the open editor.",
    stale:
      "Settings changed in another window. Your draft is retained. Cancel to load the latest settings.",
    duplicate_preset: "Assign a preset to only one scene per garland.",
    profile_name: "Enter a profile name (up to 100 characters).",
    duplicate_profile: "A profile with this name already exists.",
    auto: "Update export automatically after saving",
    connect: "Connect the export to YaHA automatically",
    connectHint:
      "Adds a separate YaHA package and backs up any existing YAML before editing it.",
    saveHint: "Confirm saves the shared draft of all garlands and profiles.",
    noPresetHint:
      "No presets were found or the catalog is not loaded yet. Expose the garland without scenes; mappings become available with a catalog or saved assignments.",
    cached: "Last known catalog",
    state_on: "On",
    state_off: "Off",
    state_unavailable: "Unavailable",
    state_unknown: "State unknown",
    state_not_loaded: "Not loaded yet",
    state_missing: "Removed or disabled in HA",
    changedCount: "Changed garlands",
    selectedCount: "In YaHA",
    name: "Profile name",
    create: "Create profile",
    delete: "Delete",
    noProfiles: "No profiles yet. Save a garland's mappings as a profile.",
    source: "Source",
    targets: "Target garlands",
    byName: "By preset name",
    byId: "By preset number",
    copyMode: "Match presets",
    copyHint:
      "Names: unambiguous matches only. Numbers: matching IDs on controllers; assignments without a catalog will be checked later.",
    emptyOnly: "Fill empty scenes only",
    replace: "Replace matching assignments",
    applyDraft: "Add to draft",
    copyReport: "Assignments changed",
    unmatched: "Not found",
    conflicts: "Conflicts",
    pending: "Awaiting catalog",
    profileHint:
      "A profile is a copying template. Applied mappings remain independent per garland.",
    exportNow: "Export saved settings",
    exportSaved: "Export saved. Refresh devices in Yandex Smart Home.",
    previewHint: "This is the current draft. Confirm changes before exporting.",
    savedPreview: "These are the saved settings.",
    warnings: "Warnings",
    errors: "Errors",
    loadError: "Could not load the editor",
    retry: "Retry",
    profileChanged: "Profiles changed",
    globalChanged: "Export settings changed",
  },
};

const CSS = `
:host{display:block;height:100%;overflow:hidden;color:var(--primary-text-color,#222);background:var(--primary-background-color,#fafafa);font-family:var(--paper-font-body1_-_font-family,Roboto,sans-serif)}
*{box-sizing:border-box}[hidden]{display:none!important}header{display:flex;align-items:center;gap:16px;height:64px;padding:0 20px;background:var(--app-header-background-color,var(--primary-color,#009fbd));color:var(--app-header-text-color,white)}h1{font-size:20px;margin:0}h2{font-size:22px;margin:0}h3{font-size:16px;margin:0 0 8px}p{line-height:1.5}a{color:var(--primary-color,#009fbd)}
button{font:inherit;padding:10px 16px;border:0;border-radius:24px;cursor:pointer;color:var(--primary-color,#009fbd);background:transparent}button.primary{background:var(--primary-color,#009fbd);color:var(--text-primary-color,white)}button:disabled{opacity:.45;cursor:default}button:focus-visible,input:focus-visible,select:focus-visible,a:focus-visible{outline:2px solid var(--primary-color,#009fbd);outline-offset:2px}
main{height:calc(100% - 64px);max-width:1540px;margin:auto;padding:20px;display:grid;grid-template-rows:auto auto minmax(0,1fr) auto;gap:12px}.heading,.tools,.footer,.filters{display:flex;align-items:center;gap:8px;flex-wrap:wrap}.heading h2{margin-inline-end:auto}.heading button{padding:8px 12px}.hint,.count,.secondary{font-size:13px;color:var(--secondary-text-color,#666)}.hint{margin:4px 0}.message{padding:8px 12px;border-radius:8px;background:var(--secondary-background-color,#eee);white-space:pre-wrap;font-size:14px}.message:empty{display:none}.error{color:var(--error-color,#b00020)}
.filters button{font-size:13px;padding:6px 11px}.filters button[aria-pressed=true]{background:var(--secondary-background-color,#eee);box-shadow:inset 0 0 0 1px var(--primary-color,#009fbd)}
.editor-layout{display:grid;grid-template-columns:minmax(280px,.9fr) minmax(380px,1.1fr);gap:18px;min-height:0}.master,.detail{min-height:0;min-width:0;background:var(--card-background-color,white);border:1px solid var(--divider-color,#ddd);border-radius:8px;overflow:hidden}.master{display:flex;flex-direction:column}.search{flex-shrink:0;margin:0;border:0;border-bottom:1px solid var(--divider-color,#ddd);border-radius:0!important;min-height:44px}.list{overflow:auto;min-height:0;flex:1;overscroll-behavior:contain}.device-row{display:grid;grid-template-columns:36px minmax(0,1fr);border-bottom:1px solid var(--divider-color,#ddd);min-height:85px}.device-row.active{background:var(--secondary-background-color,#eee);box-shadow:inset 3px 0 0 var(--primary-color,#009fbd)}.device-row input{width:18px;height:18px;padding:0;margin:16px 8px;accent-color:var(--primary-color,#009fbd)}.device-select{width:100%;text-align:start;border-radius:0;padding:9px 10px 9px 0;color:var(--primary-text-color,#222);min-width:0}.device-title{display:flex;align-items:center;gap:7px;flex-wrap:wrap}.device-title strong{font-weight:500}.device-meta{font-size:12px;color:var(--secondary-text-color,#666);margin-top:3px;overflow-wrap:anywhere}.device-id{font-family:monospace;font-size:11px;color:var(--secondary-text-color,#666);overflow-wrap:anywhere;margin-top:3px}.badges{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}.chip{display:inline-block;padding:2px 7px;border-radius:4px;font-size:12px;background:var(--secondary-background-color,#eee);color:var(--primary-text-color,#222);overflow-wrap:anywhere}.chip.added{background:color-mix(in srgb,var(--success-color,#0f9d58) 22%,var(--card-background-color,white))}.chip.removed{background:color-mix(in srgb,var(--error-color,#db4437) 20%,var(--card-background-color,white));text-decoration:line-through}.chip.empty{background:color-mix(in srgb,#ffc107 18%,var(--card-background-color,white))}.state{display:inline-flex;align-items:center;gap:5px;font-size:12px;white-space:nowrap}.dot{width:8px;height:8px;border-radius:50%;background:var(--secondary-text-color,#777)}.state.on .dot{background:var(--success-color,#188351)}.state.unavailable .dot,.state.missing .dot{background:var(--error-color,#db4437)}.state.unknown .dot,.state.not_loaded .dot{background:#c58a10}
.detail{display:flex;flex-direction:column}.detail-head{padding:14px 16px 8px;flex-shrink:0}.detail-title{display:flex;gap:12px;align-items:flex-start;justify-content:space-between}.detail-title h3{font-size:19px}.entity-link{display:inline-flex;align-items:center;padding:6px;color:var(--secondary-text-color,#666);text-decoration:none}.detail-head .tools{margin-top:8px;gap:3px}.detail-head .tools button{padding:7px 10px;font-size:13px}.toggle{display:flex;align-items:center;gap:9px;font-size:14px;margin:12px 0 4px}.toggle input{width:18px;height:18px;padding:0;margin:0;accent-color:var(--primary-color,#009fbd)}.detail-scroll{overflow:auto;min-height:0;flex:1;padding:4px 16px 18px;overscroll-behavior:contain}.empty-state{padding:22px 18px;color:var(--secondary-text-color,#666);line-height:1.5}.scene-row{display:grid;grid-template-columns:minmax(95px,.7fr) minmax(0,1.3fr);gap:8px 12px;padding:9px 0;border-bottom:1px solid var(--divider-color,#ddd);align-items:center}.scene-row label{font-size:14px}.scene-row select{min-width:0;width:100%;font-size:14px;padding:11px 9px}.scene-diff{grid-column:2;display:flex;gap:5px;flex-wrap:wrap}.scene-row.changed label{font-weight:500}.detail-info{padding:10px 12px;background:var(--secondary-background-color,#eee);border-radius:8px;line-height:1.5;font-size:13px;margin:8px 0}.profile-line{display:flex;gap:8px;align-items:center;margin:12px 0}.profile-line select{flex:1;min-width:0}.footer{min-height:58px;justify-content:space-between}.footer .actions{display:flex;gap:8px}.footer .count{margin:0}.footer small{display:block;margin-top:4px;max-width:700px}.empty-list{padding:20px;text-align:center;color:var(--secondary-text-color,#666)}
input:not([type=checkbox]):not([type=radio]),select{box-sizing:border-box;font:inherit;color:var(--primary-text-color,#222);background:var(--secondary-background-color,#f5f5f5);border:0;border-bottom:1px solid var(--secondary-text-color,#777);border-radius:4px 4px 0 0;padding:13px 12px;width:100%}label{color:var(--primary-text-color,#222)}
dialog{box-sizing:border-box;width:min(650px,calc(100vw - 32px));max-height:calc(100dvh - 32px);overflow:auto;padding:24px;border:0;border-radius:28px;color:var(--primary-text-color,#222);background:var(--card-background-color,white)}dialog::backdrop{background:#0006}.dialog-heading{display:flex;align-items:center;gap:10px;margin-bottom:16px}.dialog-heading h2{font-size:21px}.dialog-heading .close{font-size:28px;padding:0 8px}.dialog-body label.field{display:block;font-size:13px;color:var(--secondary-text-color,#666);margin:15px 0 5px}.dialog-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:20px}.targets{max-height:220px;overflow:auto;border:1px solid var(--divider-color,#ddd);border-radius:8px;padding:4px 10px}.targets label{display:flex;gap:8px;padding:8px 0;font-size:14px}.targets input{width:18px;height:18px;flex-shrink:0;accent-color:var(--primary-color,#009fbd)}.profile-row{display:flex;align-items:center;gap:8px;padding:10px 0;border-bottom:1px solid var(--divider-color,#ddd)}.profile-row span{flex:1;overflow-wrap:anywhere}.preview-row{padding:10px 0;border-bottom:1px solid var(--divider-color,#ddd)}.preview-row code{font-size:12px;color:var(--secondary-text-color,#666)}.preview-row ul{padding-left:20px;margin:5px 0;font-size:13px}.report{font-size:13px;line-height:1.6;white-space:pre-wrap;margin-top:12px}.report:empty{display:none}
@media(max-width:850px){main{padding:12px;gap:8px}.editor-layout{grid-template-columns:minmax(235px,.9fr) minmax(300px,1.1fr);gap:10px}.heading h2{font-size:20px}.heading button{font-size:12px;padding:6px 9px}}@media(max-width:640px){header{height:56px;padding:0 12px}main{height:calc(100% - 56px);padding:8px;grid-template-rows:auto auto minmax(0,1fr) auto}.editor-layout{grid-template-columns:1fr;grid-template-rows:minmax(120px,33%) minmax(180px,1fr);gap:8px}.heading .secondary-actions{display:flex;flex-wrap:wrap}.heading h2{font-size:18px;width:100%}.filters{gap:3px}.filters button{padding:5px 8px}.detail-head{padding:10px 12px 4px}.detail-scroll{padding:0 12px 10px}.detail-head .tools button{padding:5px 8px}.device-row{min-height:72px}.scene-row{grid-template-columns:90px minmax(0,1fr)}.footer small{display:none}.footer .actions button{padding:10px 12px}.footer{min-height:48px}.footer .count{font-size:12px}dialog{padding:18px;border-radius:20px}}
`;

const BaseElement = globalThis.HTMLElement || class {};
export class WledYahaPanel extends BaseElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this.filter = "all";
    this.query = "";
    this.selected = null;
    this.loading = false;
    this.saving = false;
    this.stale = false;
    this.message = "";
  }
  get words() {
    return WORDS[this._hass?.locale?.language?.startsWith("ru") ? "ru" : "en"];
  }
  get russian() {
    return this.words === WORDS.ru;
  }
  connectedCallback() {
    if (!this.initialized) this.renderShell();
    if (this._hass) this.load(Boolean(this.store));
  }
  disconnectedCallback() {
    this.unsubscribe?.();
    this.unsubscribe = null;
    clearTimeout(this.refreshTimer);
  }
  set hass(value) {
    this._hass = value;
    if (!this.isConnected) return;
    if (
      !this.initialized ||
      this.renderedLanguage !== (this.russian ? "ru" : "en")
    )
      this.renderShell();
    if (!this.store && !this.loading) this.load();
    else if (this.store) this.updateLive();
  }
  renderShell() {
    this.initialized = true;
    this.renderedLanguage = this.russian ? "ru" : "en";
    const w = this.words;
    this.shadowRoot.innerHTML = `<style>${CSS}</style><header><ha-menu-button></ha-menu-button><h1>${w.title}</h1></header><main><div class="heading"><h2>${w.subtitle}</h2><div class="secondary-actions"><button id="refresh">${w.refresh}</button><button id="profiles">${w.profiles}</button><button id="settings">${w.settings}</button><button id="preview">${w.preview}</button></div></div><div><div class="filters" role="group" aria-label="${w.all}">${["all", "enabled", "empty", "offline", "changed"].map((key) => `<button data-filter="${key}" aria-pressed="${this.filter === key}">${w[key]}</button>`).join("")}</div><div id="message" class="message" role="status"></div></div><div class="editor-layout" id="editor-layout"><section class="master"><input id="search" class="search" type="search" placeholder="${w.search}" aria-label="${w.search}"><div class="list" id="device-list" role="list" aria-label="${w.subtitle}"></div></section><section class="detail" id="detail"></section></div><footer class="footer"><div><p id="count" class="count"></p><div id="export-status" class="count" role="status"></div><small class="hint">${w.saveHint}</small></div><div class="actions"><button id="cancel">${w.cancel}</button><button id="confirm" class="primary">${w.confirm}</button></div></footer></main><div id="dialogs"></div>`;
    const menu = this.q("ha-menu-button");
    if (menu) menu.hass = this._hass;
    this.q("#search").value = this.query;
    this.q("#search").oninput = (event) => {
      this.query = event.target.value;
      this.paintList();
    };
    for (const button of this.shadowRoot.querySelectorAll("[data-filter]"))
      button.onclick = () => {
        this.filter = button.dataset.filter;
        this.paintList();
      };
    this.q("#refresh").onclick = () => this.load(true);
    this.q("#profiles").onclick = () => this.showProfiles();
    this.q("#settings").onclick = () => this.showSettings();
    this.q("#preview").onclick = () => this.showPreview();
    this.q("#cancel").onclick = () => this.cancel();
    this.q("#confirm").onclick = () => this.save();
    this.paint();
  }
  q(selector) {
    return this.shadowRoot.querySelector(selector);
  }
  async load(refresh = false) {
    if (this.loading || this.saving) return;
    this.loading = true;
    try {
      const snapshot = await this._hass.callWS({
        type: "wled_yaha/editor/get",
      });
      if (this.store?.dirty && refresh) {
        if (snapshot.revision !== this.store.revision) this.stale = true;
        this.store.refresh(snapshot);
      } else {
        this.store = new DraftStore(snapshot);
        this.stale = false;
      }
      this.selected = this.store.row(this.selected)
        ? this.selected
        : this.store.devices[0]?.registry_id || null;
      this.message = this.stale ? this.words.stale : "";
      this.paint();
      if (
        !this.unsubscribe &&
        this.isConnected &&
        this._hass.connection?.subscribeMessage
      ) {
        const unsubscribe = await this._hass.connection.subscribeMessage(
          (data) => this.receive(data),
          { type: "wled_yaha/editor/subscribe" },
        );
        if (this.isConnected) this.unsubscribe = unsubscribe;
        else unsubscribe();
      }
    } catch (error) {
      this.message = `${this.words.loadError}: ${error.message || error.code || error}`;
      this.paint();
    } finally {
      this.loading = false;
    }
  }
  receive(snapshot) {
    if (!this.store || this.saving) return;
    if (snapshot.revision !== this.store.revision) {
      if (this.store.dirty) {
        this.stale = true;
        this.message = this.words.stale;
        this.store.refresh(snapshot);
      } else this.store = new DraftStore(snapshot);
    } else this.store.refresh(snapshot);
    this.paint();
  }
  updateLive() {
    if (!this.store) return;
    let changed = false,
      catalogChanged = false;
    for (const device of this.store.devices) {
      if (device.missing) continue;
      if (this._hass.states) {
        const state =
          this._hass.states[device.entity_id]?.state || "not_loaded";
        if (device.state !== state) {
          if (
            ["on", "off"].includes(device.state) !==
            ["on", "off"].includes(state)
          )
            catalogChanged = true;
          device.state = state;
          changed = true;
        }
        const preset = this._hass.states[device.preset_entity];
        if (preset) {
          const signature = JSON.stringify([
            ["unavailable", "unknown"].includes(preset.state),
            preset.attributes?.options,
          ]);
          if (device.liveSignature && device.liveSignature !== signature)
            catalogChanged = true;
          device.liveSignature = signature;
        }
      }
    }
    if (changed) this.paint();
    if (catalogChanged) {
      clearTimeout(this.refreshTimer);
      this.refreshTimer = setTimeout(() => this.load(true), 500);
    }
  }
  status(device) {
    return `<span class="state ${escape(device.state)}"><span class="dot"></span>${escape(this.words[`state_${device.state}`] || this.words.state_unknown)}</span>`;
  }
  sceneName(scene) {
    return scene[this.russian ? "ru" : "en"];
  }
  paint() {
    const detail = this.q(".detail-scroll"),
      list = this.q("#device-list"),
      active = this.shadowRoot.activeElement;
    const scroll = detail?.scrollTop || 0,
      listScroll = list?.scrollTop || 0;
    const sameRow = this.renderedSelection === this.selected;
    const focus = active?.dataset?.scene
      ? `[data-scene="${active.dataset.scene}"]`
      : active?.dataset?.select
        ? `[data-select="${active.dataset.select}"]`
        : active?.dataset?.enabled
          ? `[data-enabled="${active.dataset.enabled}"]`
          : active?.id === "detail-enabled"
            ? "#detail-enabled"
            : null;
    this.paintList();
    this.paintDetail();
    this.paintFooter();
    if (this.q("#device-list")) this.q("#device-list").scrollTop = listScroll;
    if (sameRow && this.q(".detail-scroll"))
      this.q(".detail-scroll").scrollTop = scroll;
    if (
      focus &&
      (sameRow || active?.dataset?.select || active?.dataset?.enabled)
    )
      this.q(focus)?.focus({ preventScroll: true });
    this.renderedSelection = this.selected;
  }
  paintList() {
    const w = this.words,
      list = this.q("#device-list");
    if (!list) return;
    for (const button of this.shadowRoot.querySelectorAll("[data-filter]"))
      button.setAttribute(
        "aria-pressed",
        String(button.dataset.filter === this.filter),
      );
    if (!this.store) {
      list.innerHTML = `<div class="empty-list">${w.loadError}</div>`;
      return;
    }
    const query = norm(this.query);
    const devices = this.store.devices.filter((d) => {
      const r = this.store.record(d.registry_id);
      if (
        query &&
        !norm(`${d.name} ${d.room || ""} ${d.entity_id}`).includes(query)
      )
        return false;
      return (
        this.filter === "all" ||
        (this.filter === "enabled" && r.enabled) ||
        (this.filter === "empty" && !d.presets.length) ||
        (this.filter === "offline" && !["on", "off"].includes(d.state)) ||
        (this.filter === "changed" && this.store.changed(d.registry_id))
      );
    });
    list.innerHTML =
      devices
        .map((d) => {
          const r = this.store.record(d.registry_id),
            changed = this.store.changed(d.registry_id);
          return `<div role="listitem" class="device-row ${d.registry_id === this.selected ? "active" : ""}" data-row="${escape(d.registry_id)}"><input type="checkbox" data-enabled="${escape(d.registry_id)}" ${r.enabled ? "checked" : ""} aria-label="${w.exportDevice}: ${escape(d.name)}" ${this.saving ? "disabled" : ""}><button class="device-select" data-select="${escape(d.registry_id)}" aria-controls="detail" aria-pressed="${d.registry_id === this.selected}"><span class="device-title"><strong>${escape(d.name)}</strong>${this.status(d)}</span><div class="device-meta">${escape(d.room || "—")}</div><div class="device-id">${escape(d.entity_id)}</div><div class="badges"><span class="chip ${d.presets.length ? "" : "empty"}">${d.presets.length ? `${w.presets}: ${d.presets.length}` : d.catalog_known ? w.none : w.unknownCatalog}</span>${changed ? `<span class="chip added">${w.changed}</span>` : ""}</div></button></div>`;
        })
        .join("") ||
      `<div class="empty-list">${this.store.devices.length ? w.noResults : w.noDevices}</div>`;
    for (const input of list.querySelectorAll("[data-enabled]"))
      input.onchange = () => {
        if (this.saving) return;
        this.store.setEnabled(input.dataset.enabled, input.checked);
        this.paint();
      };
    for (const button of list.querySelectorAll("[data-select]"))
      button.onclick = () => {
        this.selected = button.dataset.select;
        this.paint();
      };
  }
  paintDetail() {
    const host = this.q("#detail"),
      w = this.words;
    if (!host) return;
    const d = this.store?.row(this.selected);
    if (!d) {
      host.innerHTML = `<div class="empty-state">${w.choose}</div>`;
      return;
    }
    const r = this.store.record(d.registry_id),
      saved = this.store.baseline.get(d.registry_id);
    const available =
      d.presets.length || Object.values(r.scenes).some((v) => v != null);
    const mappingRows = available
      ? this.store.scenes
          .map((scene) => {
            const current = r.scenes[scene.id],
              previous = saved.scenes[scene.id],
              changed = current !== previous;
            const choices = [
              { value: "none", label: "—" },
              ...d.presets.map((p) => ({
                value: token(p.id ?? p.name),
                label: p.id == null ? p.name : `#${p.id} · ${p.name}`,
              })),
            ];
            if (
              current != null &&
              !choices.some((p) => p.value === token(current))
            )
              choices.push({
                value: token(current),
                label: `⚠ ${this.store.presetLabel(d.registry_id, current)}`,
              });
            return `<div class="scene-row ${changed ? "changed" : ""}"><label for="scene-${scene.id}">${escape(this.sceneName(scene))}</label><select id="scene-${scene.id}" data-scene="${scene.id}" ${this.saving ? "disabled" : ""}>${choices.map((p) => `<option value="${escape(p.value)}" ${p.value === token(current) ? "selected" : ""}>${escape(p.label)}</option>`).join("")}</select><div class="scene-diff">${changed ? `${previous != null ? `<span class="chip removed">${escape(this.store.presetLabel(d.registry_id, previous))}</span>` : ""}${current != null ? `<span class="chip added">${escape(this.store.presetLabel(d.registry_id, current))}</span>` : ""}` : current != null ? `<span class="chip">${escape(this.store.presetLabel(d.registry_id, current))}</span>` : ""}</div></div>`;
          })
          .join("")
      : `<div class="empty-state" data-empty-presets>${w.noPresetHint}</div>`;
    host.innerHTML = `<div class="detail-head"><div class="detail-title"><div><h3>${escape(d.name)}</h3><div class="secondary">${escape(d.room || "—")} · ${this.status(d)}</div><div class="device-id">${escape(d.entity_id)}</div></div><a class="entity-link" href="/config/entities/entity/${encodeURIComponent(d.registry_id)}" target="_blank" rel="noopener" aria-label="Home Assistant">↗</a></div><label class="toggle"><input id="detail-enabled" type="checkbox" ${r.enabled ? "checked" : ""} ${this.saving ? "disabled" : ""}>${w.exportDevice}</label><div class="tools"><button id="copy">${w.copy}</button><button id="save-profile">${w.saveProfile}</button><button id="clear">${w.clear}</button><button id="reset">${w.reset}</button></div></div><div class="detail-scroll">${!d.preset_online && d.presets.length ? `<div class="detail-info">${w.cached}</div>` : ""}<p id="row-error" class="error" role="status"></p>${mappingRows}</div>`;
    this.q("#detail-enabled").onchange = (e) => {
      if (this.saving) return;
      this.store.setEnabled(d.registry_id, e.target.checked);
      this.paint();
    };
    this.q("#copy").onclick = () => this.showCopy();
    this.q("#save-profile").onclick = () => this.showSaveProfile();
    this.q("#clear").onclick = () => {
      if (this.saving) return;
      this.store.clear(d.registry_id);
      this.paint();
    };
    this.q("#reset").onclick = () => {
      if (this.saving) return;
      this.store.reset(d.registry_id);
      this.paint();
    };
    for (const select of host.querySelectorAll("[data-scene]"))
      select.onchange = () => {
        if (this.saving) return;
        const key = select.dataset.scene;
        this.store.setScene(d.registry_id, key, untoken(select.value));
        this.paint();
        this.q(`[data-scene="${key}"]`)?.focus();
      };
    if (this.store.errors(d.registry_id).length)
      this.q("#row-error").textContent = w.duplicate_preset;
  }
  paintFooter() {
    const w = this.words;
    if (!this.q("#confirm")) return;
    this.q("#confirm").disabled =
      !this.store?.dirty ||
      this.saving ||
      this.stale ||
      Boolean(this.store?.errors().length);
    this.q("#cancel").disabled =
      (!this.store?.dirty && !this.stale) || this.saving;
    this.q("#count").textContent = this.store
      ? `${w.changedCount}: ${this.store.changedDevices} · ${w.selectedCount}: ${this.store.enabledCount}/${this.store.devices.length}${!equal(this.store.profiles, this.store.savedProfiles) ? ` · ${w.profileChanged}` : ""}${!equal(this.store.flags, this.store.savedFlags) ? ` · ${w.globalChanged}` : ""}`
      : "";
    this.q("#message").textContent = this.message;
    const status = this.store?.export || {};
    const exportStatus = this.q("#export-status");
    exportStatus.textContent =
      (w[`export_${status.status}`] || "") +
      (status.warnings?.length
        ? ` · ${w.export_warnings}: ${status.warnings.length}`
        : "");
    exportStatus.classList.toggle("error", status.status === "error");
    exportStatus.title = [
      ...(status.errors || []),
      ...(status.warnings || []),
    ].join("\n");
    this.q("#editor-layout").inert = this.saving;
  }
  async save() {
    if (
      !this.store?.dirty ||
      this.saving ||
      this.stale ||
      this.store.errors().length
    )
      return;
    this.saving = true;
    this.message = "";
    this.paintFooter();
    try {
      const data = await this._hass.callWS({
        type: "wled_yaha/editor/save",
        revision: this.store.revision,
        settings: this.store.toSettings(),
      });
      this.store = new DraftStore(data);
      this.stale = false;
      this.message = this.words.saved;
    } catch (error) {
      this.stale = error.code === "stale_editor";
      this.message = this.stale
        ? this.words.stale
        : `${this.words.failed}\n${error.message || error.code || error}`;
    } finally {
      this.saving = false;
      this.paint();
    }
  }
  cancel() {
    if (this.saving || !this.store) return;
    if (this.stale) {
      this.store = null;
      this.stale = false;
      this.load();
    } else {
      this.store.resetAll();
      this.message = "";
      this.paint();
    }
  }
  showSettings() {
    if (!this.store || this.saving) return;
    const w = this.words;
    const dialog = this.dialog(
      w.settings,
      `<label class="toggle"><input id="auto-export" type="checkbox" ${this.store.flags.auto_export ? "checked" : ""}>${w.auto}</label><label class="toggle"><input id="connect-yaha" type="checkbox" ${this.store.flags.connect_yaha ? "checked" : ""}>${w.connect}</label><p class="hint">${w.connectHint}</p><p class="hint">${w.saveHint}</p>`,
    );
    dialog.querySelector("#auto-export").onchange = (e) => {
      this.store.flags.auto_export = e.target.checked;
      this.paintFooter();
    };
    dialog.querySelector("#connect-yaha").onchange = (e) => {
      this.store.flags.connect_yaha = e.target.checked;
      this.paintFooter();
    };
  }
  showSaveProfile() {
    if (!this.store || !this.selected || this.saving) return;
    const w = this.words;
    const dialog = this.dialog(
      w.saveProfile,
      `<label class="field" for="profile-name">${w.name}</label><input id="profile-name" maxlength="100" autocomplete="off"><p class="hint">${w.profileHint}</p><p class="error" id="profile-error" role="alert"></p>`,
      `<button id="create-profile" class="primary">${w.create}</button>`,
    );
    dialog.querySelector("#create-profile").onclick = () => {
      try {
        this.store.addProfile(
          this.selected,
          dialog.querySelector("#profile-name").value,
        );
        dialog.close();
        this.paint();
      } catch (error) {
        dialog.querySelector("#profile-error").textContent =
          w[error.message] || error.message;
      }
    };
    dialog.querySelector("#profile-name").onkeydown = (event) => {
      if (event.key === "Enter")
        dialog.querySelector("#create-profile").click();
    };
    dialog.querySelector("#profile-name").focus();
  }
  showProfiles() {
    if (!this.store || this.saving) return;
    const w = this.words;
    const rows =
      this.store.profiles
        .map(
          (profile) =>
            `<div class="profile-row"><span><strong>${escape(profile.name)}</strong><br><small class="secondary">${w.scenes}: ${Object.keys(profile.scenes).length}</small></span><button data-apply-profile="${escape(profile.id)}">${w.copy}</button><button data-delete-profile="${escape(profile.id)}">${w.delete}</button></div>`,
        )
        .join("") || `<p class="hint">${w.noProfiles}</p>`;
    const dialog = this.dialog(
      w.profiles,
      `${rows}<p class="hint">${w.profileHint}</p>`,
    );
    for (const button of dialog.querySelectorAll("[data-delete-profile]"))
      button.onclick = () => {
        this.store.profiles = this.store.profiles.filter(
          (p) => p.id !== button.dataset.deleteProfile,
        );
        this.paintFooter();
        this.showProfiles();
      };
    for (const button of dialog.querySelectorAll("[data-apply-profile]"))
      button.onclick = () => {
        dialog.close();
        this.showCopy(button.dataset.applyProfile);
      };
  }
  showCopy(profileId = null) {
    if (!this.store || this.saving) return;
    const w = this.words;
    const current = this.store.row(this.selected);
    const sources = [
      ...(current
        ? [{ value: `device:${current.registry_id}`, label: current.name }]
        : []),
      ...this.store.profiles.map((profile) => ({
        value: `profile:${profile.id}`,
        label: `${w.profiles}: ${profile.name}`,
      })),
    ];
    if (!sources.length) return;
    const initial = profileId ? `profile:${profileId}` : sources[0].value;
    const targets = this.store.devices
      .filter((d) => !d.missing)
      .map(
        (device) =>
          `<label><input type="checkbox" data-target="${escape(device.registry_id)}" ${profileId ? (this.store.record(device.registry_id).enabled ? "checked" : "") : device.registry_id !== this.selected && this.store.record(device.registry_id).enabled ? "checked" : ""}><span>${escape(device.name)} <span class="secondary">${escape(device.room || "")} · ${device.presets.length ? `${w.presets}: ${device.presets.length}` : w.unknownCatalog}</span></span></label>`,
      )
      .join("");
    const dialog = this.dialog(
      w.copy,
      `<label class="field" for="copy-source">${w.source}</label><select id="copy-source">${sources.map((s) => `<option value="${escape(s.value)}" ${s.value === initial ? "selected" : ""}>${escape(s.label)}</option>`).join("")}</select><label class="field" for="copy-mode">${w.copyMode}</label><select id="copy-mode"><option value="name">${w.byName}</option><option value="id">${w.byId}</option></select><p class="hint">${w.copyHint}</p><label class="field" for="copy-policy">${w.targets}</label><div class="targets">${targets}</div><label class="field" for="copy-policy">${w.copy}</label><select id="copy-policy"><option value="empty">${w.emptyOnly}</option><option value="replace">${w.replace}</option></select><div id="copy-report" class="report" role="status"></div>`,
      `<button id="apply-copy" class="primary">${w.applyDraft}</button>`,
    );
    const apply = dialog.querySelector("#apply-copy");
    const updateButton = () => {
      apply.disabled = !dialog.querySelector("[data-target]:checked");
    };
    for (const box of dialog.querySelectorAll("[data-target]"))
      box.onchange = updateButton;
    updateButton();
    apply.onclick = () => {
      const source = dialog.querySelector("#copy-source").value;
      const descriptors = source.startsWith("profile:")
        ? this.store.profiles.find((p) => p.id === source.slice(8))?.scenes
        : this.store.descriptors(source.slice(7));
      const ids = [...dialog.querySelectorAll("[data-target]:checked")].map(
        (box) => box.dataset.target,
      );
      const report = this.store.apply(
        descriptors || {},
        ids,
        dialog.querySelector("#copy-mode").value,
        dialog.querySelector("#copy-policy").value === "replace",
      );
      const describe = (items) =>
        items
          .map(
            (item) =>
              `${this.store.row(item.id)?.name}: ${this.sceneName(this.store.scenes.find((s) => s.id === item.scene))}`,
          )
          .join("; ");
      dialog.querySelector("#copy-report").textContent =
        `${w.copyReport}: ${report.changed}. ${w.unmatched}: ${report.unmatched.length}. ${w.conflicts}: ${report.conflicts.length}. ${w.pending}: ${report.pending.length}.${report.unmatched.length ? `\n${w.unmatched}: ${describe(report.unmatched)}` : ""}${report.conflicts.length ? `\n${w.conflicts}: ${describe(report.conflicts)}` : ""}`;
      this.paint();
    };
  }
  async showPreview() {
    if (!this.store || this.saving) return;
    const w = this.words;
    const dialog = this.dialog(
      w.preview,
      `<p class="hint">${this.store.dirty ? w.previewHint : w.savedPreview}</p><div id="preview-body"></div><p id="preview-error" class="error" role="alert"></p>`,
      `<button id="export-now" class="primary" ${this.store.dirty || this.stale ? "disabled" : ""}>${w.exportNow}</button>`,
    );
    try {
      const result = await this._hass.callWS({
        type: "wled_yaha/editor/preview",
        settings: this.store.toSettings(),
      });
      if (!dialog.open) return;
      dialog.querySelector("#preview-body").innerHTML =
        (result.devices || [])
          .map(
            (device) =>
              `<div class="preview-row"><strong>${escape(device.name)}</strong><br><code>${escape(device.light)}</code><ul>${Object.entries(
                device.scenes,
              )
                .map(
                  ([scene, name]) =>
                    `<li>${escape(this.sceneName(this.store.scenes.find((s) => s.id === scene)))} → ${escape(name)}</li>`,
                )
                .join("")}</ul></div>`,
          )
          .join("") +
        `${result.warnings?.length ? `<h3>${w.warnings}</h3><p class="report">${escape(result.warnings.join("\n"))}</p>` : ""}`;
      if (result.errors?.length)
        dialog.querySelector("#preview-error").textContent =
          result.errors.join("\n");
      dialog.querySelector("#export-now").disabled = Boolean(
        this.store.dirty || this.stale || result.errors?.length,
      );
    } catch (error) {
      dialog.querySelector("#preview-error").textContent =
        error.message || error.code || String(error);
      dialog.querySelector("#export-now").disabled = true;
    }
    dialog.querySelector("#export-now").onclick = async () => {
      const button = dialog.querySelector("#export-now");
      button.disabled = true;
      try {
        await this._hass.callWS({ type: "wled_yaha/editor/export" });
        this.message = w.exportSaved;
        dialog.close();
        await this.load(true);
        this.message = w.exportSaved;
        this.paintFooter();
      } catch (error) {
        dialog.querySelector("#preview-error").textContent =
          error.message || error.code || String(error);
        button.disabled = false;
      }
    };
  }
  dialog(title, body, actions = "") {
    const host = this.q("#dialogs");
    host.innerHTML = `<dialog aria-labelledby="dialog-title"><div class="dialog-heading"><button class="close" aria-label="${this.words.close}">×</button><h2 id="dialog-title">${escape(title)}</h2></div><div class="dialog-body">${body}</div><div class="dialog-actions"><button class="cancel">${this.words.close}</button>${actions}</div></dialog>`;
    const dialog = host.querySelector("dialog");
    dialog.querySelector(".close").onclick = dialog.querySelector(
      ".cancel",
    ).onclick = () => dialog.close();
    dialog.showModal();
    return dialog;
  }
}

if (globalThis.customElements && !customElements.get("wled-yaha-panel"))
  customElements.define("wled-yaha-panel", WledYahaPanel);
