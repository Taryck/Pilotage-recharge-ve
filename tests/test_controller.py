"""Controller behavior tests using a fake Home Assistant runtime.

The actual controller source is extracted with ast, keeping its methods exactly
as written while substituting only Home Assistant's state/service/event APIs.
"""
from __future__ import annotations

import ast
import asyncio
from dataclasses import dataclass
from datetime import timedelta
import importlib.util
import logging
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "ev_load_manager"
PHASES = ("l1", "l2", "l3")


def controller_class():
    plan_spec = importlib.util.spec_from_file_location("controller_test_planner", ROOT / "planner.py")
    plan = importlib.util.module_from_spec(plan_spec)
    import sys
    sys.modules[plan_spec.name] = plan
    plan_spec.loader.exec_module(plan)

    tree = ast.parse((ROOT / "controller.py").read_text(encoding="utf-8"))
    keep = [node for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.ClassDef))
            and node.name in ("Pending", "_number", "LoadController")]
    mod = ast.fix_missing_locations(ast.Module(
        body=[ast.ImportFrom(module="__future__", names=[
            ast.alias(name="annotations")], level=0)] + keep,
        type_ignores=[],
    ))
    def amp(hass, entity):
        if not entity:
            return None
        state = hass.states.get(entity)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    names = {
        "asyncio": asyncio, "dataclass": dataclass, "timedelta": timedelta,
        "isfinite": isfinite, "HomeAssistant": object,
        "callback": lambda fn: fn, "_LOGGER": logging.getLogger(__name__),
        "EPS": 0.05, "PHASES": PHASES,
        "Charger": plan.Charger, "excesses": plan.excesses,
        "current_in_amp": amp,
        "CONF_CURRENT_PREFIX": "current_", "CONF_THRESHOLD_PREFIX": "max_",
        "CONF_MIN": "min_current", "CONF_MAX": "max_current",
        "CONF_SETPOINT": "setpoint", "CONF_CONTROL": "control_entity",
        "CONF_START": "start_value", "CONF_STOP": "stop_value",
        "SUBENTRY_TYPE_CHARGER": "charger",
        "CONF_ACK_TIMEOUT": "ack_timeout", "CONF_ACK_RETRIES": "ack_retries",
        "CONF_CONTROL_INTERVAL": "control_interval", "CONF_SETTLE_DELAY": "settle_delay",
        "DEFAULT_ACK_TIMEOUT": 6, "DEFAULT_ACK_RETRIES": 2,
        "DEFAULT_CONTROL_INTERVAL": 1, "DEFAULT_SETTLE_DELAY": 0,
        "async_track_state_change_event": lambda *args: lambda: None,
        "async_track_time_interval": lambda *args: lambda: None,
    }
    exec(compile(mod, str(ROOT / "controller.py"), "exec"), names)
    return names["LoadController"], names["Pending"]


class FakeStates:
    def __init__(self):
        self.values = {}

    def get(self, name):
        state = self.values.get(name)
        return SimpleNamespace(state=str(state), attributes={"unit_of_measurement": "A"}) if state is not None else None

    def set(self, name, value):
        self.values[name] = value


class FakeServices:
    def __init__(self, states):
        self.states = states
        self.calls = []
        self.slow = set()

    async def async_call(self, domain, service, data, *, blocking):
        self.calls.append((domain, service, dict(data)))
        if domain == "persistent_notification":
            return
        if data["entity_id"] in self.slow:
            await asyncio.sleep(3600)
        if service == "set_value":
            self.states.set(data["entity_id"], data["value"])
        elif service == "select_option":
            self.states.set(data["entity_id"], data["option"])
        elif service in ("turn_on", "turn_off"):
            self.states.set(data["entity_id"], "on" if service == "turn_on" else "off")


class FakeStore:
    def __init__(self):
        self.saved = []

    async def async_save(self, data):
        import copy
        self.saved.append(copy.deepcopy(data))


def setup(count=2, limit=20):
    Controller, Pending = controller_class()
    states = FakeStates()
    services = FakeServices(states)
    tasks = []
    hass = SimpleNamespace(states=states, services=services, bus=SimpleNamespace(async_fire=lambda *a: None))

    def schedule(coroutine, *args):
        task = asyncio.create_task(coroutine)
        tasks.append(task)
        return task

    hass.async_create_task = schedule
    for p in PHASES:
        states.set(f"sensor.installation_{p}", 28 if p == "l1" else 0)
    subs = {}
    for i in range(count):
        sid = f"charger_{i}"
        d = {
            "name": sid, "min_current": 6, "max_current": 32,
            "setpoint": f"number.{sid}", "control_entity": f"select.{sid}",
            "start_value": "Charging", "stop_value": "Stopped",
            "current_l1": f"sensor.{sid}_l1",
            "current_l2": f"sensor.{sid}_l2",
            "current_l3": f"sensor.{sid}_l3",
        }
        subs[sid] = SimpleNamespace(subentry_id=sid, subentry_type="charger",
                                    title=sid, data=d)
        states.set(d["setpoint"], 16)
        states.set(d["control_entity"], "Charging")
        states.set(d["current_l1"], 14)
        states.set(d["current_l2"], 0)
        states.set(d["current_l3"], 0)
    config = {f"current_{p}": f"sensor.installation_{p}" for p in PHASES}
    config.update({f"max_{p}": limit for p in PHASES})
    config.update({"ack_timeout": 0.05, "ack_retries": 1, "settle_delay": 0})
    store = FakeStore()
    runtime = SimpleNamespace(store=store, snapshots={})
    entry = SimpleNamespace(entry_id="test_entry", data=config, options={},
                            subentries=subs, runtime_data=runtime)
    controller = Controller(hass, entry)
    return controller, hass, subs, store, tasks, Pending


