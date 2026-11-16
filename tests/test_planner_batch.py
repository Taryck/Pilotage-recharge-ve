"""Pure tests: plan all reductions iteratively BEFORE issuing service calls."""
import importlib.util
from pathlib import Path
import sys

PATH = Path(__file__).resolve().parents[1] / "custom_components/ev_load_manager/planner.py"
spec = importlib.util.spec_from_file_location("ev_planner_batch", PATH)
planner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = planner
spec.loader.exec_module(planner)


def charger(name, measured, *, current=None, original=None, min_a=6, mode="l1"):
    amps = measured if current is None else current
    phases = {
        "l1": {"l1": measured, "l2": 0, "l3": 0},
        "l2": {"l1": 0, "l2": measured, "l3": 0},
        "tri": {"l1": measured, "l2": measured, "l3": measured},
    }[mode]
    return planner.Charger(name, phases, amps, min_a, 32,
                           original=original, active=True)


def limits(*, l1=100, l2=100, l3=100):
    return {"l1": l1, "l2": l2, "l3": l3}


def totals(*, l1=0, l2=0, l3=0):
    return {"l1": l1, "l2": l2, "l3": l3}


def test_four_amps_only_take_from_largest():
    stations = [charger("16", 16), charger("12", 12), charger("10", 10)]
    plan = planner.plan_reductions(totals(l1=28), limits(l1=24), stations)
    assert plan.targets == {"16": 12}
    assert [step[0] for step in plan.steps] == ["16"] * 4
    assert plan.remaining["l1"] == 0


def test_eight_amps_rerank_every_step_leave_ten_untouched():
    stations = [charger("16", 16), charger("12", 12), charger("10", 10)]
    plan = planner.plan_reductions(totals(l1=28), limits(l1=20), stations)
    assert plan.targets == {"16": 10, "12": 10}
    assert "10" not in plan.targets
    assert len(plan.steps) == 8
    assert plan.remaining["l1"] == 0


def test_equal_consumption_prefers_charger_closest_to_original():
    stations = [
        charger("already_cut", 12, current=12, original=32),
        charger("never_cut", 12, current=12, original=12),
    ]
    plan = planner.plan_reductions(totals(l1=24), limits(l1=23), stations)
    assert plan.targets == {"never_cut": 11}


def test_tie_8A_not_yet_reduced_before_32A_heavily_reduced():
    stations = [
        charger("once_32", 8, current=8, original=32),
        charger("original_8", 8, current=8, original=8, min_a=6),
    ]
    plan = planner.plan_reductions(totals(l1=16), limits(l1=15), stations)
    assert plan.targets == {"original_8": 7}


def test_two_phase_overload_is_compensated_by_triphase_charger():
    stations = [
        charger("three", 15, mode="tri"),
        charger("mono", 9, mode="l1"),
    ]
    plan = planner.plan_reductions(totals(l1=24, l2=15, l3=15),
                                   limits(l1=21, l2=14, l3=100), stations)
    assert plan.targets["three"] == 12
    assert plan.remaining["l1"] == 0
    assert plan.remaining["l2"] == 0


def test_minimum_limits_plan_and_leaves_excess():
    stations = [charger("a", 8, min_a=8), charger("b", 10, min_a=9)]
    plan = planner.plan_reductions(totals(l1=18), limits(l1=10), stations)
    assert plan.targets == {"b": 9}
    assert plan.remaining["l1"] == 7


def test_inflight_reduction_is_reserved_to_avoid_double_cut():
    stations = [charger("pending", 16), charger("ready", 14)]
    plan = planner.plan_reductions(
        totals(l1=30), limits(l1=26), stations,
        reserved={"pending": 4},
        unavailable_ids=frozenset({"pending"}),
    )
    assert plan.targets == {}
    assert plan.remaining["l1"] == 0


def test_unavailable_measurements_not_treated_as_zero():
    bad = planner.Charger("unavailable", {"l1": None, "l2": 0, "l3": 0},
                          16, 6, 32)
    plan = planner.plan_reductions(totals(l1=20), limits(l1=15), [bad])
    assert plan.targets == {}
    assert plan.remaining["l1"] == 5


def test_no_eligible_does_not_change_inactive_charger():
    off = planner.Charger("off", {"l1": 16, "l2": 0, "l3": 0},
                          16, 6, 32, active=False)
    plan = planner.plan_reductions(totals(l1=20), limits(l1=15), [off])
    assert not plan.targets
