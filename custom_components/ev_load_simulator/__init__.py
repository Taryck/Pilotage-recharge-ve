"""EV parking simulation with a closed-loop electrical model."""
from __future__ import annotations

from datetime import timedelta
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, PLATFORMS
from .model import Parking

class SimulationRuntime:
    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        d = {**entry.data, **entry.options}
        self.parking = Parking(
            count=int(d["count"]),
            factor=float(d["factor"]),
            trip_seconds=float(d["trip_seconds"]),
            thresholds={p: float(d[f"threshold_{p}"]) for p in ("l1", "l2", "l3")},
            seed=int(d["seed"]),
        )
        self.scenario_id = "none"
        self.listeners = set()
        self.unsubscribe = None

    def subscribe(self, callback):
        self.listeners.add(callback)
        return lambda: self.listeners.discard(callback)

    def notify(self):
        for callback in list(self.listeners):
            callback()

    def on_tick(self, now):
        self.parking.tick(1.0)
        self.notify()

async def async_setup_entry(hass: HomeAssistant, entry) -> bool:
    runtime = SimulationRuntime(hass, entry)
    entry.runtime_data = runtime
    entry.async_on_unload(entry.add_update_listener(_options_changed))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    runtime.unsubscribe = async_track_time_interval(
        hass, runtime.on_tick, timedelta(seconds=1))
    return True

async def _options_changed(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)

async def async_unload_entry(hass, entry):
    if entry.runtime_data.unsubscribe:
        entry.runtime_data.unsubscribe()
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
