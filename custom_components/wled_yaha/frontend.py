"""Register the HA admin panel following Entity ID Manager's panel layout."""

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig

from .generator import DOMAIN, VERSION

PANEL = "wled-yaha"


async def async_register(hass):
    if not hass.data.get(DOMAIN + "_static"):
        await hass.http.async_register_static_paths(
            [
                StaticPathConfig(
                    f"/{DOMAIN}/editor.js",
                    str(Path(__file__).parent / "www/editor.js"),
                    False,
                )
            ]
        )
        hass.data[DOMAIN + "_static"] = True
    if not frontend.async_panel_exists(hass, PANEL):
        await panel_custom.async_register_panel(
            hass,
            frontend_url_path=PANEL,
            webcomponent_name="wled-yaha-panel",
            sidebar_title="WLED YaHA",
            sidebar_icon="mdi:string-lights",
            module_url=f"/{DOMAIN}/editor.js?v={VERSION}",
            embed_iframe=False,
            require_admin=True,
            config_panel_domain=DOMAIN,
        )


def unregister(hass):
    if frontend.async_panel_exists(hass, PANEL):
        frontend.async_remove_panel(hass, PANEL)
