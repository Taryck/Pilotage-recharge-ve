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
        elif self.key == "start" and not self.runtime.parking.tripped:
            self.charger.start()
        elif self.key == "stop":
            self.charger.stop()
        self.runtime.notify()

async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    result = [SimButton(runtime, None, "reset", "Réarmer le disjoncteur")]
    for charger in runtime.parking.chargers:
        result.extend([
            SimButton(runtime, charger, "start", "Démarrer cycle"),
            SimButton(runtime, charger, "stop", "Arrêter cycle"),
        ])
    async_add_entities(result)
