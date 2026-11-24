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
from homeassistant.exceptions import ServiceValidationError, Unauthorized
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

    async def test_skipping_empty_catalogs_counts_only_visible_scene_steps(self):
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
        self.assertEqual(result["description_placeholders"]["number"], "1")
        self.assertEqual(result["description_placeholders"]["total"], "2")
        result = await flow.async_step_scenes({"skip_scenes": True})
        self.assertEqual(result["description_placeholders"]["entity"], third.entity_id)
        self.assertEqual(result["description_placeholders"]["number"], "2")
        self.assertEqual(result["description_placeholders"]["total"], "2")
        await flow.async_step_scenes({"skip_scenes": True})
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")

    async def five_garlands(self, *, second_has_presets):
        registry = er.async_get(self.hass)
        registry.async_update_entity(self.light.entity_id, name="Гирлянда WLED1")
        lights = [self.light]
        for number in range(2, 6):
            lights.append(
                await self.add_garland(f"Гирлянда WLED{number}", f"wled{number}")
            )
        for number, light in enumerate(lights, 1):
            source = self.hass.config_entries.async_get_entry(light.config_entry_id)
            presets = (
                {1: N(name="Aurora"), 2: N(name="Flow")}
                if number == 2 and second_has_presets
                else {}
            )
            source.runtime_data = N(data=N(presets=presets))
            self.hass.states.async_set(light.entity_id, "unavailable")
            for entity in registry.entities.values():
                if (
                    entity.config_entry_id == source.entry_id
                    and entity.entity_id.startswith("select.")
                ):
                    self.hass.states.async_set(
                        entity.entity_id, "unavailable", {"options": []}
                    )
        return lights

    async def test_five_offline_lights_only_wled2_has_presets_first_dialog_is_one_of_one(
        self,
    ):
        lights = await self.five_garlands(second_has_presets=True)
        flow = self.flow()
        form = await flow.async_step_devices()
        picker = next(iter(form["data_schema"].schema.values()))
        shown = [option["value"] for option in picker.config["options"]]
        self.assertEqual(shown, [light.entity_id for light in lights])
        result = await flow.async_step_devices({"lights": list(reversed(shown))})
        self.assertEqual(
            result["description_placeholders"]["entity"], lights[1].entity_id
        )
        self.assertEqual(result["description_placeholders"]["number"], "1")
        self.assertEqual(result["description_placeholders"]["total"], "1")
        self.assertEqual(flow._auto_skipped, [0, 2, 3, 4])
        result = await flow.async_step_scenes({})
        self.assertEqual(result["step_id"], "finish")
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.hass.config_entries.async_update_entry(self.entry, data=result["data"])
        export = await self.runtime.export()
        self.assertEqual(export["device_count"], 5)
        self.assertEqual([item["light"] for item in export["devices"]], shown)

    async def test_all_five_empty_catalogs_skip_every_scene_dialog_but_export_all_lights(
        self,
    ):
        lights = await self.five_garlands(second_has_presets=False)
        flow = self.flow()
        result = await flow.async_step_devices(
            {"lights": [light.entity_id for light in lights]}
        )
        self.assertEqual(result["step_id"], "finish")
        self.assertEqual(flow._scene_total, 0)
        self.assertEqual(flow._auto_skipped, [0, 1, 2, 3, 4])
        result = await flow.async_step_finish(
            {"auto_export": True, "connect_yaha": True}
        )
        self.assertEqual(result["type"], "create_entry")
        self.hass.config_entries.async_update_entry(self.entry, data=result["data"])
        export = await self.runtime.export()
        self.assertEqual(export["device_count"], 5)

    async def test_catalog_lost_between_dialogs_skips_that_light_without_counter_gap(
        self,
    ):
        second = await self.add_garland("Гирлянда 2", "second")
        third = await self.add_garland("Гирлянда 3", "third")
        flow = self.flow()
        result = await flow.async_step_devices(
            {"lights": [second.entity_id, third.entity_id]}
        )
        self.assertEqual(result["description_placeholders"]["number"], "1")
        source = self.hass.config_entries.async_get_entry(third.config_entry_id)
        source.runtime_data.data.presets.clear()
        for entity in er.async_get(self.hass).entities.values():
            if entity.config_entry_id == third.config_entry_id:
                self.hass.states.async_set(
                    entity.entity_id, "unavailable", {"options": []}
                )
        result = await flow.async_step_scenes({"skip_scenes": True})
        self.assertEqual(result["step_id"], "finish")
        self.assertEqual(flow._scene_total, 1)
        self.assertEqual(flow._scene_completed, 1)

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

    async def test_editor_catalog_includes_offline_and_empty_presets(self):
        from custom_components.wled_yaha.editor import snapshot

        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.wled.runtime_data.data.presets = {}
        self.hass.states.async_set(self.preset.entity_id, "unknown", {"options": []})
        data = snapshot(self.hass, self.runtime)
        row = data["devices"][0]
        self.assertEqual(row["state"], "unavailable")
        self.assertEqual(row["presets"], [])
        self.assertTrue(row["catalog_known"])
        self.wled.runtime_data = None
        self.assertFalse(
            snapshot(self.hass, self.runtime)["devices"][0]["catalog_known"]
        )
        self.assertEqual(len(data["scenes"]), 18)

    async def test_editor_atomic_save_preserves_legacy_mappings_and_profiles(self):
        from custom_components.wled_yaha.editor import save, snapshot

        data = snapshot(self.hass, self.runtime)
        raw = data["settings"]
        raw["profiles"] = [
            {
                "id": "p1",
                "name": "Дом",
                "scenes": {"candle": {"preset_id": 1, "preset_name": "Свеча"}},
            }
        ]
        result = await save(self.hass, self.runtime, data["revision"], raw)
        record = result["settings"]["lights"][0]
        self.assertTrue(record["enabled"])
        self.assertEqual(record["scenes"]["party"], 7)
        self.assertEqual(record["scene_names"]["candle"], "Свеча")
        self.assertEqual(result["settings"]["profiles"], raw["profiles"])
        self.assertNotEqual(data["revision"], result["revision"])
        self.assertFalse((self.path / DEFAULT_OUTPUT).exists())

    async def test_editor_stale_and_invalid_save_leave_all_settings_unchanged(self):
        from copy import deepcopy

        from custom_components.wled_yaha.editor import EditError, save, snapshot

        data = snapshot(self.hass, self.runtime)
        raw = deepcopy(data["settings"])
        raw["lights"][0]["scenes"]["party"] = 1
        with self.assertRaises(EditError) as err:
            await save(self.hass, self.runtime, data["revision"], raw)
        self.assertEqual(err.exception.code, "duplicate_preset")
        self.assertEqual(
            snapshot(self.hass, self.runtime)["settings"], data["settings"]
        )
        with self.assertRaises(EditError) as err:
            await save(self.hass, self.runtime, "old-revision", data["settings"])
        self.assertEqual(err.exception.code, "stale_editor")
        self.assertEqual(
            snapshot(self.hass, self.runtime)["settings"], data["settings"]
        )

    async def test_editor_disabled_light_keeps_its_mapping_but_clears_owned_export(
        self,
    ):
        from custom_components.wled_yaha.editor import save, snapshot

        await self.runtime.export()
        data = snapshot(self.hass, self.runtime)
        data["settings"]["lights"][0]["enabled"] = False
        await save(self.hass, self.runtime, data["revision"], data["settings"])
        result = await self.runtime.export()
        self.assertEqual(result["device_count"], 0)
        self.assertEqual(result["errors"], [])
        self.assertNotIn(self.light.entity_id, (self.path / DEFAULT_OUTPUT).read_text())
        saved = snapshot(self.hass, self.runtime)["settings"]["lights"][0]
        self.assertEqual(saved["scenes"]["party"], 7)
        self.assertFalse(saved["enabled"])
        self.assertEqual(
            len(snapshot(self.hass, self.runtime)["devices"][0]["presets"]), 2
        )

    async def test_editor_caches_offline_assignments_and_exports_light(self):
        from custom_components.wled_yaha.editor import save, snapshot

        data = snapshot(self.hass, self.runtime)
        await save(self.hass, self.runtime, data["revision"], data["settings"])
        self.wled.runtime_data = None
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set(self.preset.entity_id, "unavailable")
        data = snapshot(self.hass, self.runtime)
        data["settings"]["auto_export"] = False
        await save(self.hass, self.runtime, data["revision"], data["settings"])
        result = await self.runtime.export(dry_run=True)
        self.assertEqual(result["device_count"], 1)
        self.assertEqual(result["devices"][0]["scenes"]["party"], "Радуга")
        self.assertFalse(result["errors"])

    async def test_editor_rejects_foreign_entities_and_duplicate_profiles(self):
        from custom_components.wled_yaha.editor import (
            EditError,
            settings,
            validate_settings,
        )

        raw = settings(self.entry)
        raw["lights"][0]["registry_id"] = "does-not-exist"
        with self.assertRaises(EditError):
            validate_settings(self.hass, self.entry, raw)
        raw = settings(self.entry)
        raw["profiles"] = [
            {"id": "1", "name": "Дом", "scenes": {}},
            {"id": "2", "name": "дом", "scenes": {}},
        ]
        with self.assertRaises(EditError) as err:
            validate_settings(self.hass, self.entry, raw)
        self.assertEqual(err.exception.code, "duplicate_profile")
        self.assertNotIn("profiles", self.entry.options)

    async def test_editor_all_websocket_commands_require_admin(self):
        from unittest.mock import Mock

        from custom_components.wled_yaha import editor

        connection = Mock(user=N(is_admin=False))
        for handler in (
            editor.websocket_get,
            editor.websocket_save,
            editor.websocket_preview,
            editor.websocket_export,
            editor.websocket_subscribe,
        ):
            with self.assertRaises(Unauthorized):
                handler(self.hass, connection, {"id": 42})
        self.assertFalse((self.path / DEFAULT_OUTPUT).exists())

    async def test_editor_options_update_does_not_reload_or_close_panel(self):
        from custom_components.wled_yaha import _options_updated

        self.options["auto_export"] = False
        await _options_updated(self.hass, self.entry)
        self.assertFalse(self.runtime.closed)
        self.assertIsNone(self.runtime.cancel_pending)
        self.assertEqual(self.runtime.status, "pending")

    async def test_new_entry_opens_panel_without_exporting_before_confirmation(self):
        self.hass.config_entries._entries.pop(self.entry.entry_id)
        result = await self.flow().async_step_user({})
        self.assertEqual(result["type"], "create_entry")
        self.assertFalse(result["data"]["configured"])
        self.assertEqual(result["data"]["lights"], [])
        entry = config_entry("wled_yaha", result["data"])
        runtime = Runtime(self.hass, entry)
        runtime.schedule()
        self.assertIsNone(runtime.cancel_pending)
        runtime.close()
        self.assertFalse((self.path / DEFAULT_OUTPUT).exists())

    async def test_editor_admin_subscription_refreshes_and_can_be_unsubscribed(self):
        from unittest.mock import Mock

        from custom_components.wled_yaha import editor
        from homeassistant.helpers.dispatcher import async_dispatcher_send

        connection = Mock(user=N(is_admin=True), subscriptions={})
        editor.websocket_get(self.hass, connection, {"id": 1})
        editor.websocket_subscribe(self.hass, connection, {"id": 2})
        await self.hass.async_block_till_done()
        self.assertEqual(
            connection.send_result.call_args_list[0].args[1]["version"], "0.2.2"
        )
        async_dispatcher_send(self.hass, "wled_yaha_updated")
        await self.hass.async_block_till_done()
        self.assertEqual(connection.send_event.call_args.args[0], 2)
        self.assertEqual(connection.send_event.call_count, 1)
        connection.subscriptions.pop(2)()
        async_dispatcher_send(self.hass, "wled_yaha_updated")
        await self.hass.async_block_till_done()
        self.assertEqual(connection.send_event.call_count, 1)

    async def test_editor_rejects_same_resolved_preset_by_id_and_name(self):
        from custom_components.wled_yaha.editor import (
            EditError,
            settings,
            validate_settings,
        )

        raw = settings(self.entry)
        raw["lights"][0]["scenes"]["alarm"] = "Свеча"
        with self.assertRaises(EditError) as err:
            validate_settings(self.hass, self.entry, raw)
        self.assertEqual(err.exception.code, "duplicate_preset")

    async def test_editor_missing_legacy_row_can_be_kept_or_explicitly_removed(self):
        from custom_components.wled_yaha.editor import save, snapshot

        er.async_get(self.hass).async_remove(self.light.entity_id)
        data = snapshot(self.hass, self.runtime)
        self.assertTrue(data["devices"][0]["missing"])
        data["settings"]["lights"][0].update(enabled=True, scene_names={})
        # Normalization in the frontend expands all optional scene keys.
        for scene in data["scenes"]:
            data["settings"]["lights"][0]["scenes"].setdefault(scene["id"], None)
        data = await save(self.hass, self.runtime, data["revision"], data["settings"])
        self.assertEqual(len(data["settings"]["lights"]), 1)
        data["settings"]["lights"][0]["enabled"] = False
        data = await save(self.hass, self.runtime, data["revision"], data["settings"])
        self.assertEqual(data["settings"]["lights"], [])

    async def test_export_controls_do_not_create_an_area_assigned_device(self):
        button, sensor = ExportButton(self.entry), ExportStatus(self.entry)
        self.assertIsNone(button.device_info)
        self.assertIsNone(sensor.device_info)
        self.assertEqual(button.unique_id, self.entry.entry_id + "_export")
        self.assertEqual(sensor.unique_id, self.entry.entry_id + "_status")
        await button.async_press()
        self.assertEqual(sensor.native_value, "ready")

    async def test_legacy_bridge_cleanup_preserves_entities_ids_names_and_wled_areas(
        self,
    ):
        from custom_components.wled_yaha import _remove_legacy_bridge_device

        devices, entities = dr.async_get(self.hass), er.async_get(self.hass)
        legacy = devices.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={("wled_yaha", self.entry.entry_id)},
            name="WLED YaHA",
        )
        service_area = ar.async_get(self.hass).async_create("Прихожая")
        devices.async_update_device(legacy.id, area_id=service_area.id)
        controls = [
            entities.async_get_or_create(
                domain,
                "wled_yaha",
                self.entry.entry_id + suffix,
                config_entry=self.entry,
                device_id=legacy.id,
                suggested_object_id="my_" + domain,
            )
            for domain, suffix in [("button", "_export"), ("sensor", "_status")]
        ]
        entities.async_update_entity(
            controls[0].entity_id,
            name="Мой экспорт",
            disabled_by=er.RegistryEntryDisabler.USER,
        )
        original_controls = [
            entities.async_get(entity.entity_id) for entity in controls
        ]
        native_before = devices.async_get(self.light.device_id)
        _remove_legacy_bridge_device(self.hass, self.entry)
        await self.hass.async_block_till_done()
        self.assertIsNone(devices.async_get(legacy.id))
        for original in original_controls:
            current = entities.async_get(original.entity_id)
            self.assertIsNotNone(current)
            self.assertIsNone(current.device_id)
            self.assertEqual(current.id, original.id)
            self.assertEqual(current.unique_id, original.unique_id)
            self.assertEqual(current.name, original.name)
            self.assertEqual(current.disabled_by, original.disabled_by)
        self.assertEqual(devices.async_get(self.light.device_id), native_before)
        preview = build_preview(self.hass, self.options)
        self.assertEqual(preview["device_count"], 1)
        self.assertEqual(preview["errors"], [])
        _remove_legacy_bridge_device(self.hass, self.entry)
        self.assertEqual(devices.async_get(self.light.device_id), native_before)

    async def test_device_cleanup_keeps_distinct_spaces_for_different_garlands(self):
        from custom_components.wled_yaha import _remove_legacy_bridge_device
        from custom_components.wled_yaha.editor import snapshot

        devices, entities = dr.async_get(self.hass), er.async_get(self.hass)
        room = ar.async_get(self.hass).async_create("Гостиная")
        second_entry = config_entry("wled", unique_id="second_wled")
        self.hass.config_entries._entries[second_entry.entry_id] = second_entry
        second_entry.runtime_data = N(data=N(presets={}))
        second_device = devices.async_get_or_create(
            config_entry_id=second_entry.entry_id,
            identifiers={("wled", "BB")},
            name="Ёлка 2",
        )
        devices.async_update_device(second_device.id, area_id=room.id)
        second_light = entities.async_get_or_create(
            "light",
            "wled",
            "BB_0",
            config_entry=second_entry,
            device_id=second_device.id,
            suggested_object_id="second_tree",
        )
        legacy = devices.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={("wled_yaha", self.entry.entry_id)},
            name="WLED YaHA",
        )
        devices.async_update_device(legacy.id, area_id=room.id)
        _remove_legacy_bridge_device(self.hass, self.entry)
        rows = {
            row["entity_id"]: row
            for row in snapshot(self.hass, self.runtime)["devices"]
        }
        self.assertEqual(rows[self.light.entity_id]["room"], "Спальня")
        self.assertEqual(rows[second_light.entity_id]["room"], "Гостиная")
        self.assertIsNone(devices.async_get(legacy.id))

    def fake_yaha_handlers(self):
        from types import ModuleType

        handlers = ModuleType("custom_components.yandex_smart_home.handlers")

        class Device:
            def __init__(self, hass, data, entity_id, state):
                self.id, self.state = entity_id, state

        async def original(hass, data):
            return [
                Device(hass, data, state.entity_id, state)
                for state in hass.states.async_all()
                if state.entity_id.startswith("light.")
                and state.state != "unavailable"
                and data.should_expose(state.entity_id)
            ]

        handlers.Device, handlers.async_get_devices = Device, original
        return handlers

    async def test_discovery_adapter_adds_selected_offline_lights_without_changing_state(
        self,
    ):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        self.hass.states.async_set("light.unrelated", "unavailable")
        original_state = self.hass.states.get(self.light.entity_id)
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            self.runtime.discovery = adapter = DiscoveryAdapter(self.runtime)
            adapter.install()
            self.assertEqual(adapter.status, "active")
            result = await handlers.async_get_devices(
                self.hass, N(should_expose=lambda _: True)
            )
            self.assertEqual([item.id for item in result], [self.light.entity_id])
            self.assertEqual(result[0].state.state, "unavailable")
            self.assertIs(self.hass.states.get(self.light.entity_id), original_state)
            self.assertEqual(
                self.hass.states.get("light.unrelated").state, "unavailable"
            )
            adapter.close()

    async def test_discovery_adapter_obeys_yaha_filter_and_disabled_export_checkbox(
        self,
    ):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            adapter = DiscoveryAdapter(self.runtime)
            adapter.install()
            self.assertEqual(
                await handlers.async_get_devices(
                    self.hass, N(should_expose=lambda _: False)
                ),
                [],
            )
            self.options["lights"][0]["enabled"] = False
            self.assertEqual(
                await handlers.async_get_devices(
                    self.hass, N(should_expose=lambda _: True)
                ),
                [],
            )
            adapter.close()

    async def test_discovery_adapter_handles_missing_state_using_registry_capabilities(
        self,
    ):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        self.hass.states.async_remove(self.light.entity_id)
        er.async_get(self.hass).async_update_entity(
            self.light.entity_id, capabilities={"supported_color_modes": ["rgb"]}
        )
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            adapter = DiscoveryAdapter(self.runtime)
            adapter.install()
            result = await handlers.async_get_devices(
                self.hass, N(should_expose=lambda _: True)
            )
            self.assertEqual(result[0].state.state, "unavailable")
            self.assertEqual(
                result[0].state.attributes["supported_color_modes"], ["rgb"]
            )
            self.assertIsNone(self.hass.states.get(self.light.entity_id))
            adapter.close()

    async def test_discovery_adapter_is_idempotent_and_runtime_unload_restores_yaha(
        self,
    ):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        original = handlers.async_get_devices
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            self.runtime.discovery = adapter = DiscoveryAdapter(self.runtime)
            adapter.install()
            first = handlers.async_get_devices
            adapter.install()
            self.assertIs(handlers.async_get_devices, first)
            result = await handlers.async_get_devices(
                self.hass, N(should_expose=lambda _: True)
            )
            self.assertEqual([item.id for item in result], [self.light.entity_id])
            self.runtime.close()
            self.assertIs(handlers.async_get_devices, original)

    async def test_discovery_adapter_rejects_incompatible_api_without_mutating_it(self):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        handlers.async_get_devices = lambda: []
        original = handlers.async_get_devices
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            adapter = DiscoveryAdapter(self.runtime)
            with self.assertLogs(
                "custom_components.wled_yaha.yaha_adapter", level="WARNING"
            ):
                adapter.install()
            self.assertEqual(adapter.status, "unsupported")
            self.assertIs(handlers.async_get_devices, original)
            adapter.close()

    async def test_discovery_adapter_never_removes_a_later_external_wrapper(self):
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        with patch.dict("sys.modules", {handlers.__name__: handlers}):
            adapter = DiscoveryAdapter(self.runtime)
            adapter.install()

            async def external(hass, data):
                return []

            handlers.async_get_devices = external
            adapter.close()
            self.assertIs(handlers.async_get_devices, external)

    async def test_discovery_adapter_attaches_after_late_yaha_setup(self):
        import sys
        from unittest.mock import patch

        from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter

        handlers = self.fake_yaha_handlers()
        with patch.dict(
            sys.modules,
            {
                handlers.__name__: None,
                "homeassistant.components.yandex_smart_home.handlers": None,
            },
        ):
            adapter = DiscoveryAdapter(self.runtime)
            adapter.install()
            self.assertEqual(adapter.status, "waiting")
            sys.modules[handlers.__name__] = handlers
            adapter.component_loaded(N(data={"component": "wled"}))
            self.assertEqual(adapter.status, "waiting")
            adapter.component_loaded(N(data={"component": "yandex_smart_home"}))
            self.assertEqual(adapter.status, "active")
            adapter.close()

    async def test_setup_failure_restores_yaha_provider(self):
        from unittest.mock import AsyncMock, patch

        from custom_components.wled_yaha import async_setup_entry

        handlers = self.fake_yaha_handlers()
        original = handlers.async_get_devices
        with (
            patch.dict("sys.modules", {handlers.__name__: handlers}),
            patch.object(
                ConfigEntries,
                "async_forward_entry_setups",
                AsyncMock(side_effect=RuntimeError("setup failed")),
            ),
            patch.object(
                ConfigEntries, "async_unload_platforms", AsyncMock(return_value=True)
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "setup failed"):
                await async_setup_entry(self.hass, self.entry)
            self.assertTrue(self.entry.runtime_data.closed)
            self.assertIs(handlers.async_get_devices, original)
