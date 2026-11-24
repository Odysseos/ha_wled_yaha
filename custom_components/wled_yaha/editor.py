"""Admin-only snapshot and atomic settings editing for the master-detail panel."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .generator import ALIASES, DOMAIN, VERSION, normalized
from .model import config_options, resolve_entity, snapshots_for_records
from .ordering import light_choices


class EditError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def get_runtime(hass):
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime = getattr(entry, "runtime_data", None)
        if runtime is not None and not runtime.closed:
            return runtime
    raise EditError("not_loaded", "WLED YaHA не загружена.")


def settings(entry):
    current = config_options(entry)
    return {
        "lights": deepcopy(current.get("lights", [])),
        "profiles": deepcopy(current.get("profiles", [])),
        "auto_export": current["auto_export"],
        "connect_yaha": current["connect_yaha"],
        "configured": current.get("configured", True),
    }


def revision(entry):
    return hashlib.sha256(
        json.dumps(settings(entry), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def snapshot(hass, runtime):
    saved = settings(runtime.entry)
    selected = {r["registry_id"]: r for r in saved["lights"]}
    registry = er.async_get(hass)
    rows = []
    seen = set()
    for option in light_choices(hass):
        entity = registry.async_get(option["value"])
        record = deepcopy(
            selected.get(
                entity.id,
                {
                    "registry_id": entity.id,
                    "entity_id": entity.entity_id,
                    "scenes": {},
                    "scene_names": {},
                },
            )
        )
        _, snapshots, _, _ = snapshots_for_records(hass, [{**record, "enabled": True}])
        item = snapshots[0] if snapshots else None
        state = hass.states.get(entity.entity_id)
        presets = (
            [
                {"id": key, "name": value}
                for key, value in sorted(item.preset_ids.items())
            ]
            if item
            else []
        )
        if item and not presets:
            presets = [{"id": None, "name": value} for value in item.options]
        title = (
            option["label"].rsplit(" · ", 1)[0].removesuffix(" · " + entity.entity_id)
        )
        # The title includes room context exactly as in the old picker.
        rows.append(
            {
                "registry_id": entity.id,
                "entity_id": entity.entity_id,
                "device_id": entity.device_id,
                "name": item.name if item else title,
                "title": title,
                "room": item.room if item else None,
                "state": state.state if state else "not_loaded",
                "preset_entity": item.preset_entity if item else None,
                "presets": presets,
                "preset_online": item.preset_online if item else False,
                "catalog_known": bool(item and item.catalog_known) or bool(presets),
                "warnings": item.warnings if item else [],
                "missing": False,
            }
        )
        seen.add(entity.id)
    for record in saved["lights"]:
        if record["registry_id"] not in seen:
            rows.append(
                {
                    "registry_id": record["registry_id"],
                    "entity_id": record["entity_id"],
                    "device_id": None,
                    "name": record["entity_id"],
                    "title": record["entity_id"],
                    "room": None,
                    "state": "missing",
                    "preset_entity": None,
                    "presets": [],
                    "preset_online": False,
                    "catalog_known": False,
                    "warnings": ["Сущность удалена или отключена в HA."],
                    "missing": True,
                }
            )
    last = runtime.result or {}
    return {
        "revision": revision(runtime.entry),
        "version": VERSION,
        "settings": saved,
        "devices": rows,
        "scenes": [
            {"id": key, "ru": names[0], "en": key.replace("_", " ").title()}
            for key, names in ALIASES.items()
        ],
        "export": {
            "status": runtime.status,
            **{
                k: last[k]
                for k in (
                    "device_count",
                    "warnings",
                    "errors",
                    "output_file",
                    "yaha_reloaded",
                    "yaha_discovery",
                )
                if k in last
            },
        },
    }


def _scene_map(raw):
    if not isinstance(raw, dict) or set(raw) - set(ALIASES):
        raise EditError("invalid_settings", "Неверный список сцен.")
    result = {}
    for scene in ALIASES:
        value = raw.get(scene)
        if (
            value is not None
            and not (type(value) is int and 1 <= value <= 250)
            and not (isinstance(value, str) and value.strip() and len(value) <= 255)
        ):
            raise EditError("invalid_settings", "Неверное назначение пресета.")
        result[scene] = value
    assigned = [
        json.dumps(v, ensure_ascii=False) for v in result.values() if v is not None
    ]
    if len(assigned) != len(set(assigned)):
        raise EditError(
            "duplicate_preset",
            "Один пресет нельзя назначить нескольким сценам одной гирлянды.",
        )
    return result


def validate_settings(hass, entry, raw):
    if not isinstance(raw, dict) or set(raw) - {
        "lights",
        "profiles",
        "auto_export",
        "connect_yaha",
        "configured",
    }:
        raise EditError("invalid_settings", "Неверные настройки редактора.")
    if (
        type(raw.get("auto_export")) is not bool
        or type(raw.get("connect_yaha")) is not bool
    ):
        raise EditError(
            "invalid_settings", "Параметры экспорта должны быть логическими значениями."
        )
    source_records = raw.get("lights", [])
    if not isinstance(source_records, list):
        raise EditError("invalid_settings", "Требуется список гирлянд.")
    old = {r["registry_id"]: r for r in settings(entry)["lights"]}
    records, identities = [], set()
    for raw_record in source_records:
        if not isinstance(raw_record, dict) or not isinstance(
            raw_record.get("registry_id"), str
        ):
            raise EditError("invalid_settings", "Неверная сущность гирлянды.")
        identity = raw_record["registry_id"]
        if identity in identities:
            raise EditError("invalid_settings", "Гирлянда выбрана несколько раз.")
        identities.add(identity)
        scenes = _scene_map(raw_record.get("scenes", {}))
        supplied_names = raw_record.get("scene_names", {})
        if not isinstance(supplied_names, dict) or any(
            k not in ALIASES or not isinstance(v, str) or not v.strip() or len(v) > 255
            for k, v in supplied_names.items()
        ):
            raise EditError("invalid_settings", "Неверные имена пресетов.")
        if type(raw_record.get("enabled", True)) is not bool:
            raise EditError("invalid_settings", "Неверная отметка экспорта.")
        entity = resolve_entity(hass, raw_record)
        if (
            entity is None
            or entity.platform != "wled"
            or not entity.entity_id.startswith("light.")
            or entity.disabled_by is not None
        ):
            if not raw_record.get("enabled", True):
                continue
            previous = old.get(identity)
            if (
                previous
                and scenes == _scene_map(previous.get("scenes", {}))
                and supplied_names == previous.get("scene_names", {})
            ):
                records.append(
                    {
                        "registry_id": identity,
                        "entity_id": previous["entity_id"],
                        "enabled": True,
                        "scenes": scenes,
                        "scene_names": deepcopy(supplied_names),
                    }
                )
                continue
            raise EditError(
                "missing_device",
                "Выбранная сущность удалена или отключена. Сними с неё отметку экспорта.",
            )
        record = {
            "registry_id": identity,
            "entity_id": entity.entity_id,
            "enabled": raw_record.get("enabled", True),
            "scenes": scenes,
            "scene_names": {
                k: v for k, v in supplied_names.items() if scenes[k] is not None
            },
        }
        _, devices, _, _ = snapshots_for_records(hass, [{**record, "enabled": True}])
        if devices:
            for scene, choice in scenes.items():
                name = (
                    devices[0].preset_ids.get(choice) if type(choice) is int else choice
                )
                if name:
                    record["scene_names"][scene] = name
        resolved_names = [normalized(value) for value in record["scene_names"].values()]
        if len(resolved_names) != len(set(resolved_names)):
            raise EditError(
                "duplicate_preset",
                "Один пресет нельзя назначить нескольким сценам одной гирлянды.",
            )
        records.append(record)
    profiles = raw.get("profiles", [])
    if not isinstance(profiles, list) or len(profiles) > 100:
        raise EditError("invalid_settings", "Слишком много профилей.")
    clean_profiles, profile_ids, profile_names = [], set(), set()
    for profile in profiles:
        if (
            not isinstance(profile, dict)
            or not isinstance(profile.get("id"), str)
            or not 1 <= len(profile["id"]) <= 64
            or not isinstance(profile.get("name"), str)
            or not profile["name"].strip()
            or len(profile["name"]) > 100
        ):
            raise EditError(
                "invalid_settings", "Профилю нужны имя и уникальный идентификатор."
            )
        name = profile["name"].strip()
        if profile["id"] in profile_ids or normalized(name) in profile_names:
            raise EditError("duplicate_profile", "Профили должны иметь разные имена.")
        values = profile.get("scenes", {})
        if not isinstance(values, dict) or set(values) - set(ALIASES):
            raise EditError("invalid_settings", "Неверные сцены профиля.")
        scenes = {}
        for key, value in values.items():
            if value is None:
                continue
            if not isinstance(value, dict):
                raise EditError("invalid_settings", "Неверный пресет профиля.")
            number, preset_name = value.get("preset_id"), value.get("preset_name")
            if number is not None and (
                type(number) is not int or not 1 <= number <= 250
            ):
                raise EditError("invalid_settings", "Неверный номер пресета профиля.")
            if preset_name is not None and (
                not isinstance(preset_name, str)
                or not preset_name.strip()
                or len(preset_name) > 255
            ):
                raise EditError("invalid_settings", "Неверное имя пресета профиля.")
            if number is None and preset_name is None:
                continue
            scenes[key] = {"preset_id": number, "preset_name": preset_name}
        clean_profiles.append({"id": profile["id"], "name": name, "scenes": scenes})
        profile_ids.add(profile["id"])
        profile_names.add(normalized(name))
    # Canonical ordering follows the same catalog presented in the master list.
    rank = {item["value"]: index for index, item in enumerate(light_choices(hass))}
    records.sort(
        key=lambda record: (
            rank.get(record["entity_id"], len(rank)),
            record["entity_id"],
        )
    )
    return {
        "lights": records,
        "profiles": clean_profiles,
        "auto_export": raw["auto_export"],
        "connect_yaha": raw["connect_yaha"],
        "configured": True,
    }


async def save(hass, runtime, expected_revision, raw):
    async with runtime.lock:
        if revision(runtime.entry) != expected_revision:
            raise EditError(
                "stale_editor",
                "Настройки изменились в другом окне. Черновик сохранён в открытом редакторе.",
            )
        cleaned = validate_settings(hass, runtime.entry, raw)
        hass.config_entries.async_update_entry(
            runtime.entry, options={**runtime.entry.options, **cleaned}
        )
    return snapshot(hass, runtime)


@websocket_api.websocket_command({"type": f"{DOMAIN}/editor/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_get(hass, connection, msg):
    try:
        connection.send_result(msg["id"], snapshot(hass, get_runtime(hass)))
    except EditError as err:
        connection.send_error(msg["id"], err.code, str(err))


@websocket_api.websocket_command(
    {
        "type": f"{DOMAIN}/editor/save",
        vol.Required("revision"): str,
        vol.Required("settings"): dict,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_save(hass, connection, msg):
    try:
        connection.send_result(
            msg["id"],
            await save(hass, get_runtime(hass), msg["revision"], msg["settings"]),
        )
    except (EditError, HomeAssistantError) as err:
        connection.send_error(msg["id"], getattr(err, "code", "save_failed"), str(err))


@websocket_api.websocket_command(
    {"type": f"{DOMAIN}/editor/preview", vol.Required("settings"): dict}
)
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_preview(hass, connection, msg):
    from . import build_preview

    try:
        runtime = get_runtime(hass)
        options = validate_settings(hass, runtime.entry, msg["settings"])
        connection.send_result(
            msg["id"], build_preview(hass, options, allow_empty=True)
        )
    except (EditError, HomeAssistantError, ValueError) as err:
        connection.send_error(
            msg["id"], getattr(err, "code", "preview_failed"), str(err)
        )


@websocket_api.websocket_command({"type": f"{DOMAIN}/editor/export"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_export(hass, connection, msg):
    try:
        connection.send_result(msg["id"], await get_runtime(hass).export())
    except (EditError, HomeAssistantError) as err:
        connection.send_error(
            msg["id"], getattr(err, "code", "export_failed"), str(err)
        )


@websocket_api.websocket_command({"type": f"{DOMAIN}/editor/subscribe"})
@websocket_api.require_admin
@websocket_api.async_response
async def websocket_subscribe(hass, connection, msg):
    @callback
    def updated():
        try:
            connection.send_event(msg["id"], snapshot(hass, get_runtime(hass)))
        except EditError:
            pass

    connection.subscriptions[msg["id"]] = async_dispatcher_connect(
        hass, DOMAIN + "_updated", updated
    )
    connection.send_result(msg["id"])


def register(hass):
    if hass.data.get(DOMAIN + "_editor_ws"):
        return
    for command in (
        websocket_get,
        websocket_save,
        websocket_preview,
        websocket_export,
        websocket_subscribe,
    ):
        websocket_api.async_register_command(hass, command)
    hass.data[DOMAIN + "_editor_ws"] = True
