"""Run UI and export against real HA 2026.10.4 registries and selectors.

WLED network data are supplied from fixtures; no physical device is contacted.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N

from custom_components.wled_yaha import Runtime, build_preview
from custom_components.wled_yaha.button import ExportButton
from custom_components.wled_yaha.config_flow import (
    WledYahaConfigFlow,
    WledYahaOptionsFlow,
)
from custom_components.wled_yaha.generator import DEFAULT_OUTPUT
from custom_components.wled_yaha.model import config_options
from custom_components.wled_yaha.sensor import ExportStatus
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def config_entry(domain, data=None):
    return ConfigEntry(
        data=data or {},
        discovery_keys={},
        domain=domain,
        minor_version=1,
        options={},
        source="user",
        subentries_data=[],
        title=domain,
        unique_id=domain,
        version=1,
    )


class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        (self.path / "configuration.yaml").write_text(
            "homeassistant:\n  packages:\n    yaha_vakio:\n      yandex_smart_home: !include includes/yandex_smart_home_vakio.yaml\n"
        )
        self.hass = HomeAssistant(self.temp.name)

        async def reload_yaha(call):
            return None

        self.hass.services.async_register("yandex_smart_home", "reload", reload_yaha)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        dr.async_setup(self.hass)
        await ar.async_load(self.hass, load_empty=True)
        await dr.async_load(self.hass, load_empty=True)
        await er.async_load(self.hass, load_empty=True)
        self.wled = config_entry("wled")
        self.hass.config_entries._entries[self.wled.entry_id] = self.wled
        self.wled.runtime_data = N(
            data=N(presets={7: N(name="Радуга"), 1: N(name="Свеча")})
        )
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.wled.entry_id,
            identifiers={("wled", "AA")},
            name="Ёлка",
        )
        area = ar.async_get(self.hass).async_create("Спальня")
        dr.async_get(self.hass).async_update_device(device.id, area_id=area.id)
        registry = er.async_get(self.hass)
        self.light = registry.async_get_or_create(
            "light",
            "wled",
            "AA_0",
            config_entry=self.wled,
            device_id=device.id,
            suggested_object_id="tree",
        )
        self.preset = registry.async_get_or_create(
            "select",
            "wled",
            "AA_preset",
            config_entry=self.wled,
            device_id=device.id,
            suggested_object_id="tree_preset",
        )
        self.hass.states.async_set(
            self.light.entity_id,
            "on",
            {"friendly_name": "Ёлка", "supported_color_modes": ["rgb"]},
        )
        self.hass.states.async_set(
            self.preset.entity_id, "Свеча", {"options": ["Радуга", "Свеча"]}
        )
        self.options = {
            "lights": [
                {
                    "entity_id": self.light.entity_id,
                    "registry_id": self.light.id,
                    "scenes": {"candle": 1, "party": 7},
                }
            ],
            "auto_export": True,
            "connect_yaha": True,
        }
        self.entry = config_entry("wled_yaha", self.options)
        self.hass.config_entries._entries[self.entry.entry_id] = self.entry
        self.runtime = self.entry.runtime_data = Runtime(self.hass, self.entry)

    async def asyncTearDown(self):
        self.runtime.close()
        await self.hass.async_stop()
        self.temp.cleanup()

    def flow(self):
        flow = WledYahaConfigFlow()
        flow.hass, flow.context, flow.flow_id, flow.handler = (
            self.hass,
            {},
            "test",
            "wled_yaha",
        )
        return flow

    async def test_wizard_selects_real_entities_and_actual_preset_ids(self):
        flow = self.flow()
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        self.assertEqual(result["step_id"], "scenes")
        schema = result["data_schema"]
        values = {key.schema: key.default() for key in schema.schema}
        self.assertEqual(values["candle"], "id:1")
        self.assertEqual(values["fantasy"], "id:7")
        # Explicitly move rainbow to Party and disable the suggested Fantasy.
        values.update(fantasy="none", party="id:7")
        result = await flow.async_step_scenes(schema(values))
        self.assertEqual(result["step_id"], "finish")
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["lights"][0]["scenes"]["party"], 7)
        self.assertFalse((self.path / DEFAULT_OUTPUT).exists())

    async def test_duplicate_scene_assignment_is_rejected(self):
        flow = self.flow()
        await flow.async_step_devices({"lights": [self.light.entity_id]})
        result = await flow.async_step_scenes({"party": "id:7", "fantasy": "id:7"})
        self.assertEqual(result["errors"], {"base": "duplicate_preset"})

    async def test_options_draft_cancellation_does_not_modify_saved_mapping(self):
        flow = WledYahaOptionsFlow()
        flow.hass, flow.handler, flow.context, flow.flow_id = (
            self.hass,
            self.entry.entry_id,
            {},
            "options",
        )
        result = await flow.async_step_init()
        self.assertEqual(result["type"], "menu")
        await flow.async_step_devices({"lights": [self.light.entity_id]})
        await flow.async_step_scenes({"night": "id:1"})
        self.assertEqual(
            config_options(self.entry)["lights"][0]["scenes"], {"candle": 1, "party": 7}
        )

    async def test_export_connects_packages_backups_and_reloads_yaha(self):
        calls = []

        async def reload(call):
            calls.append(call.data)

        self.hass.services.async_register("yandex_smart_home", "reload", reload)
        result = await self.runtime.export()
        self.assertTrue(result["written"])
        self.assertTrue(result["connection_changed"])
        self.assertTrue(result["yaha_reloaded"])
        self.assertEqual(len(calls), 1)
        self.assertTrue((self.path / "configuration.yaml.wled-yaha.bak").exists())
        text = (self.path / DEFAULT_OUTPUT).read_text()
        self.assertIn("party: Радуга", text)
        self.assertIn(self.preset.entity_id, text)
        self.assertIn("yaha_vakio:", (self.path / "configuration.yaml").read_text())
        result = await self.runtime.export()
        self.assertFalse(result["changed"])
        self.assertFalse(result["connection_changed"])
        self.assertEqual(len(calls), 1)

    async def test_preview_never_writes_files(self):
        before = (self.path / "configuration.yaml").read_bytes()
        result = await self.runtime.export(dry_run=True)
        self.assertIn("yaml", result)
        self.assertFalse((self.path / DEFAULT_OUTPUT).exists())
        self.assertEqual((self.path / "configuration.yaml").read_bytes(), before)

    async def test_renamed_entities_follow_stable_registry_identity(self):
        registry = er.async_get(self.hass)
        registry.async_update_entity(
            self.light.entity_id, new_entity_id="light.renamed_tree"
        )
        registry.async_update_entity(
            self.preset.entity_id, new_entity_id="select.renamed_preset"
        )
        self.hass.states.async_set(
            "light.renamed_tree", "on", {"friendly_name": "Ёлка"}
        )
        self.hass.states.async_set(
            "select.renamed_preset", "Свеча", {"options": ["Радуга", "Свеча"]}
        )
        result = await self.runtime.export(dry_run=True)
        self.assertFalse(result["errors"])
        self.assertEqual(result["devices"][0]["light"], "light.renamed_tree")
        self.assertEqual(result["devices"][0]["preset_entity"], "select.renamed_preset")

    async def test_missing_device_preserves_previous_export(self):
        await self.runtime.export()
        before = (self.path / DEFAULT_OUTPUT).read_bytes()
        er.async_get(self.hass).async_remove(self.light.entity_id)
        with self.assertRaises(ServiceValidationError):
            await self.runtime.export()
        self.assertEqual(before, (self.path / DEFAULT_OUTPUT).read_bytes())

    async def test_offline_device_preserves_previous_export_and_status(self):
        await self.runtime.export()
        before = (self.path / DEFAULT_OUTPUT).read_bytes()
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        with self.assertRaises(ServiceValidationError):
            await self.runtime.export()
        self.assertEqual(before, (self.path / DEFAULT_OUTPUT).read_bytes())
        self.assertEqual(self.runtime.status, "error")
        self.assertTrue(ExportStatus(self.entry).extra_state_attributes["errors"])

    async def test_renamed_preset_keeps_mapping_by_id(self):
        self.wled.runtime_data.data.presets[7] = N(name="Новое имя")
        self.hass.states.async_set(
            self.preset.entity_id, "Новое имя", {"options": ["Новое имя", "Свеча"]}
        )
        result = await self.runtime.export(dry_run=True)
        self.assertEqual(result["devices"][0]["scenes"]["party"], "Новое имя")

    async def test_missing_preset_is_not_silently_reassigned(self):
        self.wled.runtime_data.data.presets.pop(7)
        self.hass.states.async_set(
            self.preset.entity_id, "Свеча", {"options": ["Свеча"]}
        )
        with self.assertRaises(ServiceValidationError):
            await self.runtime.export()

    async def test_button_exports_and_sensor_reads_runtime(self):
        await ExportButton(self.entry).async_press()
        sensor = ExportStatus(self.entry)
        self.assertEqual(sensor.native_value, "ready")
        self.assertEqual(sensor.extra_state_attributes["device_count"], 1)

    async def test_no_selected_entities_does_not_auto_discover_others(self):
        options = {**self.options, "lights": []}
        preview = build_preview(self.hass, options)
        self.assertEqual(preview["devices"], [])
        self.assertTrue(preview["errors"])
