"""Configure multiple WLED lights and their actual presets in the HA interface."""

from __future__ import annotations

from copy import deepcopy

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from . import build_preview
from .connection import plan_connection
from .discovery import collect_snapshots
from .generator import ALIASES, DOMAIN, validate_settings
from .model import (
    DEFAULT_OPTIONS,
    config_options,
    default_scenes,
    resolve_entity,
    snapshots_for_records,
)
from .ordering import light_choices


def choice_key(value):
    if value is None:
        return "none"
    return f"id:{value}" if type(value) is int else "name:" + value


def from_choice(value):
    if value == "none":
        return None
    return int(value[3:]) if value.startswith("id:") else value[5:]


class Wizard:
    _draft = None
    _queue = None

    def begin(self, settings=None):
        self._draft = deepcopy(settings or {**DEFAULT_OPTIONS, "lights": []})
        self._queue = []
        self._visible_order = None
        self._choice_labels = {}

    async def _next_device(self):
        self._queue.pop(0)
        if self._queue:
            return await self.async_step_scenes()
        return await self.async_step_finish()

    async def async_step_devices(self, user_input=None):
        if self._draft is None:
            self.begin()
        choices = light_choices(self.hass)
        if self._visible_order is None:
            self._visible_order = [choice["value"] for choice in choices]
            self._choice_labels = {
                choice["value"]: choice["label"].rsplit(" · ", 1)[0]
                for choice in choices
            }
        errors = {}
        if user_input is not None:
            selected = list(dict.fromkeys(user_input.get("lights", [])))
            rank = {
                entity_id: index for index, entity_id in enumerate(self._visible_order)
            }
            selected.sort(
                key=lambda entity_id: (rank.get(entity_id, len(rank)), entity_id)
            )
            registry = er.async_get(self.hass)
            previous = {r["registry_id"]: r for r in self._draft["lights"]}
            records = []
            for entity_id in selected:
                entity = registry.async_get(entity_id)
                if (
                    entity is None
                    or entity.platform != "wled"
                    or not entity_id.startswith("light.")
                    or entity.disabled_by is not None
                ):
                    errors["base"] = "invalid_light"
                    break
                record = deepcopy(
                    previous.get(
                        entity.id, {"registry_id": entity.id, "entity_id": entity_id}
                    )
                )
                record["entity_id"] = entity_id
                records.append(record)
            if not records:
                errors["base"] = "no_lights"
            if not errors:
                self._draft["lights"] = records
                self._queue = list(range(len(records)))
                return await self.async_step_scenes()
        selected = [
            e.entity_id
            for r in self._draft["lights"]
            if (e := resolve_entity(self.hass, r)) is not None
        ]
        if not selected:
            snapshots, _, _ = collect_snapshots(self.hass, validate_settings({}))
            selected = [s.light for s in snapshots]
        selected_set = set(selected)
        selected = [
            choice["value"] for choice in choices if choice["value"] in selected_set
        ]
        # HA's entity picker sorts independently and submits selection order. An
        # explicit unsorted option list gives the UI and wizard one shared order.
        self._visible_order = [choice["value"] for choice in choices]
        self._choice_labels = {
            choice["value"]: choice["label"].rsplit(" · ", 1)[0] for choice in choices
        }
        return self.async_show_form(
            step_id="devices",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("lights", default=selected): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices,
                            multiple=True,
                            sort=False,
                            mode=selector.SelectSelectorMode.LIST,
                        )
                    )
                }
            ),
        )

    async def async_step_scenes(self, user_input=None):
        index = self._queue[0]
        record = self._draft["lights"][index]
        _, snapshots, _, discovery_errors = snapshots_for_records(self.hass, [record])
        if not snapshots:
            return self.async_abort(reason="device_missing")
        snapshot = snapshots[0]
        if (
            not snapshot.preset_ids
            and not snapshot.options
            and not any(v is not None for v in record.get("scenes", {}).values())
        ):
            record["scenes"] = {scene: None for scene in ALIASES}
            return await self._next_device()
        errors = {}
        actual = [None] + list(sorted(snapshot.preset_ids))
        if not snapshot.preset_ids:
            actual += list(snapshot.options)
        choices = [
            {
                "value": choice_key(v),
                "label": "—"
                if v is None
                else (f"#{v} · {snapshot.preset_ids[v]}" if type(v) is int else v),
            }
            for v in actual
        ]
        defaults = record.get("scenes", default_scenes(snapshot))
        if user_input is not None:
            selected = {
                scene: None
                if user_input.get("skip_scenes", False)
                else from_choice(user_input.get(scene, "none"))
                for scene in ALIASES
            }
            assigned = [v for v in selected.values() if v is not None]
            if len(set(assigned)) != len(assigned):
                errors["base"] = "duplicate_preset"
            elif any(v not in actual for v in assigned):
                errors["base"] = "preset_missing"
            else:
                previous_choices = record.get("scenes", {})
                previous_names = record.get("scene_names", {})
                names = {}
                for scene, choice in selected.items():
                    if choice is None:
                        continue
                    name = (
                        snapshot.preset_ids.get(choice)
                        if type(choice) is int
                        else choice
                    )
                    if not name and choice == previous_choices.get(scene):
                        name = previous_names.get(scene)
                    if name:
                        names[scene] = name
                record["scene_names"] = names
                record["scenes"] = selected
                return await self._next_device()
            defaults = selected
        missing = [
            f"{ALIASES[k][0]}: {v}" for k, v in defaults.items() if v not in actual
        ]
        for v in dict.fromkeys(defaults.values()):
            if v not in actual:
                choices.append({"value": choice_key(v), "label": f"⚠ {v}"})
        schema = vol.Schema(
            {
                vol.Optional("skip_scenes", default=False): bool,
                **{
                    vol.Optional(
                        scene, default=choice_key(defaults.get(scene))
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices, mode=selector.SelectSelectorMode.DROPDOWN
                        )
                    )
                    for scene in ALIASES
                },
            }
        )
        detail = "\n".join(discovery_errors + snapshot.errors + snapshot.warnings)
        if missing:
            detail += "\nОтсутствуют пресеты: " + ", ".join(missing)
        return self.async_show_form(
            step_id="scenes",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "device": self._choice_labels.get(
                    snapshot.light, snapshot.name
                ).removesuffix(" · " + snapshot.light),
                "entity": snapshot.light,
                "number": str(index + 1),
                "total": str(len(self._draft["lights"])),
                "details": detail or "—",
            },
        )

    async def async_step_finish(self, user_input=None):
        errors = {}
        connection_errors = []
        if user_input is not None:
            self._draft.update(user_input)
        preview = build_preview(self.hass, self._draft)
        connection = ""
        if self._draft["connect_yaha"]:
            try:
                plan = await self.hass.async_add_executor_job(
                    plan_connection, self.hass.config.config_dir
                )
                connection = (
                    "Уже подключён."
                    if plan.connected
                    else "Будет автоматически добавлен отдельный пакет YaHA. Перед изменением существующего файла создаётся копия .wled-yaha.bak."
                )
            except (OSError, ValueError) as err:
                connection_errors.append(str(err))
        if user_input is not None:
            if connection_errors:
                errors["base"] = "validation_failed"
            else:
                return self.async_create_entry(title="WLED YaHA", data=self._draft)
        lines = []
        for device in preview["devices"]:
            rows = ", ".join(
                f"{ALIASES[k][0]} → {v}" for k, v in device["scenes"].items()
            )
            lines.append(f"**{device['name']}**: {rows or 'только управление светом'}")
        pending = (
            [
                "Настройки можно сохранить. Экспорт будет повторён после устранения следующих проблем:",
                *preview["errors"],
            ]
            if preview["errors"]
            else []
        )
        summary = "\n\n".join(
            lines + [connection] + connection_errors + pending + preview["warnings"]
        )
        return self.async_show_form(
            step_id="finish",
            errors=errors,
            description_placeholders={"summary": summary or "—"},
            data_schema=vol.Schema(
                {
                    vol.Required(
                        "auto_export", default=self._draft["auto_export"]
                    ): bool,
                    vol.Required(
                        "connect_yaha", default=self._draft["connect_yaha"]
                    ): bool,
                }
            ),
        )


