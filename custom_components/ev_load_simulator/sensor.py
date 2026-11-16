"""Actual simulated phase currents, energy, overload and configuration state."""
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from .entity import SimEntity

class SimSensor(SimEntity, SensorEntity):
    def __init__(self, runtime, charger, key, name, unit=None):
        super().__init__(runtime, charger, key, name)
        self.key = key
        self._attr_native_unit_of_measurement = unit
        if key in ("energy",):
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING

    @property
    def native_value(self):
        parking = self.runtime.parking
        if self.charger is None:
            if self.key.startswith("total_"):
                return parking.totals()[self.key[6:]]
            if self.key.startswith("excess_"):
                return parking.excess()[self.key[7:]]
            if self.key == "overload_seconds":
                return round(parking.seconds_over_limit, 2)
            if self.key == "trip":
                return "coupure" if parking.tripped else "normal"
        else:
            if self.key.startswith("current_"):
                return self.charger.currents()[self.key[8:]]
            if self.key == "energy":
                return round(self.charger.delivered_kwh, 5)
            if self.key == "initial_session":
                return self.charger.initial_for_session
            if self.key == "physical":
                return round(self.charger.physical_current, 3)
        return None

    @property
    def extra_state_attributes(self):
        if self.charger is None and self.key == "trip":
            return {"total_currents_a": self.runtime.parking.totals(),
                    "excess_a": self.runtime.parking.excess(),
                    "thresholds_a": self.runtime.parking.thresholds,
                    "duration_seconds": self.runtime.parking.trip_seconds}
        if self.charger and self.key == "physical":
            return {"status": self.charger.status, "mode": self.charger.mode,
                    "energy_target_kwh": self.charger.target_kwh,
                    "command_a": self.charger.commanded_current}
        return None

async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    result = [SimSensor(runtime, None, "trip", "État disjoncteur")]
    result.append(SimSensor(runtime, None, "overload_seconds", "Durée dépassement", "s"))
    for phase in ("l1", "l2", "l3"):
        result += [
            SimSensor(runtime, None, f"total_{phase}", f"Courant total {phase.upper()}", "A"),
            SimSensor(runtime, None, f"excess_{phase}", f"Dépassement {phase.upper()}", "A"),
        ]
    for charger in runtime.parking.chargers:
        result += [
            SimSensor(runtime, charger, "energy", "Énergie délivrée", "kWh"),
            SimSensor(runtime, charger, "physical", "Courant réel moyen", "A"),
            SimSensor(runtime, charger, "initial_session", "Consigne initiale du cycle", "A"),
        ]
        for phase in ("l1", "l2", "l3"):
            result.append(SimSensor(runtime, charger, f"current_{phase}",
                                    f"Courant {phase.upper()}", "A"))
    async_add_entities(result)
