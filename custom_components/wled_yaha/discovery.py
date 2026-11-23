"""Read native WLED entities and cached preset IDs."""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .generator import ALIASES, DeviceSnapshot


def collect_snapshots(
    hass: HomeAssistant, settings: dict[str, Any]
) -> tuple[list[DeviceSnapshot], list[str], list[str]]:
    """Read the native registries and cached WLED data on the event loop."""
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    groups: dict[tuple[str | None, str | None], list[Any]] = defaultdict(list)
    for entry in entity_registry.entities.values():
        if entry.platform == "wled":
            groups[(entry.config_entry_id, entry.device_id)].append(entry)

    requested = set(settings["include_entities"])
    excluded = set(settings["exclude_entities"])
    found: set[str] = set()
    snapshots: list[DeviceSnapshot] = []
    warnings: list[str] = []
    errors: list[str] = []
    for (config_id, device_id), entries in sorted(
        groups.items(), key=lambda item: str(item[0])
    ):
        lights = [
            e
            for e in entries
            if e.entity_id.startswith("light.") and e.disabled_by is None
        ]
        found.update(e.entity_id for e in lights)
        lights = [e for e in lights if e.entity_id not in excluded]
        if requested:
            selected = [e for e in lights if e.entity_id in requested]
        elif len(lights) <= 1:
            selected = lights
        else:
            # Native WLED uses MAC for the master and MAC_segmentId for segments.
            segments = [e for e in lights if re.search(r"_\d+$", e.unique_id)]
            masters = [e for e in lights if not re.search(r"_\d+$", e.unique_id)]
            if len(segments) == 1:
                selected = segments
            elif len(masters) == 1:
                selected = masters
                warnings.append(
                    f"{masters[0].entity_id}: несколько сегментов; выбран общий свет. "
                    "Он управляет питанием, общей яркостью и пресетами; цвет сегментов "
                    "напрямую недоступен. Для отдельных сегментов задай include_entities."
                )
            else:
                errors.append(
                    "Нельзя однозначно выбрать свет WLED: "
                    + ", ".join(e.entity_id for e in lights)
                    + ". Укажи include_entities."
                )
                continue
        if not selected:
            continue

        device = device_registry.async_get(device_id) if device_id else None
        config_entry = (
            hass.config_entries.async_get_entry(config_id) if config_id else None
        )
        runtime = getattr(config_entry, "runtime_data", None)
        data = getattr(runtime, "data", None)
        raw_presets = getattr(data, "presets", None)
        ids: dict[int, str] = {}
        if isinstance(raw_presets, dict):
            for key, preset in raw_presets.items():
                name = getattr(preset, "name", None)
                if isinstance(name, str) and str(key).isdigit():
                    ids[int(key)] = name

        presets = [
            e
            for e in entries
            if e.entity_id.startswith("select.") and e.unique_id.endswith("_preset")
        ]
        preset_entry = (
            presets[0] if len(presets) == 1 and presets[0].disabled_by is None else None
        )
        preset_state = hass.states.get(preset_entry.entity_id) if preset_entry else None
        # IDs come from coordinator.data.presets. HA sorts select options by NAME.
        options = (
            list(preset_state.attributes.get("options", []))
            if preset_state is not None
            else sorted(ids.values())
        )
        options = [n for n in options if isinstance(n, str)]

        for light in sorted(selected, key=lambda e: e.entity_id):
            state = hass.states.get(light.entity_id)
            name = (
                light.name
                or (getattr(device, "name_by_user", None) if device else None)
                or (state.attributes.get("friendly_name") if state else None)
                or (getattr(device, "name", None) if device else None)
                or (config_entry.title if config_entry else light.entity_id)
            )
            area_id = light.area_id or (
                getattr(device, "area_id", None) if device else None
            )
            area = area_registry.async_get_area(area_id) if area_id else None
            snapshot = DeviceSnapshot(
                light=light.entity_id,
                name=name,
                room=area.name if area else None,
                preset_entity=preset_entry.entity_id if preset_entry else None,
                options=options,
                preset_ids=ids,
            )
            if state is None or state.state in {"unknown", "unavailable"}:
                snapshot.errors.append(
                    f"{light.entity_id}: устройство ещё не загружено или недоступно. "
                    "Файл не будет перезаписан, чтобы не потерять его режимы."
                )
            chosen_ids = {
                **settings.get("preset_ids", {}),
                **settings.get("devices", {})
                .get(light.entity_id, {})
                .get("preset_ids", {}),
            }
            scenes_disabled = all(
                scene in chosen_ids and chosen_ids[scene] is None for scene in ALIASES
            )
            # An unavailable preset selector must not block ordinary light control
            # when the user has explicitly left every scene disabled.
            preset_problems = snapshot.warnings if scenes_disabled else snapshot.errors
            if len(presets) > 1:
                preset_problems.append(
                    f"{light.entity_id}: найдено несколько сущностей выбора пресетов."
                )
            elif presets and preset_entry is None:
                preset_problems.append(
                    f"{light.entity_id}: включи отключённую сущность {presets[0].entity_id}."
                )
            elif preset_entry is None:
                if ids:
                    preset_problems.append(
                        f"{light.entity_id}: пресеты есть, но не найдена их select-сущность."
                    )
                else:
                    snapshot.warnings.append(
                        f"{light.entity_id}: нет select пресетов; будут только функции света."
                    )
            elif preset_state is None:
                preset_problems.append(
                    f"{preset_entry.entity_id}: сущность пока не загружена."
                )
            elif preset_state.state == "unavailable" and (
                options or raw_presets is None
            ):
                preset_problems.append(
                    f"{preset_entry.entity_id}: выбор пресетов недоступен."
                )
            snapshots.append(snapshot)

    if missing := requested - found:
        errors.append(
            "Не найдены включённые WLED-светильники: " + ", ".join(sorted(missing))
        )
    selected_ids = {s.light for s in snapshots}
    if unused := set(settings["devices"]) - selected_ids:
        warnings.append(
            "Не использованы настройки devices: " + ", ".join(sorted(unused))
        )
    return snapshots, warnings, errors
