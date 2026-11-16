"""End-to-end physics tests of parking without requiring Home Assistant."""
import importlib.util
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "custom_components" / "ev_load_simulator" / "model.py"
SPEC = importlib.util.spec_from_file_location("ev_sim_model", MODEL)
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)
Charger, Parking = mod.Charger, mod.Parking


def test_count_is_configurable():
    assert len(Parking(count=10).chargers) == 10
    assert len(Parking(count=24).chargers) == 24


def test_parking_total_is_only_charger_currents():
    parking = Parking(count=2, thresholds=dict.fromkeys(mod.PHASES, 100))
    parking.chargers[0].mode = "l1"
    parking.chargers[0].start()
    parking.chargers[1].mode = "tri"
    parking.chargers[1].start()
    for _ in range(15):
        parking.tick(1)
    for phase in mod.PHASES:
        expected = sum(c.currents()[phase] for c in parking.chargers)
        assert abs(parking.totals()[phase] - expected) < 0.001
    assert parking.chargers[0].currents()["l2"] == 0
    assert parking.chargers[0].currents()["l3"] == 0


def test_initial_setpoint_never_overwritten_by_control():
    c = Charger(1, initial_current=16, hardware_min=8)
    c.start()
    assert c.command(12)
    assert c.initial_current == c.initial_for_session == 16
    assert c.commanded_current == 12


def test_ramp_speed_between_one_and_two_amps_per_second():
    p = Parking(count=1)
    c = p.chargers[0]
    c.start()
    previous = 0
    for _ in range(5):
        p.tick(1)
        difference = c.physical_current - previous
        assert 1 <= difference <= 2
        previous = c.physical_current


def test_jitter_per_phase_within_point_two():
    p = Parking(count=1, thresholds=dict.fromkeys(mod.PHASES, 100))
    c = p.chargers[0]
    c.start()
    for _ in range(20):
        p.tick(1)
        for measured in c.currents().values():
            assert abs(measured - c.physical_current) <= 0.201


def test_stopping_ends_in_two_real_seconds_irrespective_of_energy_factor():
    p = Parking(count=1, factor=20)
    c = p.chargers[0]
    c.start()
    for _ in range(12):
        p.tick(1)
    c.stop()
    p.tick(1)
    assert 0 < c.physical_current < c.stop_from
    p.tick(1)
    assert c.physical_current == 0
    assert c.status == "stopped"
    assert c.currents() == dict.fromkeys(mod.PHASES, 0.0)


def test_hardware_min_mismatch_rejects_command():
    c = Charger(1, initial_current=16, commanded_current=16, hardware_min=12)
    c.start()
    assert not c.command(11)
    assert c.commanded_current == 16
    assert c.command(12)


def test_energy_multiplier_does_not_accelerate_current_ramp():
    a, b = Parking(count=1, factor=1), Parking(count=1, factor=20)
    a.chargers[0].target_kwh = b.chargers[0].target_kwh = 100
    a.chargers[0].start()
    b.chargers[0].start()
    for _ in range(5):
        a.tick(1)
        b.tick(1)
    assert a.chargers[0].physical_current == b.chargers[0].physical_current
    assert abs(20 * a.chargers[0].delivered_kwh - b.chargers[0].delivered_kwh) < 1e-9


def test_energy_target_completed_then_real_two_second_stop():
    p = Parking(count=1, factor=100)
    c = p.chargers[0]
    c.target_kwh = 0.01
    c.start()
    for _ in range(60):
        p.tick(1)
        if c.status == "stopping":
            break
    assert c.status == "stopping"
    assert c.delivered_kwh == c.target_kwh
    p.tick(1)
    p.tick(1)
    assert c.status == "stopped"
    assert sum(c.currents().values()) == 0


def test_continuous_overload_timer_and_reset():
    p = Parking(count=1, trip_seconds=3, thresholds=dict.fromkeys(mod.PHASES, 1))
    p.chargers[0].start()
    for _ in range(3):
        p.tick(1)
    assert p.tripped
    p.reset_trip()
    assert not p.tripped
    assert p.seconds_over_limit == 0


def test_no_trip_if_overload_disappears_before_limit():
    p = Parking(count=1, trip_seconds=10, thresholds=dict.fromkeys(mod.PHASES, 1))
    c = p.chargers[0]
    c.start()
    p.tick(1)
    assert p.seconds_over_limit == 1
    c.stop()
    p.tick(2)
    assert p.seconds_over_limit == 0
    assert not p.tripped


def test_scenario_catalog_and_configuration_cases():
    scenarios = json.loads((ROOT / "tests" / "simulator" / "scenarios.json").read_text())
    cases = scenarios["cases"]
    assert len(cases) >= 8
    assert {"hardware_min_mismatch", "ten_triphase", "mixed_modes",
            "energy_completion", "breaker_timeout", "unresponsive"} <= {
                x["id"] for x in cases
            }
    for case in cases:
        p = Parking(count=case["count"], factor=case["factor"],
                    thresholds=case["thresholds"], trip_seconds=case["trip_seconds"])
        assert len(p.chargers) == case["count"]
        for cfg in case["chargers"]:
            c = p.chargers[cfg["index"] - 1]
            c.mode = cfg["mode"]
            c.hardware_min = cfg.get("hardware_min", 8)
            c.initial_current = cfg["initial_current"]
            c.target_kwh = cfg["target_kwh"]
            assert c.start()


def test_resume_after_load_shedding_keeps_delivered_energy():
    parking = Parking(count=1, thresholds=dict.fromkeys(mod.PHASES, 100))
    charger = parking.chargers[0]
    charger.target_kwh = 50
    charger.start()
    for _ in range(10):
        parking.tick(1)
    previous_kwh = charger.delivered_kwh
    previous_initial = charger.initial_for_session
    charger.stop()
    parking.tick(2)
    assert charger.status == "stopped"
    assert charger.resume()
    assert charger.delivered_kwh == previous_kwh
    assert charger.initial_for_session == previous_initial


def test_ignore_commands_exposes_absent_acknowledgement():
    charger = Charger(1, initial_current=16)
    charger.start()
    charger.ignore_commands = True
    assert not charger.command(15)
    assert charger.commanded_current == 16
