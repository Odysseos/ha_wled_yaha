"""Run UI and export against real HA 2026.10.4 registries and selectors.

WLED network data are supplied from fixtures; no physical device is contacted.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N

import voluptuous as vol
from custom_components.wled_yaha import Runtime, build_preview
from custom_components.wled_yaha.button import ExportButton
from custom_components.wled_yaha.config_flow import (
    WledYahaConfigFlow,
    WledYahaOptionsFlow,
)
from custom_components.wled_yaha.generator import DEFAULT_OUTPUT
from custom_components.wled_yaha.model import config_options
from custom_components.wled_yaha.ordering import light_choices
from custom_components.wled_yaha.sensor import ExportStatus
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def config_entry(domain, data=None, unique_id=None):
    return ConfigEntry(
        data=data or {},
        discovery_keys={},
        domain=domain,
        minor_version=1,
        options={},
        source="user",
        subentries_data=[],
        title=domain,
        unique_id=unique_id or domain,
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

    async def test_no_presets_skips_scenes_and_creates_working_light_export(self):
        self.wled.runtime_data.data.presets.clear()
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        flow = self.flow()
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        self.assertEqual(result["step_id"], "finish")
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.assertTrue(
            all(v is None for v in result["data"]["lights"][0]["scenes"].values())
        )
        self.hass.config_entries.async_update_entry(self.entry, data=result["data"])
        export = await self.runtime.export()
        self.assertFalse(export["errors"])
        self.assertEqual(export["devices"][0]["scenes"], {})
        self.assertIn(self.light.entity_id, export["yaml"])
        self.assertNotIn("state_entity_id:", export["yaml"])

    async def test_no_presets_with_disabled_selector_does_not_block_setup(self):
        self.wled.runtime_data.data.presets.clear()
        er.async_get(self.hass).async_update_entity(
            self.preset.entity_id, disabled_by=er.RegistryEntryDisabler.USER
        )
        flow = self.flow()
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        self.assertEqual(result["step_id"], "finish")
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")

    async def test_scene_fields_are_optional_and_skip_ignores_default_mappings(self):
        flow = self.flow()
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        schema = result["data_schema"]
        self.assertFalse(any(isinstance(key, vol.Required) for key in schema.schema))
        result = await flow.async_step_scenes(schema({"skip_scenes": True}))
        self.assertEqual(result["step_id"], "finish")
        self.assertTrue(
            all(v is None for v in flow._draft["lights"][0]["scenes"].values())
        )

    async def test_no_scene_mapping_ignores_unavailable_preset_selector(self):
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.options,
                "lights": [{**self.options["lights"][0], "scenes": {}}],
            },
        )
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": ["Радуга", "Свеча"]}
        )
        result = await self.runtime.export()
        self.assertFalse(result["errors"])
        self.assertEqual(result["devices"][0]["scenes"], {})

    async def test_existing_scene_mapping_exports_with_unavailable_selector(self):
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": ["Радуга", "Свеча"]}
        )
        result = await self.runtime.export()
        self.assertFalse(result["errors"])
        self.assertEqual(result["devices"][0]["scenes"]["party"], "Радуга")

    async def test_lost_presets_are_not_automatically_cleared_in_options(self):
        self.wled.runtime_data.data.presets.clear()
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        flow = WledYahaOptionsFlow()
        flow.hass, flow.handler, flow.context, flow.flow_id = (
            self.hass,
            self.entry.entry_id,
            {},
            "options",
        )
        await flow.async_step_init()
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        self.assertEqual(result["step_id"], "scenes")
        result = await flow.async_step_scenes({"skip_scenes": True})
        self.assertEqual(result["step_id"], "finish")
        self.assertEqual(
            config_options(self.entry)["lights"][0]["scenes"], {"candle": 1, "party": 7}
        )

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
        await self.runtime.export()
        self.assertEqual(before, (self.path / DEFAULT_OUTPUT).read_bytes())
        self.assertEqual(self.runtime.status, "ready")
        self.assertTrue(ExportStatus(self.entry).extra_state_attributes["warnings"])

    async def add_garland(self, name, key, room="Спальня"):
        entry = config_entry("wled", unique_id=key)
        self.hass.config_entries._entries[entry.entry_id] = entry
        entry.runtime_data = N(data=N(presets={1: N(name="Свеча")}))
        device = dr.async_get(self.hass).async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("wled", key)}, name=name
        )
        area = ar.async_get(self.hass).async_get_or_create(room)
        dr.async_get(self.hass).async_update_device(device.id, area_id=area.id)
        registry = er.async_get(self.hass)
        light = registry.async_get_or_create(
            "light",
            "wled",
            key + "_0",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=key,
        )
        preset = registry.async_get_or_create(
            "select",
            "wled",
            key + "_preset",
            config_entry=entry,
            device_id=device.id,
            suggested_object_id=key + "_preset",
        )
        self.hass.states.async_set(light.entity_id, "on", {"friendly_name": name})
        self.hass.states.async_set(preset.entity_id, "Свеча", {"options": ["Свеча"]})
        return light

    async def test_picker_and_all_scene_steps_share_natural_name_order(self):
        lights = [
            await self.add_garland("Гирлянда 10", "ten"),
            await self.add_garland("Гирлянда 2", "two"),
            await self.add_garland("гирлянда 1", "one"),
        ]
        flow = self.flow()
        form = await flow.async_step_devices()
        picker = next(iter(form["data_schema"].schema.values()))
        self.assertFalse(picker.config["sort"])
        shown = [
            o["value"]
            for o in picker.config["options"]
            if o["value"] in {e.entity_id for e in lights}
        ]
        self.assertEqual(
            shown, [lights[2].entity_id, lights[1].entity_id, lights[0].entity_id]
        )
        result = await flow.async_step_devices({"lights": list(reversed(shown))})
        scene_order = []
        while result.get("step_id") == "scenes":
            scene_order.append(result["description_placeholders"]["entity"])
            result = await flow.async_step_scenes({"skip_scenes": True})
        self.assertEqual(scene_order, shown)
        self.assertEqual([r["entity_id"] for r in flow._draft["lights"]], shown)
        self.assertEqual(
            [d["light"] for d in build_preview(self.hass, flow._draft)["devices"]],
            shown,
        )

    async def test_same_names_sort_by_room_then_entity_id_and_remain_identifiable(self):
        first = await self.add_garland("Гирлянда", "garland_10", "Спальня")
        second = await self.add_garland("Гирлянда", "garland_2", "Спальня")
        third = await self.add_garland("Гирлянда", "child", "Детская")
        form = await self.flow().async_step_devices()
        picker = next(iter(form["data_schema"].schema.values()))
        options = [
            o
            for o in picker.config["options"]
            if o["value"] in {first.entity_id, second.entity_id, third.entity_id}
        ]
        self.assertEqual(
            [o["value"] for o in options],
            [third.entity_id, second.entity_id, first.entity_id],
        )
        self.assertTrue(all(o["value"] in o["label"] for o in options))
        self.assertIn("Детская", options[0]["label"])

    async def test_picker_shows_current_power_and_availability_status_in_russian(self):
        self.hass.config.language = "ru"
        on = await self.add_garland("Гирлянда 1", "on")
        off = await self.add_garland("Гирлянда 2", "off")
        offline = await self.add_garland("Гирлянда 3", "offline")
        unknown = await self.add_garland("Гирлянда 4", "unknown")
        unloaded = await self.add_garland("Гирлянда 5", "unloaded")
        self.hass.states.async_set(off.entity_id, "off")
        self.hass.states.async_set(offline.entity_id, "unavailable")
        self.hass.states.async_set(unknown.entity_id, "unknown")
        self.hass.states.async_remove(unloaded.entity_id)
        form = await self.flow().async_step_devices()
        picker = next(iter(form["data_schema"].schema.values()))
        labels = {
            option["value"]: option["label"] for option in picker.config["options"]
        }
        for entity, status in [
            (on, "🟢 Включена"),
            (off, "⚪ Выключена"),
            (offline, "🔴 Недоступна"),
            (unknown, "🟡 Состояние неизвестно"),
            (unloaded, "🟡 Ещё не загружена"),
        ]:
            self.assertTrue(
                labels[entity.entity_id].endswith(status), labels[entity.entity_id]
            )
        self.assertIn(offline.entity_id, picker([offline.entity_id]))

    async def test_status_changes_do_not_reorder_the_picker_or_change_identifiers(self):
        first = await self.add_garland("Гирлянда 2", "first")
        second = await self.add_garland("Гирлянда 10", "second")
        before = light_choices(self.hass)
        self.hass.states.async_set(first.entity_id, "unavailable")
        self.hass.states.async_set(second.entity_id, "off")
        after = light_choices(self.hass)
        self.assertEqual([o["value"] for o in before], [o["value"] for o in after])
        self.assertNotEqual(
            next(o["label"] for o in before if o["value"] == first.entity_id),
            next(o["label"] for o in after if o["value"] == first.entity_id),
        )

    async def test_reopening_picker_refreshes_status_and_scene_title_does_not_cache_status(
        self,
    ):
        self.hass.config.language = "en"
        flow = self.flow()
        await flow.async_step_devices()
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        result = await flow.async_step_devices({"lights": [self.light.entity_id]})
        self.assertNotIn("🟢", result["description_placeholders"]["device"])
        reopened = await self.flow().async_step_devices()
        picker = next(iter(reopened["data_schema"].schema.values()))
        label = next(
            o["label"]
            for o in picker.config["options"]
            if o["value"] == self.light.entity_id
        )
        self.assertTrue(label.endswith("🔴 Unavailable"))

    async def test_scene_order_stays_as_displayed_if_a_device_is_renamed_during_selection(
        self,
    ):
        first = await self.add_garland("Гирлянда 1", "a")
        second = await self.add_garland("Гирлянда 2", "b")
        flow = self.flow()
        await flow.async_step_devices()
        er.async_get(self.hass).async_update_entity(
            first.entity_id, name="Я — переименована"
        )
        result = await flow.async_step_devices(
            {"lights": [second.entity_id, first.entity_id]}
        )
        self.assertEqual(result["description_placeholders"]["entity"], first.entity_id)

    async def test_first_export_includes_an_offline_light_without_any_presets(self):
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.options,
                "lights": [{**self.options["lights"][0], "scenes": {}}],
            },
        )
        self.wled.runtime_data = None
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        result = await self.runtime.export()
        self.assertTrue(result["written"])
        self.assertEqual(result["device_count"], 1)
        self.assertIn(self.light.entity_id, result["yaml"])
        self.assertFalse(result["errors"])

    async def test_one_offline_device_does_not_block_other_selected_devices(self):
        other = await self.add_garland("Гирлянда 2", "second")
        self.hass.config_entries.async_update_entry(
            self.entry,
            data={
                **self.options,
                "lights": [
                    {**self.options["lights"][0], "scenes": {}},
                    {
                        "entity_id": other.entity_id,
                        "registry_id": other.id,
                        "scenes": {},
                    },
                ],
            },
        )
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        result = await self.runtime.export()
        self.assertFalse(result["errors"])
        self.assertEqual(
            {d["light"] for d in result["devices"]},
            {self.light.entity_id, other.entity_id},
        )

    async def test_skipping_a_light_without_presets_keeps_remaining_scene_order(self):
        self.wled.runtime_data.data.presets.clear()
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        er.async_get(self.hass).async_update_entity(
            self.light.entity_id, name="Гирлянда 1"
        )
        second = await self.add_garland("Гирлянда 2", "second")
        third = await self.add_garland("Гирлянда 3", "third")
        flow = self.flow()
        await flow.async_step_devices()
        result = await flow.async_step_devices(
            {"lights": [third.entity_id, self.light.entity_id, second.entity_id]}
        )
        self.assertEqual(result["description_placeholders"]["entity"], second.entity_id)
        result = await flow.async_step_scenes({"skip_scenes": True})
        self.assertEqual(result["description_placeholders"]["entity"], third.entity_id)
        await flow.async_step_scenes({"skip_scenes": True})
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")

    async def test_old_export_restores_scene_names_when_both_native_entities_are_offline(
        self,
    ):
        await self.runtime.export()
        before = (self.path / DEFAULT_OUTPUT).read_bytes()
        self.wled.runtime_data = None
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        result = await self.runtime.export()
        self.assertEqual(before, (self.path / DEFAULT_OUTPUT).read_bytes())
        self.assertEqual(
            result["devices"][0]["scenes"], {"candle": "Свеча", "party": "Радуга"}
        )

    async def test_wizard_saves_names_and_settings_after_devices_go_offline(self):
        flow = self.flow()
        await flow.async_step_devices({"lights": [self.light.entity_id]})
        await flow.async_step_scenes({"candle": "id:1", "party": "id:7"})
        self.assertEqual(
            flow._draft["lights"][0]["scene_names"],
            {"candle": "Свеча", "party": "Радуга"},
        )
        self.wled.runtime_data = None
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.hass.config_entries.async_update_entry(self.entry, data=result["data"])
        export = await self.runtime.export()
        self.assertEqual(export["devices"][0]["scenes"]["party"], "Радуга")

    async def test_offline_without_known_scene_names_exports_light_and_recovers_scenes_later(
        self,
    ):
        self.wled.runtime_data = None
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(
            self.preset.entity_id, "unavailable", {"options": []}
        )
        result = await self.runtime.export()
        self.assertTrue(result["written"])
        self.assertEqual(result["devices"][0]["scenes"], {})
        self.assertEqual(
            config_options(self.entry)["lights"][0]["scenes"], {"candle": 1, "party": 7}
        )
        self.wled.runtime_data = N(
            data=N(presets={1: N(name="Свеча"), 7: N(name="Радуга")})
        )
        self.hass.states.async_set(self.light.entity_id, "on")
        self.hass.states.async_set(
            self.preset.entity_id, "Свеча", {"options": ["Свеча", "Радуга"]}
        )
        result = await self.runtime.export()
        self.assertEqual(result["devices"][0]["scenes"]["party"], "Радуга")

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
