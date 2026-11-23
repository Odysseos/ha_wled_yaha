"""One-click export of all selected garlands."""

from homeassistant.components.button import ButtonEntity
from homeassistant.helpers.entity import EntityCategory

from .entity import BridgeEntity


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([ExportButton(entry)])


class ExportButton(BridgeEntity, ButtonEntity):
    _attr_translation_key = "export"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:export"

    def __init__(self, entry):
        super().__init__(entry, "export")

    async def async_press(self):
        await self.runtime.export()
