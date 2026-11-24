"""Describe selected offline WLED lights without changing their actual state.

YaHA's device-list handler filters out unavailable entities before describing
those devices. Replace only that handler's list provider while this integration
is loaded. Query/action handlers retain YaHA's DEVICE_UNREACHABLE behavior.
No YaHA files, entity states, entity filters or Device properties are modified.
"""

from __future__ import annotations

import inspect
import logging
import sys
from functools import wraps

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import State, callback

from .model import config_options, resolve_entity

_LOGGER = logging.getLogger(__name__)


class DiscoveryAdapter:
    def __init__(self, runtime):
        self.runtime = runtime
        self.status = "waiting"
        self.error = None
        self._handlers = None
        self._original = None
        self._wrapper = None

    @callback
    def install(self):
        """Use the loaded YaHA API; defer until it exists and check its shape."""
        if self.runtime.closed:
            return
        handlers = next(
            (
                sys.modules.get(prefix + ".handlers")
                for prefix in (
                    "custom_components.yandex_smart_home",
                    "homeassistant.components.yandex_smart_home",
                )
                if sys.modules.get(prefix + ".handlers") is not None
            ),
            None,
        )
        if handlers is None:
            self.status = "waiting"
            return
        if (
            self._wrapper is not None
            and getattr(handlers, "async_get_devices", None) is self._wrapper
        ):
            return
        original = getattr(handlers, "async_get_devices", None)
        device_class = getattr(handlers, "Device", None)
        try:
            if not inspect.iscoroutinefunction(original) or not callable(device_class):
                raise TypeError("missing async device-list provider or Device class")
            inspect.signature(original).bind(self.runtime.hass, object())
            inspect.signature(device_class).bind(
                self.runtime.hass,
                object(),
                "light.wled",
                State("light.wled", STATE_UNAVAILABLE),
            )
        except (TypeError, ValueError) as err:
            self.status, self.error = "unsupported", str(err)
            _LOGGER.warning("YaHA offline discovery adapter is unavailable: %s", err)
            return

        @wraps(original)
        async def devices(hass, entry_data):
            result = await original(hass, entry_data)
            if hass is not self.runtime.hass or self.runtime.closed:
                return result
            if not callable(getattr(entry_data, "should_expose", None)):
                self.status, self.error = (
                    "unsupported",
                    "missing per-entry entity filter",
                )
                return result
            if not isinstance(result, list):
                self.status, self.error = (
                    "unsupported",
                    "device-list provider returned a non-list",
                )
                return result
            result = list(result)
            seen = {item.id for item in result}
            for entity, state in self._offline_entities(entry_data):
                if entity.entity_id not in seen:
                    result.append(
                        device_class(hass, entry_data, entity.entity_id, state)
                    )
                    seen.add(entity.entity_id)
            return result

        self._handlers, self._original, self._wrapper = handlers, original, devices
        handlers.async_get_devices = devices
        self.status, self.error = "active", None

    def _offline_entities(self, entry_data):
        hass = self.runtime.hass
        for record in config_options(self.runtime.entry).get("lights", []):
            if not record.get("enabled", True):
                continue
            entity = resolve_entity(hass, record)
            if (
                entity is None
                or entity.platform != "wled"
                or entity.domain != "light"
                or entity.disabled_by is not None
            ):
                continue
            if not entry_data.should_expose(entity.entity_id):
                continue
            state = hass.states.get(entity.entity_id)
            if state is not None and state.state != STATE_UNAVAILABLE:
                continue
            # Registry capabilities survive a restart while a controller is offline.
            # Preserve only known capabilities; never invent color/brightness support.
            attributes = {
                **(entity.capabilities or {}),
                "supported_features": entity.supported_features or 0,
            }
            attributes.update(dict(state.attributes) if state else {})
            attributes.setdefault(
                "friendly_name", entity.name or entity.original_name or entity.entity_id
            )
            yield entity, State(entity.entity_id, STATE_UNAVAILABLE, attributes)

    def diagnostics(self):
        return {"status": self.status, "error": self.error}

    @callback
    def component_loaded(self, event):
        if event.data.get("component") == "yandex_smart_home":
            self.install()

    @callback
    def close(self):
        if (
            self._handlers is not None
            and self._handlers.async_get_devices is self._wrapper
        ):
            self._handlers.async_get_devices = self._original
        self._handlers = self._original = self._wrapper = None
        self.status = "unloaded"
