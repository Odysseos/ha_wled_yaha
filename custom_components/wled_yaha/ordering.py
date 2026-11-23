"""One explicit display order shared by the picker, scene wizard and summary."""

from __future__ import annotations

import re
import unicodedata

from homeassistant.helpers import (
    area_registry as ar,
)
from homeassistant.helpers import (
    device_registry as dr,
)
from homeassistant.helpers import (
    entity_registry as er,
)


def natural_key(value):
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return tuple(
        (1, int(part)) if part.isdecimal() else (0, part)
        for part in re.split(r"(\d+)", value)
        if part
    )


def light_choices(hass):
    devices, areas = dr.async_get(hass), ar.async_get(hass)
    result = []
    for entity in er.async_get(hass).entities.values():
        if (
            entity.platform != "wled"
            or not entity.entity_id.startswith("light.")
            or entity.disabled_by is not None
        ):
            continue
        device = devices.async_get(entity.device_id) if entity.device_id else None
        state = hass.states.get(entity.entity_id)
        name = (
            entity.name
            or (state.attributes.get("friendly_name") if state else None)
            or (getattr(device, "name_by_user", None) if device else None)
            or getattr(entity, "original_name", None)
            or (getattr(device, "name", None) if device else None)
            or entity.entity_id
        )
        area_id = entity.area_id or (
            getattr(device, "area_id", None) if device else None
        )
        area = areas.async_get_area(area_id) if area_id else None
        room = area.name if area else ""
        label = " · ".join(part for part in (name, room, entity.entity_id) if part)
        result.append((name, room, entity.entity_id, label))
    result.sort(
        key=lambda item: (
            natural_key(item[0]),
            natural_key(item[1]),
            natural_key(item[2]),
        )
    )
    return [{"value": entity_id, "label": label} for _, _, entity_id, label in result]
