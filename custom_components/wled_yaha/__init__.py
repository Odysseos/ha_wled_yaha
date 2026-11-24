"""UI configured bridge from native WLED lights to YaHA."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import voluptuous as vol
import yaml
from homeassistant.const import (
    EVENT_COMPONENT_LOADED,
    EVENT_HOMEASSISTANT_STARTED,
    Platform,
)
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.service import async_register_admin_service

from .connection import apply_connection, plan_connection
from .generator import (
    DEFAULT_OUTPUT,
    DOMAIN,
    MARKER,
    build_export,
    render_export,
    write_export,
)
from .model import config_options, snapshots_for_records

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR]
SERVICE = "export_yaha_config"
SERVICE_SCHEMA = vol.Schema({vol.Optional("dry_run", default=False): cv.boolean})


def build_preview(hass, options, previous_scenes=None, *, allow_empty=False):
    settings, snapshots, warnings, errors = snapshots_for_records(
        hass, options.get("lights", []), previous_scenes
    )
    payload, devices, extra_warnings, extra_errors = build_export(snapshots, settings)
    if not snapshots and not allow_empty:
        errors.append("Не выбрана ни одна существующая сущность WLED.")
    return {
        "output_file": DEFAULT_OUTPUT,
        "device_count": len(devices),
        "devices": devices,
        "warnings": warnings + extra_warnings,
        "errors": errors + extra_errors,
        "yaml": render_export(payload),
    }


def _read_previous_scenes(config_dir):
    path = Path(config_dir) / DEFAULT_OUTPUT
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.startswith(MARKER):
        return {}
    payload = yaml.safe_load(text)
    if not isinstance(payload, dict):
        return {}
    result = {}
    entities = payload.get("entity_config", {})
    if not isinstance(entities, dict):
        return {}
    for entity_id, config in entities.items():
        if not isinstance(config, dict):
            continue
        modes = config.get("modes", {})
        if not isinstance(modes, dict):
            continue
        scenes = modes.get("scene", {})
        if isinstance(scenes, dict):
            result[entity_id] = {
                scene: name for scene, name in scenes.items() if isinstance(name, str)
            }
    return result


def _write_files(config_dir, text, connect):
    plan = plan_connection(config_dir) if connect else None
    changed = write_export(config_dir, DEFAULT_OUTPUT, text)
    connected = apply_connection(plan) if plan else False
    return changed, connected


class Runtime:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.lock = hass.data.setdefault(DOMAIN, asyncio.Lock())
        self.result = None
        self.status = "pending"
        self.cancel_pending = None
        self.closed = False
        self.discovery = None

    @callback
    def schedule(self, *_):
        options = config_options(self.entry)
        if (
            self.closed
            or not options["auto_export"]
            or not options.get("configured", True)
        ):
            return
        if self.cancel_pending:
            self.cancel_pending()
        self.cancel_pending = async_call_later(self.hass, 3, self._scheduled)

    async def _scheduled(self, _):
        self.cancel_pending = None
        try:
            await self.export()
        except HomeAssistantError as err:
            _LOGGER.warning("Автоматический экспорт YaHA отложен: %s", err)

    @callback
    def event(self, event):
        if event.event_type == "state_changed":
            entity_id = event.data.get("entity_id", "")
            if not entity_id.startswith(("light.", "select.")):
                return
            settings, snapshots, _, _ = snapshots_for_records(
                self.hass, config_options(self.entry).get("lights", [])
            )
            selected = set(settings["include_entities"]) | {
                s.preset_entity for s in snapshots
            }
            if entity_id not in selected:
                return
            old, new = event.data.get("old_state"), event.data.get("new_state")
            if (
                old
                and new
                and (
                    old.attributes.get("options") == new.attributes.get("options")
                    and old.attributes.get("friendly_name")
                    == new.attributes.get("friendly_name")
                    and (old.state in {"unavailable", "unknown"})
                    == (new.state in {"unavailable", "unknown"})
                )
            ):
                return
        self.schedule()

    async def export(self, *, dry_run=False):
        async with self.lock:
            if self.closed:
                raise ServiceValidationError("Интеграция выгружена; экспорт отменён.")
            options = config_options(self.entry)
            if self.discovery is not None:
                self.discovery.install()
            try:
                previous_scenes = await self.hass.async_add_executor_job(
                    _read_previous_scenes, self.hass.config.config_dir
                )
            except (OSError, ValueError, yaml.YAMLError):
                previous_scenes = {}
            result = build_preview(
                self.hass,
                options,
                previous_scenes,
                allow_empty=options.get("configured", True),
            )
            result.update(written=False, changed=False, connection_changed=False)
            try:
                if options["connect_yaha"]:
                    plan = await self.hass.async_add_executor_job(
                        plan_connection, self.hass.config.config_dir
                    )
                    result["connection"] = {
                        "connected": plan.connected,
                        "file": str(
                            plan.target.relative_to(self.hass.config.config_dir)
                        )
                        if plan.target
                        else None,
                    }
                if dry_run:
                    return result
                if result["errors"]:
                    raise ServiceValidationError(" ".join(result["errors"]))
                changed, connected = await self.hass.async_add_executor_job(
                    _write_files,
                    self.hass.config.config_dir,
                    result["yaml"],
                    options["connect_yaha"],
                )
                result.update(
                    written=True, changed=changed, connection_changed=connected
                )
                if (changed or connected) and self.hass.services.has_service(
                    "yandex_smart_home", "reload"
                ):
                    try:
                        await self.hass.services.async_call(
                            "yandex_smart_home", "reload", {}, blocking=True
                        )
                        result["yaha_reloaded"] = True
                    except HomeAssistantError as err:
                        result["warnings"].append(
                            "Файл сохранён; YaHA не удалось перечитать: " + str(err)
                        )
                        result["yaha_reloaded"] = False
                elif not self.hass.services.has_service("yandex_smart_home", "reload"):
                    result["warnings"].append(
                        "YaHA не загружена. Добавь/включи её и перечитай настройки YaHA."
                    )
                    result["yaha_reloaded"] = False
                if self.discovery is not None:
                    self.discovery.install()
                    result["yaha_discovery"] = self.discovery.diagnostics()
                    if self.discovery.status != "active":
                        result["warnings"].append(
                            "Адаптер обнаружения недоступных гирлянд не активен: "
                            + self.discovery.status
                            + ". Обнови/загрузи YaHA; см. диагностику."
                        )
                self.status = (
                    "ready" if result.get("yaha_reloaded", True) else "reload_required"
                )
                self.result = result
            except (OSError, TypeError, ValueError, HomeAssistantError) as err:
                self.status = "error"
                if str(err) not in result["errors"]:
                    result["errors"].append(str(err))
                self.result = result
                raise ServiceValidationError(str(err)) from err
            finally:
                if not dry_run:
                    async_dispatcher_send(self.hass, DOMAIN + "_updated")
            return result

    @callback
    def close(self):
        self.closed = True
        if self.discovery is not None:
            self.discovery.close()
        if self.cancel_pending:
            self.cancel_pending()
            self.cancel_pending = None


async def async_setup(hass, config):
    from . import editor

    editor.register(hass)

    async def export(call):
        loaded = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if getattr(e, "runtime_data", None) is not None
        ]
        if not loaded:
            raise ServiceValidationError(
                "Сначала добавь WLED YaHA в разделе «Устройства и службы»."
            )
        result = await loaded[0].runtime_data.export(
            dry_run=call.data.get("dry_run", False)
        )
        return result if call.return_response else None

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE,
        export,
        schema=SERVICE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


@callback
def _remove_legacy_bridge_device(hass, entry):
    """Keep export entities, but remove the old area-assigned service device."""
    devices = dr.async_get(hass)
    identity = (DOMAIN, entry.entry_id)
    device = next(
        (
            item
            for item in dr.async_entries_for_config_entry(devices, entry.entry_id)
            if identity in item.identifiers
        ),
        None,
    )
    if device is None:
        return
    owner = getattr(device, "config_entry_id", None)
    if owner is not None:
        owned = owner == entry.entry_id
    else:
        owned = device.config_entries == {entry.entry_id}
    if not owned or device.identifiers != {identity} or device.connections:
        return
    entities = er.async_get(hass)
    unique_ids = {entry.entry_id + "_export", entry.entry_id + "_status"}
    for entity in er.async_entries_for_device(
        entities, device.id, include_disabled_entities=True
    ):
        if (
            entity.config_entry_id == entry.entry_id
            and entity.platform == DOMAIN
            and entity.unique_id in unique_ids
        ):
            entities.async_update_entity(entity.entity_id, device_id=None)
    # An entity's device must be cleared before removing it: HA otherwise deletes
    # that entity registry entry, losing custom entity IDs and user settings.
    if not er.async_entries_for_device(
        entities, device.id, include_disabled_entities=True
    ):
        remove = getattr(devices, "async_remove_device", None) or devices.async_remove
        remove(device.id)


async def async_setup_entry(hass, entry):
    from . import frontend
    from .yaha_adapter import DiscoveryAdapter

    _remove_legacy_bridge_device(hass, entry)
    runtime = entry.runtime_data = Runtime(hass, entry)
    runtime.discovery = DiscoveryAdapter(runtime)
    runtime.discovery.install()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await frontend.async_register(hass)
    except Exception:
        runtime.close()
        await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        raise
    entry.async_on_unload(lambda: frontend.unregister(hass))
    entry.async_on_unload(runtime.close)
    entry.async_on_unload(
        hass.bus.async_listen(
            EVENT_COMPONENT_LOADED, runtime.discovery.component_loaded
        )
    )
    for event in (
        "state_changed",
        "entity_registry_updated",
        "device_registry_updated",
        "area_registry_updated",
    ):
        entry.async_on_unload(hass.bus.async_listen(event, runtime.event))
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    if hass.is_running:
        runtime.schedule()
    else:
        entry.async_on_unload(
            hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STARTED, runtime.schedule)
        )
    return True


async def _options_updated(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and not runtime.closed:
        runtime.status = "pending"
        runtime.schedule()
        async_dispatcher_send(hass, DOMAIN + "_updated")


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    text = render_export({"filter": {"include_entities": []}, "entity_config": {}})
    try:
        async with hass.data.setdefault(DOMAIN, asyncio.Lock()):
            await hass.async_add_executor_job(
                write_export, hass.config.config_dir, DEFAULT_OUTPUT, text
            )
            if hass.services.has_service("yandex_smart_home", "reload"):
                await hass.services.async_call(
                    "yandex_smart_home", "reload", {}, blocking=True
                )
    except (OSError, ValueError, HomeAssistantError):
        _LOGGER.exception("Не удалось очистить экспорт YaHA при удалении интеграции")
