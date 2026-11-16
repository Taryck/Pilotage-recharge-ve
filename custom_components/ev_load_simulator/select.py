"""Mode, command and fault injection selector entities."""
from homeassistant.components.select import SelectEntity
from .entity import SimEntity

class SimSelect(SimEntity, SelectEntity):
    def __init__(self, runtime, charger, key, name, options):
        super().__init__(runtime, charger, key, name)
        self.key = key
        self._attr_options = options

    @property
    def current_option(self):
        if self.key == "mode":
            return {"tri": "Triphasé", "l1": "Monophasé L1",
                    "l2": "Monophasé L2", "l3": "Monophasé L3"}[self.charger.mode]
        if self.key == "fault":
            return "Ignore consignes" if self.charger.ignore_commands else "Normal"
        return ("Charge en cours" if self.charger.status == "charging"
                else "Arrêt en cours" if self.charger.status == "stopping"
                else "Charge Terminée")

    async def async_select_option(self, option):
        if self.key == "mode":
            self.charger.mode = {"Triphasé": "tri", "Monophasé L1": "l1",
                                 "Monophasé L2": "l2", "Monophasé L3": "l3"}[option]
        elif self.key == "fault":
            self.charger.ignore_commands = option == "Ignore consignes"
        elif self.key == "command":
            if option == "Charge en cours":
                if self.charger.status == "stopped" and not self.runtime.parking.tripped:
                    self.charger.start()
            elif option == "Charge Terminée":
                if not self.charger.ignore_commands:
                    self.charger.stop()
            else:
                raise ValueError(f"Unsupported command: {option}")
        self.runtime.notify()

async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    result = []
    for charger in runtime.parking.chargers:
        result.extend([
            SimSelect(runtime, charger, "command", "Charging Command",
                      ["Charge en cours", "Charge Terminée", "Arrêt en cours"]),
            SimSelect(runtime, charger, "mode", "Mode de charge",
                      ["Triphasé", "Monophasé L1", "Monophasé L2", "Monophasé L3"]),
            SimSelect(runtime, charger, "fault", "Simulation de panne",
                      ["Normal", "Ignore consignes"]),
        ])
    async_add_entities(result)
