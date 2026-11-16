"""UI configuration for an installation and its charge points."""
from __future__ import annotations

from typing import Any
from types import MappingProxyType
from homeassistant.config_entries import ConfigSubentry

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_CHARGER_NAME, CONF_CONTROL, CONF_CURRENT_PREFIX, CONF_MAX,
    CONF_MIN, CONF_SETPOINT, CONF_START, CONF_STOP, CONF_THRESHOLD_PREFIX,
    DOMAIN, PHASES, SUBENTRY_TYPE_CHARGER,
)


def _current_selector():
    return selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))


def _entity_selector():
    return selector.EntitySelector(selector.EntitySelectorConfig())


def _setpoint_selector():
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=["number", "input_number"])
    )


def _threshold_selector():
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0.1, max=10000, step=0.1, mode="box", unit_of_measurement="A")
    )


def _charger_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    d = defaults or {}
    fields = {
        vol.Required(CONF_CHARGER_NAME, default=d.get(CONF_CHARGER_NAME, "")): str,
        vol.Required(CONF_SETPOINT, default=d.get(CONF_SETPOINT, vol.UNDEFINED)):
            _setpoint_selector(),
        vol.Required(CONF_CONTROL, default=d.get(CONF_CONTROL, vol.UNDEFINED)):
            _entity_selector(),
        vol.Required(CONF_START, default=d.get(CONF_START, "")): str,
        vol.Required(CONF_STOP, default=d.get(CONF_STOP, "")): str,
        vol.Required(CONF_MIN, default=d.get(CONF_MIN, 6)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1000, step=0.1, mode="box", unit_of_measurement="A")),
        vol.Required(CONF_MAX, default=d.get(CONF_MAX, 32)):
            selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=1000, step=0.1, mode="box", unit_of_measurement="A")),
    }
    for phase in PHASES:
        key = f"{CONF_CURRENT_PREFIX}{phase}"
        fields[vol.Optional(key, default=d[key] if d.get(key) else vol.UNDEFINED)] = _current_selector()
    return vol.Schema(fields)


def _validate_charger(hass, data: dict[str, Any]) -> str | None:
    if data[CONF_MIN] > data[CONF_MAX]:
        return "invalid_limits"
    if not data[CONF_START].strip() or not data[CONF_STOP].strip():
        return "missing_command_value"
    if data[CONF_START] == data[CONF_STOP]:
        return "same_command_values"
    entity_id = data[CONF_CONTROL]
    state = hass.states.get(entity_id)
    if entity_id.startswith("select.") and state is not None:
        options = state.attributes.get("options", [])
        if data[CONF_START] not in options or data[CONF_STOP] not in options:
            return "invalid_select_option"
    if entity_id.startswith(("switch.", "input_boolean.")):
        if {data[CONF_START], data[CONF_STOP]} != {"on", "off"}:
            return "invalid_boolean_values"
    return None


class EvLoadManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Create the electrical installation."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            # Normal manual configuration has no fixed unique ID: multiple sites
            # are supported. Simulation imports have their own stable identity.
            return self.async_create_entry(title="Gestion de recharge VE", data=user_input)
        schema = {}
        for phase in PHASES:
            schema[vol.Required(f"{CONF_CURRENT_PREFIX}{phase}")] = _current_selector()
            schema[vol.Required(f"{CONF_THRESHOLD_PREFIX}{phase}", default=30.0)] = _threshold_selector()
        return self.async_show_form(step_id="user", data_schema=vol.Schema(schema))

    async def async_step_import(self, user_input: dict[str, Any]) -> FlowResult:
        """Explicitly provision or update a simulator-owned installation.

        This step is only invoked by the simulator after the user confirms
        its own options flow. NEVER modify a manually configured entry.
        """
        if (
            user_input.get("origin") != "charging_station_simulator"
            or not (simulator_id := user_input.get("simulator_entry_id"))
            or not isinstance(user_input.get("simulator_chargers"), list)
        ):
            return self.async_abort(reason="invalid_import")

        unique_id = f"simulator_{simulator_id}"
        await self.async_set_unique_id(unique_id)
        config = {
            key: user_input[key]
            for phase in PHASES
            for key in (f"{CONF_CURRENT_PREFIX}{phase}",
                        f"{CONF_THRESHOLD_PREFIX}{phase}")
        }
        # Marker used to identify test configurations. Keep this in the entry
        # so that a later import can NEVER overwrite a real installation.
        config["simulator_entry_id"] = simulator_id
        name = str(user_input.get("simulator_name", "Simulated parking"))
        incoming = []
        seen = set()
        for charger in user_input["simulator_chargers"]:
            key = charger.get("simulator_key")
            if not key or key in seen:
                return self.async_abort(reason="invalid_import")
            seen.add(key)
            data = {k: v for k, v in charger.items() if k != "simulator_key"}
            if _validate_charger(self.hass, data) is not None:
                return self.async_abort(reason="invalid_import")
            incoming.append((key, data))

        existing_entry = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, unique_id
        )
        if existing_entry is None:
            return self.async_create_entry(
                title=f"Recharge VE - Test {name}", data=config,
                subentries=[
                    {
                        "subentry_type": SUBENTRY_TYPE_CHARGER,
                        "title": data[CONF_CHARGER_NAME],
                        "unique_id": key,
                        "data": data,
                    }
                    for key, data in incoming
                ],
            )

        # Protection against any accidental match with a manual entry.
        if existing_entry.data.get("simulator_entry_id") != simulator_id:
            return self.async_abort(reason="invalid_import")
        existing = {
            sub.unique_id: sub for sub in existing_entry.subentries.values()
            if sub.subentry_type == SUBENTRY_TYPE_CHARGER
        }
        updated = dict(existing_entry.subentries)
        for key, data in incoming:
            old = existing.get(key)
            subentry = ConfigSubentry(
                data=MappingProxyType(data),
                subentry_type=SUBENTRY_TYPE_CHARGER,
                title=data[CONF_CHARGER_NAME],
                unique_id=key,
                **({"subentry_id": old.subentry_id} if old else {}),
            )
            updated[subentry.subentry_id] = subentry
        # Keep previous subentries not in this simulation batch; no automatic
        # deletion of charger configuration is performed.
        self.hass.config_entries.async_update_entry(
            existing_entry, data=config, subentries=updated,
        )
        return self.async_abort(reason="already_configured")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return EvLoadManagerOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry):
        return {SUBENTRY_TYPE_CHARGER: ChargerSubentryFlow}


class EvLoadManagerOptionsFlow(config_entries.OptionsFlow):
    """Edit installation phase entities and thresholds."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        d = {**self.config_entry.data, **self.config_entry.options}
        schema = {}
        for phase in PHASES:
            key = f"{CONF_CURRENT_PREFIX}{phase}"
            schema[vol.Required(key, default=d.get(key, vol.UNDEFINED))] = _current_selector()
            key = f"{CONF_THRESHOLD_PREFIX}{phase}"
            schema[vol.Required(key, default=d.get(key, 30.0))] = _threshold_selector()
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))


class ChargerSubentryFlow(config_entries.ConfigSubentryFlow):
    """Add or edit a charger."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            error = _validate_charger(self.hass, user_input)
            if not error:
                return self.async_create_entry(title=user_input[CONF_CHARGER_NAME], data=user_input)
            return self.async_show_form(step_id="user", data_schema=_charger_schema(user_input), errors={"base": error})
        return self.async_show_form(step_id="user", data_schema=_charger_schema())

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None):
        existing = self._get_reconfigure_subentry()
        if user_input is not None:
            error = _validate_charger(self.hass, user_input)
            if not error:
                return self.async_update_reload_and_abort(
                    self._get_entry(), existing,
                    title=user_input[CONF_CHARGER_NAME], data=user_input,
                )
            return self.async_show_form(step_id="reconfigure", data_schema=_charger_schema(user_input), errors={"base": error})
        return self.async_show_form(step_id="reconfigure", data_schema=_charger_schema(dict(existing.data)))
