"""Buttons to start individual charging cycles and reset the simulated breaker."""
from homeassistant.components.button import ButtonEntity
import json
from pathlib import Path
from .entity import SimEntity

class SimButton(SimEntity, ButtonEntity):
    def __init__(self, runtime, charger, key, name):
        super().__init__(runtime, charger, key, name)
        self.key = key

    async def async_press(self):
        if self.key == "reset":
            self.runtime.parking.reset_trip()
        elif self.key == "scenario":
            parking = self.runtime.parking
            cases = json.loads((Path(__file__).parent / "scenarios.json").read_text(encoding="utf-8"))["cases"]
            case = next((item for item in cases if item["id"] == self.runtime.scenario_id), None)
            if case is None:
                return
            if case["count"] > parking.count:
                self.runtime.hass.bus.async_fire("ev_simulator_scenario_error", {
                    "scenario": case["id"], "reason": "Configure more chargers in simulator options"})
                return
            for charger in parking.chargers:
                charger.stop()
                charger.tick(2.0, parking.factor, parking.rng)
                charger.status = "stopped"
                charger.physical_current = 0.0
                charger.delivered_kwh = 0.0
                charger.initial_for_session = None
                charger.ignore_commands = False
            parking.factor = float(case["factor"])
            parking.trip_seconds = float(case["trip_seconds"])
            parking.thresholds = {p: float(case["thresholds"][p]) for p in ("l1", "l2", "l3")}
            parking.reset_trip()
            for item in case["chargers"]:
                charger = parking.chargers[item["index"] - 1]
                charger.mode = item.get("mode", "tri")
                charger.hardware_min = float(item.get("hardware_min", 8))
                charger.hardware_max = float(item.get("hardware_max", 32))
                charger.initial_current = float(item.get("initial_current", 16))
                charger.target_kwh = float(item.get("target_kwh", 25))
                charger.ignore_commands = bool(item.get("ignore_commands", False))
                charger.start()
        elif self.key == "random":
            parking = self.runtime.parking
            parking.reset_trip()
            for charger in parking.chargers:
                if charger.status != "stopped":
                    charger.stop()
                    charger.tick(2.0, parking.factor, parking.rng)
                charger.mode = parking.rng.choice(("tri", "l1", "l2", "l3"))
                charger.hardware_min = parking.rng.choice((6, 8, 10))
                charger.hardware_max = parking.rng.choice((20, 24, 32))
                charger.initial_current = float(parking.rng.randint(
                    int(charger.hardware_min), int(charger.hardware_max)))
                charger.target_kwh = float(parking.rng.randint(2, 50))
                charger.ignore_commands = False
                if parking.rng.random() < 0.85:
                    charger.start()
        elif self.key == "start" and not self.runtime.parking.tripped:
            self.charger.start()
        elif self.key == "stop":
            self.charger.stop()
        self.runtime.notify()

async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    result = [
        SimButton(runtime, None, "reset", "Réarmer le disjoncteur"),
        SimButton(runtime, None, "random", "Lancer cycles aléatoires"),
        SimButton(runtime, None, "scenario", "Appliquer scénario de test"),
    ]
    for charger in runtime.parking.chargers:
        result.extend([
            SimButton(runtime, charger, "start", "Démarrer cycle"),
            SimButton(runtime, charger, "stop", "Arrêter cycle"),
        ])
    async_add_entities(result)
