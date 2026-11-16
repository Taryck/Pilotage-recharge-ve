"""Read-only regulation telemetry with stable translatable state values."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    CONF_CHARGER_NAME, CONF_CURRENT_PREFIX, CONF_MAX, CONF_MIN,
    CONF_SETPOINT, CONF_CONTROL, CONF_START, CONF_STOP, PHASES,
    CONF_THRESHOLD_PREFIX,
    SUBENTRY_TYPE_CHARGER, DOMAIN,
)

UNITS = {"A": 1.0, "mA": 0.001, "kA": 1000.0}


def current_in_amp(hass: HomeAssistant, entity_id: str | None) -> float | None:
    """Return measured current in A, None for missing/unavailable/invalid."""
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", "none", ""):
        return None
    factor = UNITS.get(state.attributes.get("unit_of_measurement", "A"))
    if factor is None:
        return None
    try:
        value = float(state.state) * factor
        if not -1e10 < value < 1e10:
            return None
        return round(value, 3)
    except (TypeError, ValueError):
        return None


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    entities = [InstallationStatus(hass, entry)]
    for sub in entry.subentries.values():
        if sub.subentry_type == SUBENTRY_TYPE_CHARGER:
            entities.extend((ChargerStatus(hass, entry, sub), ChargerSavedCurrent(entry, sub), ChargerSavedRunState(entry, sub)))
    for entity in entities:
        entity.hass = hass
    async_add_entities(entities)


class ChargerBase(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, sub) -> None:
        self.hass = None
        self.entry = entry
        self.sub = sub
        self._attr_unique_id = f"{entry.entry_id}_{sub.subentry_id}_{self.kind}"
        self._attr_name = None
        self._attr_translation_key = self.kind
        self._attr_translation_placeholders = {"charger": sub.title}

    async def async_added_to_hass(self):
        """Update saved-state sensors whenever a snapshot is persisted."""
        self.async_on_remove(
            self.hass.bus.async_listen("ev_load_manager_snapshot_updated", self._snapshot_updated)
        )

    @callback
    def _snapshot_updated(self, event):
        if event.data.get("entry_id") == self.entry.entry_id:
            self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.entry.entry_id}_{self.sub.subentry_id}")},
            name=self.sub.title,
            manufacturer="EV Load Manager",
            via_device=(DOMAIN, self.entry.entry_id),
        )

    @property
    def snapshot(self):
        return self.entry.runtime_data.snapshots.get(self.sub.subentry_id, {})


class ChargerSavedCurrent(ChargerBase):
    kind = "saved_current"
    label = "Consigne mémorisée"
    _attr_native_unit_of_measurement = "A"
    _attr_icon = "mdi:content-save"

    @property
    def native_value(self):
        return self.snapshot.get("initial_current_a")


class ChargerSavedRunState(ChargerBase):
    kind = "saved_running"
    label = "Commande mémorisée"
    _attr_icon = "mdi:content-save-check"

    @property
    def native_value(self):
        return self.snapshot.get("initial_command")


class ChargerStatus(ChargerBase):
    kind = "status"
    label = "État de recharge"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["monitoring"]
    _attr_icon = "mdi:ev-station"

    def __init__(self, hass, entry, sub):
        super().__init__(entry, sub)
        self.hass = hass

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        ids = [self.sub.data.get(f"{CONF_CURRENT_PREFIX}{p}") for p in PHASES]
        ids += [self.sub.data.get(CONF_SETPOINT), self.sub.data.get(CONF_CONTROL)]
        ids = list(filter(None, ids))
        if ids:
            self.async_on_remove(async_track_state_change_event(self.hass, ids, self._state_changed))

    @callback
    def _state_changed(self, event):
        self.async_write_ha_state()

    @property
    def native_value(self):
        return "monitoring"

    @property
    def extra_state_attributes(self):
        d = self.sub.data
        attributes = {
            "min_current_a": d[CONF_MIN], "max_current_a": d[CONF_MAX],
            "setpoint_entity": d[CONF_SETPOINT],
            "control_entity": d[CONF_CONTROL],
            "start_value": d[CONF_START], "stop_value": d[CONF_STOP],
            "saved_initial_current_a": self.snapshot.get("initial_current_a"),
            "saved_initial_command": self.snapshot.get("initial_command"),
            "saved_initial_phase_currents_a": self.snapshot.get("initial_phase_currents_a"),
            "stopped_by_module": self.snapshot.get("stopped_by_module", False),
        }
        for p in PHASES:
            ent = d.get(f"{CONF_CURRENT_PREFIX}{p}")
            attributes[f"{p}_entity"] = ent
            attributes[f"{p}_current_a"] = current_in_amp(self.hass, ent) if ent else 0.0
            attributes[f"{p}_valid"] = current_in_amp(self.hass, ent) is not None if ent else True
        return attributes


class InstallationStatus(SensorEntity):
    """Global supply currents and per-phase excess, visible in Home Assistant."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:transmission-tower"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["normal", "overload", "unavailable"]

    def __init__(self, hass, entry):
        self.hass = hass
        self.entry = entry
        self._attr_unique_id = f"{entry.entry_id}_installation_status"
        self._attr_name = None
        self._attr_translation_key = "installation_status"

    async def async_added_to_hass(self):
        d = {**self.entry.data, **self.entry.options}
        ids = [d.get(f"{CONF_CURRENT_PREFIX}{p}") for p in PHASES]
        self.async_on_remove(async_track_state_change_event(
            self.hass, list(filter(None, ids)), self._changed
        ))

    @callback
    def _changed(self, event):
        self.async_write_ha_state()

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, self.entry.entry_id)},
            name=self.entry.title,
            manufacturer="EV Load Manager",
        )

    def _values(self):
        d = {**self.entry.data, **self.entry.options}
        currents = {p: current_in_amp(self.hass, d.get(f"{CONF_CURRENT_PREFIX}{p}"))
                    for p in PHASES}
        limits = {p: float(d[f"{CONF_THRESHOLD_PREFIX}{p}"]) for p in PHASES}
        return currents, limits

    @property
    def native_value(self):
        currents, limits = self._values()
        if any(v is None for v in currents.values()):
            return "unavailable"
        return "overload" if any(currents[p] > limits[p] for p in PHASES) else "normal"

    @property
    def extra_state_attributes(self):
        currents, limits = self._values()
        result = {}
        for p in PHASES:
            result[f"{p}_current_a"] = currents[p]
            result[f"{p}_threshold_a"] = limits[p]
            result[f"{p}_excess_a"] = (
                round(max(0.0, currents[p] - limits[p]), 3)
                if currents[p] is not None else None
            )
        return result
