"""Non-blocking, per-phase EV charge regulation for Home Assistant.

Commands are dispatched in waves: one amp per eligible charger, with no wait for
another charger's acknowledgement. Each pending action is retried independently.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from math import isfinite
from datetime import timedelta

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .const import (
    CONF_CONTROL, CONF_CURRENT_PREFIX, CONF_MAX, CONF_MIN, CONF_SETPOINT,
    CONF_START, CONF_STOP, CONF_THRESHOLD_PREFIX, PHASES, SUBENTRY_TYPE_CHARGER,
    CONF_ACK_TIMEOUT, CONF_ACK_RETRIES, CONF_CONTROL_INTERVAL, CONF_SETTLE_DELAY,
    DEFAULT_ACK_TIMEOUT, DEFAULT_ACK_RETRIES, DEFAULT_CONTROL_INTERVAL, DEFAULT_SETTLE_DELAY,
)
from .planner import Charger, excesses
from .sensor import current_in_amp

_LOGGER = logging.getLogger(__name__)
EPS = 0.05


@dataclass
class Pending:
    """Action sent to a charger; the entity state is its acknowledgement."""
    action: str
    target: float | str
    sent_at: float
    attempts: int = 0
    sending: bool = False


def _number(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", ""):
        return None
    try:
        result = float(state.state)
        return result if isfinite(result) else None
    except (ValueError, TypeError):
        return None


class LoadController:
    """Independent acknowledgements, fast multi-charger shedding and recovery."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass, self.entry = hass, entry
        self.runtime = entry.runtime_data
        self.lock = asyncio.Lock()
        self.remove_callbacks = []
        self.pending: dict[str, Pending] = {}
        self.ready_at: dict[str, float] = {}
        self._dirty = False
        self._task: asyncio.Task | None = None
        self._stopping = False
        self._last_error: str | None = None
        self._last_notification: str | None = None

    def _options(self) -> dict:
        return {**self.entry.data, **self.entry.options}

    def _setting(self, key, default):
        return self._options().get(key, default)

    def _subs(self):
        return [s for s in self.entry.subentries.values()
                if s.subentry_type == SUBENTRY_TYPE_CHARGER]

    async def async_start(self) -> None:
        data = self._options()
        entities = [data.get(f"{CONF_CURRENT_PREFIX}{p}") for p in PHASES]
        for sub in self._subs():
            entities.extend((sub.data.get(CONF_SETPOINT), sub.data.get(CONF_CONTROL)))
            entities.extend(sub.data.get(f"{CONF_CURRENT_PREFIX}{p}") for p in PHASES)
        ids = list(set(filter(None, entities)))
        if ids:
            self.remove_callbacks.append(
                async_track_state_change_event(self.hass, ids, self._changed)
            )
        self.remove_callbacks.append(
            async_track_time_interval(
                self.hass, self._periodic,
                timedelta(seconds=float(self._setting(
                    CONF_CONTROL_INTERVAL, DEFAULT_CONTROL_INTERVAL
                ))),
            )
        )
        self._request_evaluation()

    async def async_stop(self) -> None:
        self._stopping = True
        for remove in self.remove_callbacks:
            remove()
        self.remove_callbacks.clear()
        if self._task is not None:
            await self._task
        async with self.lock:
            pass

    @callback
    def _changed(self, event) -> None:
        self._request_evaluation()

    @callback
    def _periodic(self, now) -> None:
        self._request_evaluation()

    @callback
    def _request_evaluation(self) -> None:
        if self._stopping:
            return
        self._dirty = True
        if self._task is None or self._task.done():
            self._task = self.hass.async_create_task(self.async_evaluate())

    async def _notify_error(self, message: str):
        _LOGGER.error("EV load management: %s", message)
        if self._last_notification == message:
            return
        self._last_notification = message
        await self.hass.services.async_call(
            "persistent_notification", "create",
            {"title": "EV Load Manager: regulation error",
             "message": message,
             "notification_id": f"ev_load_manager_error_{self.entry.entry_id}"},
            blocking=False,
        )

    async def _persist(self):
        await self.runtime.store.async_save({"snapshots": self.runtime.snapshots})
        self.hass.bus.async_fire(
            "ev_load_manager_snapshot_updated", {"entry_id": self.entry.entry_id}
        )

    async def _remember(self, sub, setpoint: float):
        if sub.subentry_id in self.runtime.snapshots:
            return
        state = self.hass.states.get(sub.data[CONF_CONTROL])
        self.runtime.snapshots[sub.subentry_id] = {
            "initial_current_a": setpoint,
            "initial_command": state.state if state else None,
            "initial_phase_currents_a": {
                p: current_in_amp(self.hass, sub.data.get(f"{CONF_CURRENT_PREFIX}{p}"))
                if sub.data.get(f"{CONF_CURRENT_PREFIX}{p}") else 0.0
                for p in PHASES
            },
            "stopped_by_module": False,
        }
        await self._persist()

    def _state(self):
        data = self._options()
        totals = {
            p: current_in_amp(self.hass, data[f"{CONF_CURRENT_PREFIX}{p}"])
            for p in PHASES
        }
        limits = {p: float(data[f"{CONF_THRESHOLD_PREFIX}{p}"]) for p in PHASES}
        if not all(isfinite(x) and x > 0 for x in limits.values()):
            raise ValueError("The current limits must be finite and positive")
        chargers = []
        for sub in self._subs():
            d = sub.data
            currents = {}
            for p in PHASES:
                entity_id = d.get(f"{CONF_CURRENT_PREFIX}{p}")
                currents[p] = current_in_amp(self.hass, entity_id) if entity_id else 0.0
            control = self.hass.states.get(d[CONF_CONTROL])
            active = control is not None and control.state == d[CONF_START]
            low, high = float(d[CONF_MIN]), float(d[CONF_MAX])
            if not 0 <= low <= high or not all(isfinite(x) for x in (low, high)):
                raise ValueError(f"Invalid current limits for charger {sub.title}")
            snapshot = self.runtime.snapshots.get(sub.subentry_id, {})
            chargers.append(Charger(
                identifier=sub.subentry_id,
                currents=currents, setpoint=_number(self.hass, d[CONF_SETPOINT]),
                minimum=low, maximum=high, original=snapshot.get("initial_current_a"),
                active=active,
            ))
        return totals, limits, chargers

    def _acknowledged(self, sub, pending: Pending) -> bool:
        d = sub.data
        if pending.action in ("reduce", "restore", "prepare_restart"):
            value = _number(self.hass, d[CONF_SETPOINT])
            return value is not None and abs(value - float(pending.target)) <= 0.1
        state = self.hass.states.get(d[CONF_CONTROL])
        if state is None:
            return False
        domain = d[CONF_CONTROL].split(".", 1)[0]
        if domain in ("number", "input_number"):
            try:
                return abs(float(state.state) - float(pending.target)) <= 0.1
            except (ValueError, TypeError):
                return False
        return state.state == pending.target

    async def _send(self, sub, pending: Pending) -> None:
        """Send one service call; do not wait for acknowledgement here."""
        d = sub.data
        if pending.action in ("reduce", "restore", "prepare_restart"):
            entity_id = d[CONF_SETPOINT]
            if entity_id.split(".", 1)[0] not in ("number", "input_number"):
                raise ValueError(f"Unsupported setpoint entity: {entity_id}")
            low, high = float(d[CONF_MIN]), float(d[CONF_MAX])
            if not low - EPS <= float(pending.target) <= high + EPS:
                raise ValueError(f"Setpoint outside bounds for {sub.title}")
            service = "set_value"
            params = {"entity_id": entity_id, "value": pending.target}
        else:
            entity_id = d[CONF_CONTROL]
            domain = entity_id.split(".", 1)[0]
            if domain in ("select", "input_select"):
                service = "select_option"
                params = {"entity_id": entity_id, "option": pending.target}
            elif domain in ("switch", "input_boolean"):
                if pending.target not in ("on", "off"):
                    raise ValueError("Binary control must be on or off")
                service = "turn_on" if pending.target == "on" else "turn_off"
                params = {"entity_id": entity_id}
            elif domain in ("number", "input_number"):
                service = "set_value"
                params = {"entity_id": entity_id, "value": float(pending.target)}
            else:
                raise ValueError(f"Unsupported command entity: {entity_id}")
        domain = entity_id.split(".", 1)[0]
        await self.hass.services.async_call(domain, service, params, blocking=True)

    async def _send_background(self, sub, pending: Pending):
        pending.sending = True
        try:
            await asyncio.wait_for(
                self._send(sub, pending),
                timeout=float(self._setting(CONF_ACK_TIMEOUT, DEFAULT_ACK_TIMEOUT)),
            )
        except Exception:
            _LOGGER.exception("Service call failed for %s (%s)", sub.title, pending.action)
        finally:
            pending.sending = False
            self._request_evaluation()

    async def _issue(self, sub, action: str, target: float | str, now: float):
        if sub.subentry_id in self.pending:
            return False
        pending = Pending(action=action, target=target, sent_at=now, attempts=1, sending=True)
        self.pending[sub.subentry_id] = pending
        self.hass.async_create_task(self._send_background(sub, pending))
        return True

    async def _expire_pending(self, subs: dict, now: float):
        delay = float(self._setting(CONF_ACK_TIMEOUT, DEFAULT_ACK_TIMEOUT))
        retries = int(self._setting(CONF_ACK_RETRIES, DEFAULT_ACK_RETRIES))
        settle = float(self._setting(CONF_SETTLE_DELAY, DEFAULT_SETTLE_DELAY))
        for sid, pending in list(self.pending.items()):
            sub = subs.get(sid)
            if sub is None:
                self.pending.pop(sid, None)
                continue
            if self._acknowledged(sub, pending):
                self.pending.pop(sid, None)
                self.ready_at[sid] = now + settle
                if pending.action == "stop":
                    snap = self.runtime.snapshots.get(sid)
                    if snap is not None:
                        snap["stopped_by_module"] = True
                        await self._persist()
                elif pending.action == "start":
                    snap = self.runtime.snapshots.get(sid)
                    if snap is not None:
                        snap["stopped_by_module"] = False
                        await self._persist()
                elif pending.action == "restore":
                    snap = self.runtime.snapshots.get(sid)
                    if snap is not None:
                        ceiling = min(float(snap["initial_current_a"]), float(sub.data[CONF_MAX]))
                        if float(pending.target) >= ceiling - EPS:
                            self.runtime.snapshots.pop(sid, None)
                            await self._persist()
                continue
            if pending.sending or now - pending.sent_at < delay:
                continue
            if pending.attempts <= retries:
                pending.attempts += 1
                pending.sent_at = now
                pending.sending = True
                self.hass.async_create_task(self._send_background(sub, pending))
                continue
            self.pending.pop(sid, None)
            if pending.action in ("reduce", "prepare_restart"):
                _LOGGER.warning("No acknowledgement after %s attempts for %s: requesting stop",
                                pending.attempts, sub.title)
                await self._issue(sub, "stop", sub.data[CONF_STOP], now)
            elif pending.action == "stop":
                await self._notify_error(
                    f"Charger {sub.title} did not acknowledge stop. Check the physical installation."
                )
            else:
                await self._notify_error(
                    f"Charger {sub.title} did not acknowledge {pending.action}."
                )

    async def _prune_snapshots(self, chargers):
        changed = False
        for charger in chargers:
            sid = charger.identifier
            snap = self.runtime.snapshots.get(sid)
            if snap is None or sid in self.pending:
                continue
            # A still-active charger drawing no current has completed its
            # charging session: there is no original charge left to restore.
            no_draw = all(value is not None and abs(value) <= EPS
                          for value in charger.currents.values())
            if (charger.active and no_draw) or (
                not charger.active and not snap.get("stopped_by_module")
            ):
                self.runtime.snapshots.pop(sid, None)
                changed = True
            elif (charger.active and charger.setpoint is not None
                  and not snap.get("stopped_by_module")
                  and charger.setpoint >= min(float(snap["initial_current_a"]),
                                               charger.maximum) - EPS):
                self.runtime.snapshots.pop(sid, None)
                changed = True
        if changed:
            await self._persist()

    async def _run_once(self):
        now = asyncio.get_running_loop().time()
        subs = {s.subentry_id: s for s in self._subs()}
        await self._expire_pending(subs, now)
        totals, limits, chargers = self._state()
        await self._prune_snapshots(chargers)
        over = excesses(totals, limits)
        if any(v is None for v in over.values()):
            return
        hot = {p for p in PHASES if over[p] > EPS}
        ready = [c for c in chargers
                 if c.identifier not in self.pending
                 and now >= self.ready_at.get(c.identifier, 0)]
        if hot:
            # Never wait for a slow charger if others can lower the excess.
            # Every charger is asked to drop at most 1 A during one wave.
            invalid = [
                c for c in ready if c.active and (
                    c.setpoint is None or any(x is None for x in c.currents.values())
                )
            ]
            for c in invalid:
                sub = subs[c.identifier]
                if c.setpoint is not None:
                    await self._remember(sub, c.setpoint)
                await self._issue(sub, "stop", sub.data[CONF_STOP], now)
            remaining = {p: float(over[p]) for p in PHASES}
            ranked = sorted(
                (c for c in ready if c.active and c not in invalid and
                 c.largest_draw(hot) > EPS),
                key=lambda c: (-c.largest_draw(hot), c.identifier),
            )
            for c in ranked:
                if not any(remaining[p] > EPS and p in c.phases() for p in hot):
                    break
                sub = subs[c.identifier]
                if c.setpoint is not None and c.setpoint - c.minimum >= 1 - EPS:
                    await self._remember(sub, c.setpoint)
                    await self._issue(sub, "reduce", round(c.setpoint - 1, 3), now)
                    for p in c.phases():
                        remaining[p] = max(0.0, remaining[p] - 1.0)
            # Do not stop a minimum-current charger while another adjustment
            # is pending; let it change the physical reading first.
            if not self.pending and any(v > EPS for v in remaining.values()):
                minimum_only = sorted(
                    (c for c in ranked if c.setpoint is not None
                     and c.setpoint - c.minimum < 1 - EPS),
                    key=lambda c: (-c.largest_draw(hot), c.identifier),
                )
                for c in minimum_only:
                    if not any(remaining[p] > EPS and p in c.phases() for p in hot):
                        break
                    sub = subs[c.identifier]
                    await self._remember(sub, c.setpoint)
                    await self._issue(sub, "stop", sub.data[CONF_STOP], now)
                    for p in c.phases():
                        remaining[p] = max(0, remaining[p] - max(0, c.currents[p] or 0))
            return

        # Do not increase charge while still awaiting the outcome of shedding.
        if self.pending:
            return
        # Previously stopped chargers have priority over already-running
        # chargers, so they cannot starve while others are being restored.
        for c in sorted(ready, key=lambda c: c.identifier):
            snap = self.runtime.snapshots.get(c.identifier)
            if not snap or not snap.get("stopped_by_module"):
                continue
            if snap.get("initial_command") != subs[c.identifier].data[CONF_START]:
                continue
            phase_currents = snap.get("initial_phase_currents_a") or {}
            used = [p for p in PHASES if (phase_currents.get(p) or 0) > EPS]
            if not used:
                used = list(PHASES)
            required = c.setpoint if c.setpoint is not None else c.minimum
            required = max(required, c.minimum)
            if all(limits[p] - totals[p] >= required + 2 for p in used):
                await self._issue(subs[c.identifier], "start",
                                  subs[c.identifier].data[CONF_START], now)
                return

        margin = {p: limits[p] - totals[p] for p in PHASES}
        restore = [c for c in ready if c.active and c.original is not None
                   and c.setpoint is not None and c.phases()
                   and c.setpoint + 1 <= min(c.original, c.maximum) + EPS
                   and all(v is not None for v in c.currents.values())]
        restore.sort(key=lambda c: (
            sum(c.currents[p] or 0 for p in PHASES),
            -(c.original - c.setpoint), c.identifier,
        ))
        for c in restore:
            if all(margin[p] >= 2 for p in c.phases()):
                await self._issue(subs[c.identifier], "restore",
                                  round(c.setpoint + 1, 3), now)
                for p in c.phases():
                    margin[p] -= 1

    async def async_evaluate(self):
        """Catch errors, notify the user, and re-run for coalesced events."""
        if self.lock.locked():
            self._dirty = True
            return
        async with self.lock:
            while self._dirty and not self._stopping:
                self._dirty = False
                try:
                    await self._run_once()
                    self._last_error = None
                except Exception as exc:
                    problem = f"{type(exc).__name__}: {exc}"
                    _LOGGER.exception("EV Load Manager regulation failed")
                    if self._last_error != problem:
                        self._last_error = problem
                        try:
                            await self._notify_error(problem)
                        except Exception:
                            _LOGGER.exception("Could not create persistent notification")
                    break
