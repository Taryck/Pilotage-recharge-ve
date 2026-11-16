"""Home Assistant adapter for the one-step-at-a-time EV load controller."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from math import isfinite

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_CONTROL, CONF_CURRENT_PREFIX, CONF_MAX, CONF_MIN, CONF_SETPOINT,
    CONF_START, CONF_STOP, CONF_THRESHOLD_PREFIX, PHASES, SUBENTRY_TYPE_CHARGER,
)
from .planner import Charger, decide
from .sensor import current_in_amp

_LOGGER = logging.getLogger(__name__)
ACK_TIMEOUT = 12
POLL_SECONDS = 5
COOLDOWN_SECONDS = 5


def _number(hass: HomeAssistant, entity_id: str) -> float | None:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        value = float(state.state)
        return value if isfinite(value) else None
    except (ValueError, TypeError):
        return None


class LoadController:
    """Serialize adjustments and verify that each charger acknowledges them."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass, self.entry = hass, entry
        self.runtime = entry.runtime_data
        self.lock = asyncio.Lock()
        self.remove_callbacks = []
        self._last_action = 0.0
        self._last_problem = None

    async def async_start(self) -> None:
        ids = []
        d = {**self.entry.data, **self.entry.options}
        for phase in PHASES:
            ids.append(d[f"{CONF_CURRENT_PREFIX}{phase}"])
        for sub in self._subs():
            ids += [sub.data.get(CONF_SETPOINT), sub.data.get(CONF_CONTROL)]
            ids += [sub.data.get(f"{CONF_CURRENT_PREFIX}{p}") for p in PHASES]
        ids = list(set(filter(None, ids)))
        if ids:
            self.remove_callbacks.append(
                async_track_state_change_event(self.hass, ids, self._changed)
            )
        self.remove_callbacks.append(
            async_track_time_interval(self.hass, self._periodic, timedelta(seconds=POLL_SECONDS))
        )

    async def async_stop(self) -> None:
        for remove in self.remove_callbacks:
            remove()
        self.remove_callbacks.clear()
        async with self.lock:
            pass

    def _subs(self):
        return [sub for sub in self.entry.subentries.values()
                if sub.subentry_type == SUBENTRY_TYPE_CHARGER]

    @callback
    def _changed(self, event) -> None:
        self.hass.async_create_task(self.async_evaluate())

    @callback
    def _periodic(self, now) -> None:
        self.hass.async_create_task(self.async_evaluate())

    def _state(self):
        data = {**self.entry.data, **self.entry.options}
        totals = {
            p: current_in_amp(self.hass, data[f"{CONF_CURRENT_PREFIX}{p}"])
            for p in PHASES
        }
        limits = {p: float(data[f"{CONF_THRESHOLD_PREFIX}{p}"]) for p in PHASES}
        chargers = []
        for sub in self._subs():
            d = sub.data
            currents = {}
            for p in PHASES:
                entity = d.get(f"{CONF_CURRENT_PREFIX}{p}")
                currents[p] = current_in_amp(self.hass, entity) if entity else 0.0
            control = self.hass.states.get(d[CONF_CONTROL])
            is_active = control is not None and control.state == d[CONF_START]
            snapshot = self.runtime.snapshots.get(sub.subentry_id, {})
            chargers.append(Charger(
                identifier=sub.subentry_id,
                currents=currents,
                setpoint=_number(self.hass, d[CONF_SETPOINT]),
                minimum=float(d[CONF_MIN]),
                maximum=float(d[CONF_MAX]),
                original=snapshot.get("initial_current_a"),
                active=is_active,
            ))
        return totals, limits, chargers

    async def _persist(self):
        await self.runtime.store.async_save({"snapshots": self.runtime.snapshots})
        self.hass.bus.async_fire("ev_load_manager_snapshot_updated",
                                 {"entry_id": self.entry.entry_id})

    async def _remember(self, sub, current: float) -> None:
        if sub.subentry_id in self.runtime.snapshots:
            return
        control = self.hass.states.get(sub.data[CONF_CONTROL])
        self.runtime.snapshots[sub.subentry_id] = {
            "initial_current_a": current,
            "initial_command": control.state if control else None,
        }
        # Persist BEFORE any write to the charger.
        await self._persist()

    async def _set_current(self, sub, target: float) -> bool:
        entity_id = sub.data[CONF_SETPOINT]
        domain = entity_id.split(".", 1)[0]
        service = "set_value"
        try:
            await self.hass.services.async_call(
                domain, service,
                {"entity_id": entity_id, "value": target},
                blocking=True,
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + ACK_TIMEOUT
            while loop.time() < deadline:
                value = _number(self.hass, entity_id)
                if value is not None and abs(value - target) < 0.1:
                    return True
                await asyncio.sleep(0.5)
        except Exception:
            _LOGGER.exception("Failed to change current for %s", sub.title)
        return False

    async def _stop(self, sub) -> bool:
        d = sub.data
        entity_id = d[CONF_CONTROL]
        domain = entity_id.split(".", 1)[0]
        stop_value = d[CONF_STOP]
        if domain == "select":
            service, data = "select_option", {"option": stop_value}
        elif domain in ("switch", "input_boolean"):
            service, data = ("turn_off" if stop_value == "off" else "turn_on"), {}
        elif domain == "input_select":
            service, data = "select_option", {"option": stop_value}
        else:
            _LOGGER.error("Unsupported charge command domain: %s", domain)
            return False
        try:
            await self.hass.services.async_call(
                domain, service, {"entity_id": entity_id, **data}, blocking=True
            )
            loop = asyncio.get_running_loop()
            deadline = loop.time() + ACK_TIMEOUT
            while loop.time() < deadline:
                state = self.hass.states.get(entity_id)
                if state is not None and state.state == stop_value:
                    return True
                await asyncio.sleep(0.5)
        except Exception:
            _LOGGER.exception("Failed to stop charger %s", sub.title)
        _LOGGER.error("Charger %s did not acknowledge stop command", sub.title)
        return False

    async def async_evaluate(self) -> None:
        if self.lock.locked():
            return
        async with self.lock:
            loop = asyncio.get_running_loop()
            if loop.time() - self._last_action < COOLDOWN_SECONDS:
                return
            totals, limits, chargers = self._state()
            decision = decide(totals, limits, chargers)
            if decision.action == "idle":
                return
            sub = next((s for s in self._subs()
                        if s.subentry_id == decision.charger_id), None)
            if sub is None:
                return
            charger = next(c for c in chargers if c.identifier == sub.subentry_id)
            self._last_action = loop.time()
            if charger.setpoint is not None:
                await self._remember(sub, charger.setpoint)
            if decision.action == "stop":
                await self._stop(sub)
                return
            if decision.target is None:
                return
            ok = await self._set_current(sub, decision.target)
            if not ok:
                _LOGGER.warning("Charger %s failed to acknowledge %s A; stopping", sub.title, decision.target)
                await self._stop(sub)
                return
            if decision.action == "restore":
                snap = self.runtime.snapshots.get(sub.subentry_id)
                if snap and decision.target >= snap["initial_current_a"] - 0.1:
                    self.runtime.snapshots.pop(sub.subentry_id, None)
                    await self._persist()
