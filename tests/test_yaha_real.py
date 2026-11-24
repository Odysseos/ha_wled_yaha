"""Optional end-to-end tests with the pinned, unmodified YaHA source.

Run: YAHA_TEST_SOURCE=/absolute/path/to/yandex_smart_home python ...unittest...
The normal suite does not download or vendor another custom integration.
"""

import json
import os
import unittest
from pathlib import Path

import custom_components
import test_integration as fixtures
import yaml
from custom_components.wled_yaha import build_preview
from custom_components.wled_yaha.yaha_adapter import DiscoveryAdapter
from homeassistant.core import Context
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entityfilter import FILTER_SCHEMA

SOURCE = os.environ.get("YAHA_TEST_SOURCE")

if SOURCE:
    custom_components.__path__.append(str(Path(SOURCE) / "custom_components"))
    from custom_components.yandex_smart_home import handlers
    from custom_components.yandex_smart_home.config_schema import (
        YANDEX_SMART_HOME_SCHEMA,
    )
    from custom_components.yandex_smart_home.const import EntityFilterSource
    from custom_components.yandex_smart_home.entry_data import ConfigEntryData
    from custom_components.yandex_smart_home.helpers import (
        CacheStore,
        RequestData,
        SmartHomePlatform,
    )
    from custom_components.yandex_smart_home.schema import ResponseCode

    class TestEntryData(ConfigEntryData):
        @property
        def is_reporting_states(self):
            return False


@unittest.skipUnless(SOURCE, "Set YAHA_TEST_SOURCE to exercise actual YaHA handlers")
class RealYahaTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.IntegrationTests.asyncSetUp
    asyncTearDown = fixtures.IntegrationTests.asyncTearDown

    def data(self, config=None):
        config = YANDEX_SMART_HOME_SCHEMA(
            config or yaml.safe_load(build_preview(self.hass, self.options)["yaml"])
        )
        entry = fixtures.config_entry("yandex_smart_home")
        self.hass.config_entries._entries[entry.entry_id] = entry
        self.hass.config_entries.async_update_entry(
            entry, options={"filter_source": EntityFilterSource.YAML}
        )
        entry_data = TestEntryData(
            self.hass,
            entry,
            yaml_config=config,
            entity_config=config.get("entity_config"),
            entity_filter=FILTER_SCHEMA(config["filter"]),
        )
        entry_data.cache = CacheStore(self.hass)
        entry_data._entity_registry = er.async_get(self.hass)
        return RequestData(
            entry_data=entry_data,
            context=Context(),
            platform=SmartHomePlatform.YANDEX,
            request_user_id="fixture",
            request_id="fixture",
        )

    def attach(self):
        self.runtime.discovery = DiscoveryAdapter(self.runtime)
        self.runtime.discovery.install()
        self.assertEqual(self.runtime.discovery.status, "active")

    async def test_actual_yaha_list_includes_offline_light_and_preserves_identity_room(
        self,
    ):
        self.hass.states.async_set(
            self.light.entity_id, "unavailable", {"supported_color_modes": ["rgb"]}
        )
        request = self.data()
        before = await handlers.async_device_list(self.hass, request, "{}")
        self.assertEqual(before.devices, [])
        self.attach()
        result = await handlers.async_device_list(self.hass, request, "{}")
        self.assertEqual(
            [device.id for device in result.devices], [self.light.entity_id]
        )
        self.assertEqual(result.devices[0].room, "Спальня")
        self.assertEqual(result.devices[0].type.value, "devices.types.light.garland")
        self.assertTrue(result.devices[0].capabilities)
        self.assertEqual(
            self.hass.states.get(self.light.entity_id).state, "unavailable"
        )

    async def test_actual_yaha_query_and_action_still_report_unreachable(self):
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        request = self.data()
        self.attach()
        query = await handlers.async_devices_query(
            self.hass, request, json.dumps({"devices": [{"id": self.light.entity_id}]})
        )
        self.assertEqual(query.devices[0].error_code, ResponseCode.DEVICE_UNREACHABLE)
        action = await handlers.async_devices_action(
            self.hass,
            request,
            json.dumps(
                {
                    "payload": {
                        "devices": [
                            {
                                "id": self.light.entity_id,
                                "capabilities": [
                                    {
                                        "type": "devices.capabilities.on_off",
                                        "state": {"instance": "on", "value": True},
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
        )
        self.assertEqual(
            action.devices[0].action_result.error_code, ResponseCode.DEVICE_UNREACHABLE
        )
        self.assertEqual(
            self.hass.states.get(self.light.entity_id).state, "unavailable"
        )

    async def test_actual_yaha_five_offline_lights_are_all_registered(self):
        registry = er.async_get(self.hass)
        for index in range(2, 6):
            light = registry.async_get_or_create(
                "light",
                "wled",
                "AA_" + str(index),
                config_entry=self.wled,
                device_id=self.light.device_id,
                suggested_object_id="tree_" + str(index),
            )
            self.options["lights"].append(
                {"registry_id": light.id, "entity_id": light.entity_id, "scenes": {}}
            )
        for record in self.options["lights"]:
            self.hass.states.async_set(record["entity_id"], "unavailable")
        self.attach()
        result = await handlers.async_device_list(self.hass, self.data(), "{}")
        self.assertEqual(
            {device.id for device in result.devices},
            {record["entity_id"] for record in self.options["lights"]},
        )
        self.assertEqual(len(result.devices), 5)

    async def test_actual_yaha_missing_state_uses_registry_without_fabricating_color(
        self,
    ):
        self.hass.states.async_remove(self.light.entity_id)
        self.attach()
        result = await handlers.async_device_list(self.hass, self.data(), "{}")
        self.assertEqual(
            [device.id for device in result.devices], [self.light.entity_id]
        )
        self.assertIsNone(self.hass.states.get(self.light.entity_id))
        self.assertIn(
            "devices.capabilities.on_off",
            {c.type.value for c in result.devices[0].capabilities},
        )
        self.assertNotIn(
            "devices.capabilities.range",
            {c.type.value for c in result.devices[0].capabilities},
        )

    async def test_actual_yaha_reconnect_reuses_original_entity_without_duplicates(
        self,
    ):
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        request = self.data()
        self.attach()
        offline = await handlers.async_device_list(self.hass, request, "{}")
        self.hass.states.async_set(
            self.light.entity_id,
            "on",
            {"supported_color_modes": ["rgb"], "brightness": 128},
        )
        online = await handlers.async_device_list(self.hass, request, "{}")
        self.assertEqual(
            [device.id for device in online.devices],
            [device.id for device in offline.devices],
        )
        query = await handlers.async_devices_query(
            self.hass, request, json.dumps({"devices": [{"id": self.light.entity_id}]})
        )
        self.assertIsNone(query.devices[0].error_code)

    async def test_actual_yaha_filters_and_adapter_unload_are_preserved(self):
        self.hass.states.async_set(self.light.entity_id, "unavailable")
        request = self.data()
        self.attach()
        original_result = await handlers.async_device_list(self.hass, request, "{}")
        self.assertEqual(len(original_result.devices), 1)
        excluded = self.data({"filter": {"include_entities": []}, "entity_config": {}})
        self.assertEqual(
            (await handlers.async_device_list(self.hass, excluded, "{}")).devices, []
        )
        self.runtime.discovery.close()
        self.assertEqual(
            (await handlers.async_device_list(self.hass, request, "{}")).devices, []
        )
