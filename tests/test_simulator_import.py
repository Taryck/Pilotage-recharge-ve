"""Test the simulator import step without Home Assistant or hardware.

Extract just its async function with ast; the surrounding config-flow UI relies
on HA, but the synchronization's authorization and reconciliation logic does not.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any


class FakeSubentry:
    counter = 0

    def __init__(self, *, data, subentry_type, title, unique_id, subentry_id=None):
        FakeSubentry.counter += 1
        self.data = data
        self.subentry_type = subentry_type
        self.title = title
        self.unique_id = unique_id
        self.subentry_id = subentry_id or f"sub_{FakeSubentry.counter}"


def import_function():
    path = Path(__file__).resolve().parents[1] / "custom_components" / "ev_load_manager" / "config_flow.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    klass = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "EvLoadManagerConfigFlow")
    method = next(x for x in klass.body if isinstance(x, ast.AsyncFunctionDef) and x.name == "async_step_import")
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    globals_ = {
        "Any": Any,
        "FlowResult": dict,
        "PHASES": ("l1", "l2", "l3"),
        "CONF_CURRENT_PREFIX": "current_",
        "CONF_THRESHOLD_PREFIX": "max_",
        "CONF_CHARGER_NAME": "name",
        "SUBENTRY_TYPE_CHARGER": "charger",
        "DOMAIN": "ev_load_manager",
        "ConfigSubentry": FakeSubentry,
        "MappingProxyType": MappingProxyType,
        "_validate_charger": lambda hass, data: None,
    }
    exec(compile(module, str(path), "exec"), globals_)
    return globals_["async_step_import"]


def payload(n=2, sim="parking-123"):
    data = {
        "origin": "charging_station_simulator",
        "simulator_entry_id": sim,
        "simulator_name": "Parking de test",
        "simulator_chargers": [
            {
                "simulator_key": f"charging_station_simulator:{sim}:{i:02d}",
                "name": f"Borne {i:02d}", "setpoint": f"number.borne_{i}",
                "control_entity": f"select.borne_{i}",
                "min_current": 8, "max_current": 32,
                "start_value": "Charging", "stop_value": "Stopped",
                **{f"current_{phase}": f"sensor.borne_{i}_{phase}"
                   for phase in ("l1", "l2", "l3")},
            }
            for i in range(1, n + 1)
        ],
    }
    for phase in ("l1", "l2", "l3"):
        data[f"current_{phase}"] = f"sensor.total_{phase}"
        data[f"max_{phase}"] = 40.0
    return data


class FakeFlow:
    def __init__(self, entries=()):
        self.updates = []
        self.entries = list(entries)
        self.unique_id = None
        self.hass = SimpleNamespace(
            config_entries=SimpleNamespace(
                async_entries=lambda domain: self.entries,
                async_update_entry=self.update_entry,
            )
        )

    async def async_set_unique_id(self, unique_id):
        self.unique_id = unique_id

    def update_entry(self, entry, *, data, subentries):
        self.updates.append((entry, data, subentries))

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    def async_abort(self, *, reason):
        return {"type": "abort", "reason": reason}


def test_first_sync_creates_dedicated_entry_and_all_chargers():
    flow = FakeFlow()
    result = asyncio.run(import_function()(flow, payload(10)))
    assert flow.unique_id == "simulator_parking-123"
    assert result["type"] == "create_entry"
    assert result["data"]["simulator_entry_id"] == "parking-123"
    assert "simulator_chargers" not in result["data"]
    assert len(result["subentries"]) == 10
    assert len({x["unique_id"] for x in result["subentries"]}) == 10
    assert not flow.updates


def test_repeat_sync_updates_existing_and_preserves_unrelated_subentries():
    existing_sub = FakeSubentry(
        data=MappingProxyType({"name": "old"}),
        subentry_type="charger",
        title="old", unique_id="charging_station_simulator:parking-123:01",
        subentry_id="existing_1",
    )
    unrelated = FakeSubentry(
        data=MappingProxyType({"name": "preserved"}),
        subentry_type="charger", title="preserved",
        unique_id="other", subentry_id="unrelated",
    )
    old = SimpleNamespace(
        unique_id="simulator_parking-123",
        data={"simulator_entry_id": "parking-123"},
        subentries={"existing_1": existing_sub, "unrelated": unrelated},
    )
    flow = FakeFlow([old])
    result = asyncio.run(import_function()(flow, payload(3)))
    assert result == {"type": "abort", "reason": "already_configured"}
    assert len(flow.updates) == 1
    _, updated_data, updated_subentries = flow.updates[0]
    assert len(updated_subentries) == 4  # existing + 2 new + unrelated
    assert updated_subentries["existing_1"].unique_id == existing_sub.unique_id
    assert updated_subentries["unrelated"] is unrelated
    assert updated_data["current_l1"] == "sensor.total_l1"


def test_collision_with_non_simulated_entry_never_modifies_it():
    physical = SimpleNamespace(
        unique_id="simulator_parking-123",
        data={"current_l1": "sensor.real_meter"},
        subentries={},
    )
    flow = FakeFlow([physical])
    result = asyncio.run(import_function()(flow, payload()))
    assert result == {"type": "abort", "reason": "invalid_import"}
    assert flow.updates == []


def test_duplicate_simulator_keys_are_rejected():
    incoming = payload(2)
    incoming["simulator_chargers"][1]["simulator_key"] = incoming["simulator_chargers"][0]["simulator_key"]
    flow = FakeFlow()
    result = asyncio.run(import_function()(flow, incoming))
    assert result == {"type": "abort", "reason": "invalid_import"}
    assert not flow.updates
