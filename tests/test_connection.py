"""Exercise real YAML source editing and backups with representative HA layouts."""

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wled_connection_test",
    ROOT / "custom_components/wled_yaha/connection.py",
    submodule_search_locations=[str(ROOT / "custom_components/wled_yaha")],
)
c = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = c
spec.loader.exec_module(c)


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "configuration.yaml"

    def apply(self, text):
        self.config.write_text(text)
        plan = c.plan_connection(str(self.root))
        c.apply_connection(plan)
        yaml.compose(self.config.read_text())
        self.assertTrue(c.plan_connection(str(self.root)).connected)
        return plan

    def test_existing_vakio_packages_and_comments_survive(self):
        text = "# Config\nhomeassistant:\n  name: Дом\n  packages:\n    yaha_other:\n      yandex_smart_home: !include includes/yandex_smart_home.yaml\n    yaha_vakio:\n      yandex_smart_home: !include includes/yandex_smart_home_vakio.yaml\n# untouched\nhttp:\n  api_password: !secret password\n"
        self.apply(text)
        after = self.config.read_text()
        self.assertIn(
            "    yaha_vakio:\n      yandex_smart_home: !include includes/yandex_smart_home_vakio.yaml",
            after,
        )
        self.assertIn("api_password: !secret password", after)
        self.assertEqual(
            self.config.with_name("configuration.yaml.wled-yaha.bak").read_text(), text
        )
        self.assertFalse(c.apply_connection(c.plan_connection(str(self.root))))

    def test_missing_homeassistant(self):
        self.apply("default_config:\nlogger:\n  default: warning\n")

    def test_empty_configuration(self):
        self.apply("")

    def test_homeassistant_without_packages(self):
        self.apply(
            "homeassistant:\n  name: Дом\n  latitude: !secret latitude\nlogger:\n  default: warning\n"
        )

    def test_empty_flow_mappings(self):
        for text in ["homeassistant: {}\n", "homeassistant:\n  packages: {}\n"]:
            with self.subTest(text=text):
                backup = self.config.with_name("configuration.yaml.wled-yaha.bak")
                backup.unlink(missing_ok=True)
                self.apply(text)

    def test_packages_file_relative_include(self):
        self.config.write_text(
            "homeassistant:\n  packages: !include includes/packages.yaml\n"
        )
        (self.root / "includes").mkdir()
        path = self.root / "includes/packages.yaml"
        original = (
            "yaha_vakio:\n  yandex_smart_home: !include yandex_smart_home_vakio.yaml\n"
        )
        path.write_text(original)
        plan = c.plan_connection(str(self.root))
        c.apply_connection(plan)
        self.assertIn("!include yandex_smart_home_wled.yaml", path.read_text())
        self.assertIn(original, path.read_text())
        self.assertTrue(c.plan_connection(str(self.root)).connected)

    def test_include_dir_named_and_merge_named(self):
        for tag in ("!include_dir_named", "!include_dir_merge_named"):
            with self.subTest(tag=tag):
                folder = self.root / tag[1:]
                folder.mkdir()
                self.config.write_text(
                    f"homeassistant:\n  packages: {tag} {folder.name}\n"
                )
                plan = c.plan_connection(str(self.root))
                c.apply_connection(plan)
                self.assertEqual(plan.target.parent, folder)
                self.assertTrue(plan.target.read_text().startswith(c.PACKAGE_MARKER))
                self.assertTrue(c.plan_connection(str(self.root)).connected)

    def test_homeassistant_include_file(self):
        self.config.write_text("homeassistant: !include includes/home.yaml\n")
        (self.root / "includes").mkdir()
        (self.root / "includes/home.yaml").write_text("name: Дом\n")
        plan = c.plan_connection(str(self.root))
        c.apply_connection(plan)
        self.assertTrue(c.plan_connection(str(self.root)).connected)
        self.assertIn("!include yandex_smart_home_wled.yaml", plan.target.read_text())

    def test_conflicting_package_is_not_overwritten(self):
        original = "homeassistant:\n  packages:\n    wled_yaha_generated:\n      yandex_smart_home: !include manual.yaml\n"
        self.config.write_text(original)
        with self.assertRaises(ValueError):
            c.plan_connection(str(self.root))
        self.assertEqual(self.config.read_text(), original)

    def test_concurrent_edit_is_preserved(self):
        self.config.write_text("default_config:\n")
        plan = c.plan_connection(str(self.root))
        self.config.write_text("default_config:\n# new edit\n")
        with self.assertRaises(ValueError):
            c.apply_connection(plan)
        self.assertIn("# new edit", self.config.read_text())

    def test_symlink_and_external_include_rejected(self):
        self.config.write_text("homeassistant: !include ../outside.yaml\n")
        with self.assertRaises(ValueError):
            c.plan_connection(str(self.root))

    def test_duplicate_keys_and_merge_not_silently_changed(self):
        for text in (
            "homeassistant: {}\nhomeassistant: {}\n",
            "homeassistant:\n  packages:\n    <<: {}\n",
        ):
            self.config.write_text(text)
            with self.assertRaises(ValueError):
                c.plan_connection(str(self.root))
