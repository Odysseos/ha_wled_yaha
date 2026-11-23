"""Entities for local export controls and status."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .generator import DOMAIN, VERSION


class BridgeEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, key):
        self.runtime = entry.runtime_data
        self._attr_unique_id = entry.entry_id + "_" + key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="WLED YaHA",
            manufacturer="Community",
            model="YaHA preset bridge",
            sw_version=VERSION,
        )

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, DOMAIN + "_updated", self.async_write_ha_state
            )
        )
