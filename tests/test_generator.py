"""Run with: python3 -m unittest discover -s tests -v."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

MODULE = (
    Path(__file__).resolve().parents[1] / "custom_components/wled_yaha/generator.py"
)
spec = importlib.util.spec_from_file_location("wled_yaha_generator", MODULE)
g = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = g
spec.loader.exec_module(g)


def device(**overrides):
    values = {
        "light": "light.garland",
        "name": "Гирлянда",
        "room": "Спальня",
        "preset_entity": "select.real_entity_name",
        "options": ["A — ночь", "Радуга", "Свеча"],
        "preset_ids": {7: "A — ночь", 1: "Свеча", 4: "Радуга"},
    }
    values.update(overrides)
    return g.DeviceSnapshot(**values)


class GenerationTests(unittest.TestCase):
    def export(self, snapshots=None, **settings):
        return g.build_export(
            [device()] if snapshots is None else snapshots,
            g.validate_settings(settings),
        )

    def test_real_ids_not_alphabetical_select_order(self):
        payload, reports, _, errors = self.export(
            preset_ids={"party": 7, "fantasy": None}
        )
        self.assertFalse(errors)
        self.assertEqual(
            payload["entity_config"]["light.garland"]["modes"]["scene"]["party"],
            "A — ночь",
        )
        self.assertEqual(reports[0]["presets"]["1"], "Свеча")

    def test_native_entity_ids_and_custom_scene_action(self):
        payload, _, _, errors = self.export()
        config = payload["entity_config"]["light.garland"]
        self.assertFalse(errors)
        self.assertEqual(config["type"], "light.garland")
        self.assertEqual(
            config["modes"]["scene"], {"candle": "Свеча", "fantasy": "Радуга"}
        )
        scene = config["custom_modes"]["scene"]
        self.assertEqual(scene["state_entity_id"], "select.real_entity_name")
        self.assertEqual(
            scene["set_mode"]["target"]["entity_id"], "select.real_entity_name"
        )
        self.assertEqual(scene["set_mode"]["data"]["option"], "{{ mode }}")

    def test_every_device_has_its_own_preset_target(self):
        other = device(
            light="light.kitchen", preset_entity="select.kitchen_preset", name="Кухня"
        )
        payload, _, _, errors = self.export(snapshots=[device(), other])
        self.assertFalse(errors)
        target = payload["entity_config"]["light.kitchen"]["custom_modes"]["scene"]
        self.assertEqual(target["state_entity_id"], "select.kitchen_preset")

    def test_per_device_overrides_and_independent_id_maps(self):
        payload, _, _, errors = self.export(
            preset_ids={"party": 7},
            devices={
                "light.garland": {
                    "name": "Ёлка",
                    "room": "Зал",
                    "preset_ids": {"party": None},
                }
            },
        )
        self.assertFalse(errors)
        config = payload["entity_config"]["light.garland"]
        self.assertEqual(config["name"], "Ёлка")
        self.assertEqual(config["room"], "Зал")
        self.assertNotIn("party", config["modes"]["scene"])

    def test_unknown_names_are_reported_not_assigned_arbitrarily(self):
        payload, reports, warnings, errors = self.export(
            snapshots=[
                device(
                    options=["Preset 1", "Preset 2"],
                    preset_ids={1: "Preset 1", 2: "Preset 2"},
                )
            ]
        )
        self.assertFalse(errors)
        self.assertTrue(warnings)
        self.assertEqual(reports[0]["unmapped_presets"], ["Preset 1", "Preset 2"])
        self.assertIs(
            payload["entity_config"]["light.garland"]["custom_modes"]["scene"], False
        )

    def test_missing_explicit_id_is_error(self):
        _, _, _, errors = self.export(preset_ids={"party": 25})
        self.assertTrue(any("ID 25" in e for e in errors))

    def test_duplicates_in_wled_names_are_error(self):
        _, _, _, errors = self.export(
            snapshots=[
                device(options=["Свеча", "Свеча"], preset_ids={1: "Свеча", 7: "Свеча"})
            ]
        )
        self.assertTrue(any("разные имена" in e for e in errors))

    def test_one_preset_cannot_report_two_yandex_scenes(self):
        _, _, _, errors = self.export(preset_ids={"party": 4})
        self.assertTrue(any("нескольким режимам" in e for e in errors))

    def test_missing_options_never_generates_invalid_select_option(self):
        _, _, _, errors = self.export(
            snapshots=[device(options=["Свеча"])], preset_ids={"party": 7}
        )
        self.assertTrue(any("options" in e for e in errors))

    def test_ambiguous_aliases_need_explicit_choice(self):
        payload, _, warnings, errors = self.export(
            snapshots=[
                device(
                    options=["Свеча", "Candle"], preset_ids={1: "Свеча", 2: "Candle"}
                )
            ]
        )
        self.assertFalse(errors)
        self.assertTrue(any("несколько пресетов" in w for w in warnings))
        self.assertIs(
            payload["entity_config"]["light.garland"]["custom_modes"]["scene"], False
        )

    def test_yaml_roundtrip_unicode_quotes_and_jinja(self):
        payload, _, _, _ = self.export(scene_map={"night": "A — ночь"})
        text = g.render_export(payload)
        self.assertTrue(text.startswith(g.MARKER))
        self.assertEqual(yaml.safe_load(text), payload)

    def test_empty_export_adds_no_global_exclusions(self):
        payload, _, _, errors = self.export(snapshots=[])
        self.assertFalse(errors)
        self.assertEqual(
            payload, {"filter": {"include_entities": []}, "entity_config": {}}
        )

    def test_regeneration_drops_old_entity(self):
        old, *_ = self.export()
        new, *_ = self.export(snapshots=[device(light="light.renamed")])
        self.assertIn("light.garland", old["entity_config"])
        self.assertNotIn("light.garland", new["entity_config"])
        self.assertEqual(new["filter"]["include_entities"], ["light.renamed"])

    def test_invalid_configs_fail(self):
        bad = [
            [],
            False,
            {"typo": True},
            {"preset_ids": {"party": True}},
            {"preset_ids": {"party": 0}},
            {"preset_ids": {"party": 251}},
            {"scene_map": {"custom_mode": "Свеча"}},
            {"scene_map": {"party": 42}},
            {"include_entities": "light.a"},
            {"include_entities": ["select.x"]},
            {"devices": {"light.x": {"bad": True}}},
        ]
        for config in bad:
            with self.subTest(config=config), self.assertRaises(ValueError):
                g.validate_settings(config)


class WriteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.text = g.render_export(
            {"filter": {"include_entities": []}, "entity_config": {}}
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_atomic_write_idempotence_and_regeneration(self):
        self.assertTrue(g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text))
        self.assertFalse(g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text))
        self.assertTrue(
            g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text + "# changed\n")
        )
        self.assertEqual(list((self.root / "includes").glob(".wled_yaha_*")), [])

    def test_does_not_overwrite_manual_file(self):
        target = self.root / g.DEFAULT_OUTPUT
        target.parent.mkdir()
        target.write_text("manual: true\n")
        with self.assertRaises(ValueError):
            g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text)
        self.assertEqual(target.read_text(), "manual: true\n")

    def test_rejects_escape_and_non_yaml(self):
        for path in (
            "../else.yaml",
            "/tmp/out.yaml",
            "configuration.yaml",
            "includes/../config.yaml",
            "includes/a.py",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                g.write_export(str(self.root), path, self.text)

    def test_rejects_symlink_parent(self):
        (self.root / "outside").mkdir()
        (self.root / "includes").symlink_to(
            self.root / "outside", target_is_directory=True
        )
        with self.assertRaises(ValueError):
            g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text)

    def test_rejects_symlink_file(self):
        (self.root / "includes").mkdir()
        other = self.root / "other.yaml"
        other.write_text("manual: true\n")
        (self.root / g.DEFAULT_OUTPUT).symlink_to(other)
        with self.assertRaises(ValueError):
            g.write_export(str(self.root), g.DEFAULT_OUTPUT, self.text)
        self.assertEqual(other.read_text(), "manual: true\n")


if __name__ == "__main__":
    unittest.main()
