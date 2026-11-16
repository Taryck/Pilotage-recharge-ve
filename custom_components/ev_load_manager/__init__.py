"""EV Load Manager Home Assistant integration."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .controller import LoadController

PLATFORMS = ["sensor"]
STORAGE_VERSION = 1


@dataclass
class Runtime:
    store: Store
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    controller: LoadController | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
    saved = await store.async_load()
    runtime = Runtime(store=store, snapshots=(saved or {}).get("snapshots", {}))
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    runtime.controller = LoadController(hass, entry)
    await runtime.controller.async_start()
    return True


async def _options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    runtime = entry.runtime_data
    if runtime.controller:
        await runtime.controller.async_stop()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
