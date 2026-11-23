"""UI configured bridge from native WLED lights to YaHA."""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, Platform
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.service import async_register_admin_service

from .connection import apply_connection, plan_connection
from .generator import DEFAULT_OUTPUT, DOMAIN, build_export, render_export, write_export
from .model import config_options, snapshots_for_records

_LOGGER = logging.getLogger(__name__)
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.BUTTON, Platform.SENSOR]
SERVICE = "export_yaha_config"
SERVICE_SCHEMA = vol.Schema({vol.Optional("dry_run", default=False): cv.boolean})


def build_preview(hass, options):
    settings, snapshots, warnings, errors = snapshots_for_records(
        hass, options.get("lights", [])
    )
    payload, devices, extra_warnings, extra_errors = build_export(snapshots, settings)
    if not snapshots:
        errors.append("Не выбрана ни одна доступная гирлянда WLED.")
    return {
        "output_file": DEFAULT_OUTPUT,
        "device_count": len(devices),
        "devices": devices,
        "warnings": warnings + extra_warnings,
        "errors": errors + extra_errors,
        "yaml": render_export(payload),
    }


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

    @callback
    def schedule(self, *_):
        if self.closed or not config_options(self.entry)["auto_export"]:
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
            result = build_preview(self.hass, options)
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
        if self.cancel_pending:
            self.cancel_pending()
            self.cancel_pending = None


async def async_setup(hass, config):
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


async def async_setup_entry(hass, entry):
    runtime = entry.runtime_data = Runtime(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(runtime.close)
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
    await hass.config_entries.async_reload(entry.entry_id)


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
