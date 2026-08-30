"""Per-person sensors: timestamp of the last granted access."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HikvisionAccessConfigEntry
from .coordinator import HikvisionAccessCoordinator
from .entity import HikvisionAccessEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create a sensor per person; new persons appear automatically."""
    coordinator = entry.runtime_data.coordinator
    known: set[str] = set()

    @callback
    def _sync_persons() -> None:
        data = coordinator.data
        if not data:
            return
        new = [employee_no for employee_no in data.users if employee_no not in known]
        if new:
            known.update(new)
            async_add_entities(
                HikvisionAccessPersonSensor(coordinator, employee_no)
                for employee_no in new
            )

    _sync_persons()
    entry.async_on_unload(coordinator.async_add_listener(_sync_persons))


class HikvisionAccessPersonSensor(HikvisionAccessEntity, SensorEntity):
    """When this person last got access granted."""

    _attr_translation_key = "last_granted"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(
        self, coordinator: HikvisionAccessCoordinator, employee_no: str
    ) -> None:
        """Bind to one person by their stable employee number."""
        super().__init__(coordinator, f"person_{employee_no}")
        self._employee_no = employee_no
        user = (coordinator.data.users if coordinator.data else {}).get(employee_no)
        self._attr_translation_placeholders = {
            "person": user.name if user else employee_no
        }

    @property
    def available(self) -> bool:
        """Unavailable once the person was removed from the device."""
        data = self.coordinator.data
        return (
            super().available and data is not None and self._employee_no in data.users
        )

    @property
    def native_value(self) -> datetime | None:
        """Device-local wall-clock time of the last grant, made tz-aware."""
        data = self.coordinator.data
        if not data:
            return None
        when = data.last_granted.get(self._employee_no)
        if when is None:
            return None
        return when.replace(tzinfo=dt_util.get_default_time_zone())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Expose masked identity details and the card count."""
        data = self.coordinator.data
        user = data.users.get(self._employee_no) if data else None
        return {
            "employee_no": self.redact(self._employee_no),
            "num_cards": user.num_cards if user else None,
        }
