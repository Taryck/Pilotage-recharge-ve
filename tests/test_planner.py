"""Automatic unit tests for load-balancing decisions (no HA installation required)."""
import importlib.util
from pathlib import Path
import sys

PATH = Path(__file__).resolve().parents[1] / "custom_components" / "ev_load_manager" / "planner.py"
spec = importlib.util.spec_from_file_location("ev_planner", PATH)
planner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = planner
spec.loader.exec_module(planner)
Charger, decide, excesses = planner.Charger, planner.decide, planner.excesses


def c(name, l1=0, l2=0, l3=0, amps=16, minimum=6,
      original=None, active=True):
    return Charger(
        name, {"l1": l1, "l2": l2, "l3": l3},
        amps, minimum, 32, original, active,
    )


def test_excesses_single_double_triple():
    limits = dict.fromkeys(("l1", "l2", "l3"), 20)
    assert excesses({"l1": 23, "l2": 20, "l3": 17}, limits) == {
        "l1": 3, "l2": 0, "l3": 0
    }
    assert excesses({"l1": 23, "l2": 22, "l3": 19}, limits)["l2"] == 2
    assert all(v == 1 for v in excesses(dict.fromkeys(limits, 21), limits).values())


def test_select_highest_draw_on_overloaded_phase():
    result = decide(
        {"l1": 23, "l2": 10, "l3": 10}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("lower", l1=7, amps=16), c("higher", l1=12, amps=16)],
    )
    assert (result.action, result.charger_id, result.target) == ("reduce", "higher", 15)


def test_ignores_charger_on_other_phase():
    result = decide(
        {"l1": 21, "l2": 17, "l3": 10}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("wrong", l2=16), c("right", l1=7)],
    )
    assert result.charger_id == "right"


def test_two_phases_and_single_amp_step():
    result = decide(
        {"l1": 23, "l2": 22, "l3": 12}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("three_phase", 12, 12, 12, amps=16), c("mono", 14, 0, 0, amps=16)],
    )
    assert result.action == "reduce"
    assert result.target == 15


def test_reached_minimum_stops_last_resort():
    result = decide(
        {"l1": 22, "l2": 10, "l3": 10}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("minimum", l1=7, amps=6, minimum=6)],
    )
    assert (result.action, result.charger_id) == ("stop", "minimum")


def test_unavailable_measurement_prioritizes_stop():
    result = decide(
        {"l1": 23, "l2": 10, "l3": 10}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("normal", l1=9), c("unavailable", l1=None)],
    )
    assert (result.action, result.charger_id) == ("stop", "unavailable")


def test_missing_installation_measurement_blocks_commands():
    result = decide(
        {"l1": None, "l2": 20, "l3": 22}, dict.fromkeys(("l1", "l2", "l3"), 20),
        [c("borne", l3=8)],
    )
    assert result.action == "idle"


def test_recovery_requires_two_amp_headroom():
    limits = dict.fromkeys(("l1", "l2", "l3"), 20)
    chargers = [c("borne", l1=8, l2=8, l3=8, amps=12, original=16)]
    assert decide({"l1": 19, "l2": 17, "l3": 17}, limits, chargers).action == "idle"
    result = decide({"l1": 18, "l2": 18, "l3": 18}, limits, chargers)
    assert (result.action, result.target) == ("restore", 13)


def test_recovery_sort_current_ascending_then_reduction_descending():
    limits = dict.fromkeys(("l1", "l2", "l3"), 20)
    results = [
        c("higher", l1=11, amps=12, original=18),
        c("lower_less_reduced", l1=7, amps=13, original=15),
        c("lower_more_reduced", l1=7, amps=10, original=18),
    ]
    answer = decide(dict.fromkeys(limits, 15), limits, results)
    assert (answer.action, answer.charger_id, answer.target) == ("restore", "lower_more_reduced", 11)


def test_no_restoration_past_original_setpoint():
    limits = dict.fromkeys(("l1", "l2", "l3"), 20)
    assert decide(dict.fromkeys(limits, 15), limits,
                  [c("borne", l1=8, amps=16, original=16)]).action == "idle"
