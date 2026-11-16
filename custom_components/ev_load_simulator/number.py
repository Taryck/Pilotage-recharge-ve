"""Configurable values and the charging current control interface."""
from homeassistant.components.number import NumberEntity, NumberMode
from .entity import SimEntity

class SimNumber(SimEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = None

    def __init__(self, runtime, charger, key, name, min_value, max_value, unit=None, step=1):
        super().__init__(runtime, charger, key, name)
        self.key = key
        self._attr_native_min_value = min_value
        self._attr_native_max_value = max_value
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit

    @property
    def native_value(self):
        if self.charger is None:
            if self.key == "factor":
                return self.runtime.parking.factor
            if self.key == "trip_seconds":
                return self.runtime.parking.trip_seconds
            if self.key.startswith("threshold_"):
                return self.runtime.parking.thresholds[self.key[len("threshold_"):]]
        return getattr(self.charger, self.key)

    async def async_set_native_value(self, value):
        value = float(value)
        if self.charger is None:
            if self.key == "factor":
                self.runtime.parking.factor = value
            elif self.key == "trip_seconds":
                self.runtime.parking.trip_seconds = value
            elif self.key.startswith("threshold_"):
                self.runtime.parking.thresholds[self.key[len("threshold_"):]] = value
        elif self.key == "commanded_current":
            # On a failed command, keep the entity unchanged; the load manager
            # detects missing acknowledgement and can issue its STOP command.
            if not self.charger.command(value):
                self.runtime.hass.bus.async_fire(
                    "ev_simulator_command_rejected",
                    {"charger": self.charger.index, "requested_a": value})
        else:
            setattr(self.charger, self.key, value)
        self.runtime.notify()

async def async_setup_entry(hass, entry, async_add_entities):
    runtime = entry.runtime_data
    result = [
        SimNumber(runtime, None, "factor", "Facteur énergie", 1, 1000, step=1),
        SimNumber(runtime, None, "trip_seconds", "Durée dépassement maxi", 1, 3600, "s"),
    ]
    for phase in ("l1", "l2", "l3"):
        result.append(SimNumber(runtime, None, f"threshold_{phase}",
                                f"Seuil {phase.upper()}", 1, 1000, "A"))
    for charger in runtime.parking.chargers:
        result.extend([
            SimNumber(runtime, charger, "commanded_current", "Consigne courant", 0, 100, "A"),
            SimNumber(runtime, charger, "initial_current", "Courant de départ", 1, 100, "A"),
            SimNumber(runtime, charger, "hardware_min", "Minimum matériel", 1, 100, "A"),
            SimNumber(runtime, charger, "hardware_max", "Maximum matériel", 1, 100, "A"),
            SimNumber(runtime, charger, "target_kwh", "Cible énergie", 0.1, 1000, "kWh", step=0.1),
        ])
    async_add_entities(result)
