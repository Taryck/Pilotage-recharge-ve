"""EV Load Manager Home Assistant integration."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .controller import LoadController

PLATFORMS = ["sensor"]
STORAGE_VERSION = 1
_LOGGER = logging.getLogger(__name__)


@dataclass
class Runtime:
    store: Store
    snapshots: dict[str, dict[str, Any]] = field(default_factory=dict)
    controller: LoadController | None = None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_{entry.entry_id}")
    try:
        saved = await store.async_load()
        if saved is None:
            snapshots = {}
        elif not isinstance(saved, dict) or not isinstance(saved.get("snapshots", {}), dict):
            raise ValueError("Invalid snapshot storage structure")
        else:
            snapshots = saved.get("snapshots", {})
            if not all(
                isinstance(key, str)
                and isinstance(value, dict)
                and (
                    value.get("initial_current_a") is None
                    or (
                        isinstance(value["initial_current_a"], (int, float))
                        and 0 <= value["initial_current_a"] <= 10000
                    )
                )
                for key, value in snapshots.items()
            ):
                raise ValueError("Invalid saved charger snapshot")
    except Exception as exc:
        _LOGGER.exception("Could not read saved charge settings")
        # Never silently discard recovery settings on a corrupted store.
        raise ConfigEntryNotReady("Saved charge settings could not be read") from exc
    runtime = Runtime(store=store, snapshots=snapshots)
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