class WledYahaConfigFlow(Wizard, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if not any(
            e.platform == "wled"
            and e.entity_id.startswith("light.")
            and e.disabled_by is None
            for e in er.async_get(self.hass).entities.values()
        ):
            return self.async_abort(reason="no_wled")
        return await self.async_step_devices(user_input)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return WledYahaOptionsFlow()


class WledYahaOptionsFlow(Wizard, config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if self._draft is None:
            self.begin(config_options(self.config_entry))
        return self.async_show_menu(
            step_id="init", menu_options=["devices", "finish", "export"]
        )

    async def async_step_export(self, user_input=None):
        runtime = getattr(self.config_entry, "runtime_data", None)
        if runtime is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            try:
                await runtime.export()
            except HomeAssistantError as err:
                return self.async_show_form(
                    step_id="export",
                    data_schema=vol.Schema({}),
                    errors={"base": "validation_failed"},
                    description_placeholders={"summary": str(err)},
                )
            return self.async_abort(reason="exported")
        try:
            preview = await runtime.export(dry_run=True)
            summary = f"Устройств: {preview['device_count']}.\n\n" + "\n\n".join(
                preview["errors"] + preview["warnings"]
            )
        except HomeAssistantError as err:
            summary = str(err)
        return self.async_show_form(
            step_id="export",
            data_schema=vol.Schema({}),
            description_placeholders={"summary": summary},
        )
