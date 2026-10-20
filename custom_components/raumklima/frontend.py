"""Serve and register the Raumklima Lovelace card."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from homeassistant.components.http import StaticPathConfig
from homeassistant.core import (
    CoreState,
    EVENT_HOMEASSISTANT_STARTED,
    HomeAssistant,
)

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
_CARD_PATH = "/raumklima/raumklima-card.js"
_CARD_VERSION = json.loads(
    (Path(__file__).parent / "manifest.json").read_text(encoding="utf-8")
)["version"]


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve the card and add it to storage-mode Lovelace resources."""
    frontend_path = Path(__file__).parent / "frontend"
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_CARD_PATH, str(frontend_path / "raumklima-card.js"), False)]
        )
    except RuntimeError:
        _LOGGER.debug("Raumklima frontend path is already registered")

    async def register_lovelace_resource(event=None) -> None:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None:
            _LOGGER.debug("Lovelace is not available; Raumklima card resource skipped")
            return

        # In YAML resource mode resources are read-only and must be declared in YAML.
        if not hasattr(resources, "async_create_item"):
            _LOGGER.info(
                "Lovelace uses YAML resources; add %s as a module resource manually",
                _CARD_PATH,
            )
            return

        # Ensure storage resources have loaded before inspecting or changing them.
        await resources.async_get_info()
        resource_url = f"{_CARD_PATH}?v={_CARD_VERSION}"
        for resource in resources.async_items():
            if resource["url"].split("?", 1)[0] == _CARD_PATH:
                if resource.get("url") != resource_url or resource.get("type") != "module":
                    await resources.async_update_item(
                        resource["id"],
                        {"url": resource_url, "res_type": "module"},
                    )
                return

        await resources.async_create_item(
            {"url": resource_url, "res_type": "module"}
        )
        _LOGGER.info("Registered the Raumklima Lovelace card")

    if hass.state is CoreState.running:
        await register_lovelace_resource()
    else:
        hass.bus.async_listen_once(
            EVENT_HOMEASSISTANT_STARTED, register_lovelace_resource
        )
