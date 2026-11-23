"""Registry discovery contracts using real HA modules and detached device data."""

import unittest
from types import SimpleNamespace as N
from unittest.mock import patch

from custom_components.wled_yaha import discovery as integration
from custom_components.wled_yaha.generator import validate_settings


def entry(entity_id, unique_id, device_id="d1", config_id="e1", **changes):
    values = dict(
        entity_id=entity_id,
        unique_id=unique_id,
        device_id=device_id,
        config_entry_id=config_id,
        platform="wled",
        name=None,
        area_id=None,
        disabled_by=None,
    )
    values.update(changes)
    return N(**values)


def make_hass(path, entries=None):
    entities = entries or [
        entry("light.renamed", "AA_0"),
        entry("select.custom_name", "AA_preset"),
    ]
    states = {
        e.entity_id: N(
            state="on" if e.entity_id.startswith("light.") else "Свеча",
            attributes={"friendly_name": "Гирлянда", "options": ["Preset 7", "Свеча"]},
        )
        for e in entities
    }
    device = N(name_by_user="Ёлка", name="WLED", area_id="bedroom")
    config = N(
        title="WLED",
        runtime_data=N(data=N(presets={1: N(name="Свеча"), 7: N(name="Preset 7")})),
    )

    async def executor(function, *args):
        return function(*args)

    return N(
        config=N(config_dir=path),
        entity_registry=N(entities={e.entity_id: e for e in entities}),
        device_registry=N(async_get=lambda device_id: device),
        area_registry=N(async_get_area=lambda area_id: N(name="Спальня")),
        config_entries=N(async_get_entry=lambda entry_id: config),
        states=N(get=states.get),
        services=N(has_service=lambda *args: False),
        async_add_executor_job=executor,
        fixture_states=states,
    )


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        for module, key in (
            (integration.er, "entity_registry"),
            (integration.dr, "device_registry"),
            (integration.ar, "area_registry"),
        ):
            context = patch.object(
                module, "async_get", lambda hass, attr=key: getattr(hass, attr)
            )
            context.start()
            self.addCleanup(context.stop)

    def test_renamed_entity_ids_are_matched_by_registry_unique_ids(self):
        snapshots, _, errors = integration.collect_snapshots(
            make_hass("unused"), validate_settings({})
        )
        self.assertFalse(errors)
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].preset_entity, "select.custom_name")
        self.assertEqual(snapshots[0].preset_ids, {1: "Свеча", 7: "Preset 7"})
        self.assertEqual(snapshots[0].room, "Спальня")

    def test_one_segment_preferred_over_optional_master(self):
        entries = [
            entry("light.master", "AA"),
            entry("light.segment", "AA_0"),
            entry("select.custom_name", "AA_preset"),
        ]
        snapshots, _, errors = integration.collect_snapshots(
            make_hass("unused", entries), validate_settings({})
        )
        self.assertFalse(errors)
        self.assertEqual([s.light for s in snapshots], ["light.segment"])

    def test_multiple_segments_use_master_with_explained_color_limitation(self):
        entries = [
            entry("light.master", "AA"),
            entry("light.a", "AA_0"),
            entry("light.b", "AA_1"),
            entry("select.x", "AA_preset"),
        ]
        snapshots, warnings, errors = integration.collect_snapshots(
            make_hass("unused", entries), validate_settings({})
        )
        self.assertFalse(errors)
        self.assertEqual([s.light for s in snapshots], ["light.master"])
        self.assertTrue(any("цвет" in w for w in warnings))

    def test_explicit_segment_selection_is_honored(self):
        entries = [
            entry("light.master", "AA"),
            entry("light.a", "AA_0"),
            entry("light.b", "AA_1"),
            entry("select.x", "AA_preset"),
        ]
        snapshots, _, errors = integration.collect_snapshots(
            make_hass("unused", entries),
            validate_settings({"include_entities": ["light.b"]}),
        )
        self.assertFalse(errors)
        self.assertEqual([s.light for s in snapshots], ["light.b"])

    def test_nonexistent_requested_entity_fails(self):
        _, _, errors = integration.collect_snapshots(
            make_hass("unused"), validate_settings({"include_entities": ["light.typo"]})
        )
        self.assertTrue(any("light.typo" in e for e in errors))

    def test_disabled_preset_entity_is_not_silently_lost(self):
        entries = [
            entry("light.x", "AA_0"),
            entry("select.x", "AA_preset", disabled_by="user"),
        ]
        snapshots, _, _ = integration.collect_snapshots(
            make_hass("unused", entries), validate_settings({})
        )
        self.assertTrue(any("отключённую" in e for e in snapshots[0].warnings))
