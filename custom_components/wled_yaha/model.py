"""Persistent UI selections, resolved through registry identities on each export."""

from __future__ import annotations

from homeassistant.helpers import entity_registry as er

from .discovery import collect_snapshots
from .generator import ALIASES, resolve_scenes, validate_settings

DEFAULT_OPTIONS = {"auto_export": True, "connect_yaha": True}


def resolve_entity(hass, record):
    """Follow entity renames; do not bind a replacement entity by its old name."""
    registry = er.async_get(hass)
    if registry_id := record.get("registry_id"):
        return next(
            (e for e in registry.entities.values() if e.id == registry_id), None
        )
    return registry.async_get(record["entity_id"])


def settings_for_records(hass, records):
    settings = {"include_entities": [], "devices": {}}
    missing = []
    for record in records:
        entity = resolve_entity(hass, record)
        if (
            entity is None
            or entity.platform != "wled"
            or entity.disabled_by is not None
        ):
            missing.append(
                f"{record['entity_id']}: выбранная сущность удалена или отключена."
            )
            continue
        settings["include_entities"].append(entity.entity_id)
        part = {"preset_ids": {}, "scene_map": {}}
        for scene in ALIASES:
            choice = record.get("scenes", {}).get(scene)
            if isinstance(choice, int):
                part["preset_ids"][scene] = choice
            elif isinstance(choice, str):
                part["scene_map"][scene] = [choice]
            else:
                part["preset_ids"][scene] = None
        settings["devices"][entity.entity_id] = part
    return validate_settings(settings), missing


def snapshots_for_records(hass, records, previous_scenes=None):
    settings, missing = settings_for_records(hass, records)
    # The generator's empty include list means auto discovery. UI empty means none.
    if not settings["include_entities"]:
        return settings, [], [], missing
    snapshots, warnings, errors = collect_snapshots(hass, settings)
    by_entity = {s.light: s for s in snapshots}
    ordered = []
    for record in records:
        entity = resolve_entity(hass, record)
        if entity is None or (snapshot := by_entity.get(entity.entity_id)) is None:
            continue
        if not snapshot.online or not snapshot.preset_online:
            saved = {
                **(previous_scenes or {}).get(record["entity_id"], {}),
                **(previous_scenes or {}).get(entity.entity_id, {}),
                **record.get("scene_names", {}),
            }
            for scene, choice in record.get("scenes", {}).items():
                name = saved.get(scene)
                if type(choice) is int and name and choice not in snapshot.preset_ids:
                    snapshot.preset_ids[choice] = name
                if isinstance(choice, str):
                    name = choice
                elif type(choice) is int:
                    name = snapshot.preset_ids.get(choice)
                if name and name not in snapshot.options:
                    snapshot.options.append(name)
        ordered.append(snapshot)
    return settings, ordered, warnings, missing + errors


def default_scenes(snapshot):
    """Suggest recognisable names for review; preserve actual preset numbers."""
    scenes, _, _ = resolve_scenes(snapshot, ALIASES, {})
    ids = {name: key for key, name in snapshot.preset_ids.items()}
    return {scene: ids.get(name, name) for scene, name in scenes.items()}


def config_options(entry):
    return {**DEFAULT_OPTIONS, **entry.data, **entry.options}
