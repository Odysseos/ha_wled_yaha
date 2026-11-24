"""Configuration and last export report, without WLED credentials."""

from . import build_preview
from .model import config_options


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = getattr(entry, "runtime_data", None)
    return {
        "settings": config_options(entry),
        "current": build_preview(hass, config_options(entry)),
        "last_export": runtime.result if runtime else None,
        "yaha_discovery": runtime.discovery.diagnostics()
        if runtime and runtime.discovery
        else None,
    }
