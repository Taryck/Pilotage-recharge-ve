"""Shared entity implementation."""
from homeassistant.helpers.entity import Entity
from .const import DOMAIN

class SimEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, charger=None, suffix="", name=""):
        self.runtime = runtime
        self.charger = charger
        part = "parking" if charger is None else f"borne_{charger.index:02}"
        self._attr_unique_id = f"{DOMAIN}_{part}_{suffix}"
        self._attr_name = (f"Parking {name}" if charger is None
                           else f"VE SIM Borne {charger.index:02} {name}")

    @property
    def available(self):
        return True

    async def async_added_to_hass(self):
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))
