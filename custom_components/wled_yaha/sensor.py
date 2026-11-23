"""Visible export health and actionable diagnostics."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import BridgeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ExportStatus(entry)])


class ExportStatus(BridgeEntity, SensorEntity):
    _attr_translation_key = "status"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["pending", "ready", "error", "reload_required"]
    _attr_icon = "mdi:string-lights"

    def __init__(self, entry):
        super().__init__(entry, "status")

    @property
    def native_value(self):
        return self.runtime.status

    @property
    def extra_state_attributes(self):
        result = self.runtime.result or {}
        return {
            k: result[k]
            for k in (
                "device_count",
                "output_file",
                "warnings",
                "errors",
                "connection_changed",
                "yaha_reloaded",
            )
            if k in result
        }