async def settle_tasks(tasks):
    await asyncio.sleep(0.01)
    await asyncio.gather(*(task for task in tasks if task.done()), return_exceptions=True)


def test_reduction_wave_does_not_wait_for_slow_charger():
    async def run():
        controller, hass, subs, store, tasks, _ = setup(3)
        hass.services.slow.add("number.charger_0")
        await controller._run_once()
        await asyncio.sleep(0.005)
        calls = [call for call in hass.services.calls if call[1] == "set_value"]
        assert len(calls) == 3
        assert {data["entity_id"] for _, _, data in calls} == {
            "number.charger_0", "number.charger_1", "number.charger_2"
        }
        assert all(data["value"] == 15 for _, _, data in calls)
        assert len(store.saved) == 3  # snapshots persisted BEFORE commands
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_active_zero_current_clears_snapshot():
    async def run():
        controller, hass, subs, store, tasks, _ = setup(1)
        controller.runtime.snapshots["charger_0"] = {"initial_current_a": 16}
        hass.states.set("sensor.charger_0_l1", 0)
        _, _, chargers = controller._state()
        await controller._prune_snapshots(chargers)
        assert controller.runtime.snapshots == {}
        assert store.saved[-1]["snapshots"] == {}
    asyncio.run(run())


def test_snapshot_cleared_when_maximum_is_lowered():
    async def run():
        controller, hass, subs, store, tasks, _ = setup(1)
        controller.runtime.snapshots["charger_0"] = {"initial_current_a": 32}
        subs["charger_0"].data["max_current"] = 20
        hass.states.set("number.charger_0", 20)
        _, _, chargers = controller._state()
        await controller._prune_snapshots(chargers)
        assert not controller.runtime.snapshots
    asyncio.run(run())


def test_timeout_retries_before_emergency_stop():
    async def run():
        controller, hass, subs, store, tasks, Pending = setup(1)
        slow = "number.charger_0"
        hass.services.slow.add(slow)
        controller.pending["charger_0"] = Pending("reduce", 15, -100, attempts=1)
        await controller._expire_pending(subs, 1)
        assert controller.pending["charger_0"].attempts == 2
        assert controller.pending["charger_0"].action == "reduce"
        await asyncio.sleep(.01)
        controller.pending["charger_0"].sending = False
        controller.pending["charger_0"].sent_at = -100
        await controller._expire_pending(subs, 1)
        assert controller.pending["charger_0"].action == "stop"
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    asyncio.run(run())


def test_restart_priority_prevents_starvation_by_restore():
    async def run():
        controller, hass, subs, store, tasks, _ = setup(2, limit=50)
        hass.states.set("sensor.installation_l1", 20)
        hass.states.set("select.charger_0", "Stopped")
        hass.states.set("sensor.charger_0_l1", 0)
        controller.runtime.snapshots["charger_0"] = {
            "initial_current_a": 16, "initial_command": "Charging",
            "initial_phase_currents_a": {"l1": 14, "l2": 0, "l3": 0},
            "stopped_by_module": True,
        }
        controller.runtime.snapshots["charger_1"] = {
            "initial_current_a": 20, "initial_command": "Charging",
            "stopped_by_module": False,
        }
        await controller._run_once()
        assert controller.pending["charger_0"].action == "start"
        assert "charger_1" not in controller.pending
        await settle_tasks(tasks)
    asyncio.run(run())


def test_corrupted_configuration_creates_persistent_error_notification():
    async def run():
        controller, hass, subs, store, tasks, _ = setup(1)
        del subs["charger_0"].data["control_entity"]
        controller._dirty = True
        await controller.async_evaluate()
        assert any(domain == "persistent_notification" and service == "create"
                   for domain, service, data in hass.services.calls)
    asyncio.run(run())


def test_rejects_setpoint_domain_other_than_number():
    async def run():
        controller, hass, subs, store, tasks, Pending = setup(1)
        subs["charger_0"].data["setpoint"] = "switch.fake"
        with pytest.raises(ValueError, match="Unsupported setpoint"):
            await controller._send(subs["charger_0"], Pending("reduce", 12, 0))
    asyncio.run(run())
