"""Setup number of simulated chargers and parking parameters."""
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import DOMAIN

def schema(defaults=None):
    d = defaults or {}
    return vol.Schema({
        vol.Required("count", default=d.get("count", 10)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=100, step=1, mode="box")),
        vol.Required("factor", default=d.get("factor", 5)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=1000, step=1, mode="box")),
        vol.Required("trip_seconds", default=d.get("trip_seconds", 15)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=3600, step=1, mode="box")),
        vol.Required("threshold_l1", default=d.get("threshold_l1", 50)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=1000, step=1, mode="box", unit_of_measurement="A")),
        vol.Required("threshold_l2", default=d.get("threshold_l2", 50)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=1000, step=1, mode="box", unit_of_measurement="A")),
        vol.Required("threshold_l3", default=d.get("threshold_l3", 50)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=1, max=1000, step=1, mode="box", unit_of_measurement="A")),
        vol.Required("seed", default=d.get("seed", 12345)): selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, max=999999999, step=1, mode="box")),
    })

class SimulatorConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id("parking")
        self._abort_if_unique_id_configured()
        if user_input is not None:
            return self.async_create_entry(title="Parking VE virtuel", data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return SimulatorOptionsFlow()

class SimulatorOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=schema({**self.config_entry.data, **self.config_entry.options}))
