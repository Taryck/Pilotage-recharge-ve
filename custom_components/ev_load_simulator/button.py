"""Buttons to start individual charging cycles and reset the simulated breaker."""
from homeassistant.components.button import ButtonEntity
from .entity import SimEntity

class SimButton(SimEntity, ButtonEntity):
    def __init__(self, runtime, charger, key, name):
        super().__init__(runtime, charger, key, name)
        self.key = key

    async def async_press(self):
        if self.key == "reset":
            self.runtime.parking.reset_trip()
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
    ]
    for charger in runtime.parking.chargers:
        result.extend([
            SimButton(runtime, charger, "start", "Démarrer cycle"),
            SimButton(runtime, charger, "stop", "Arrêter cycle"),
        ])
    async_add_entities(result)
