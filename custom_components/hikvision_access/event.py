"""Event entities: one per door, fired for every card swipe."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessConfigEntry
from .const import EVENT_TYPES
from .coordinator import HikvisionAccessCoordinator
from .entity import HikvisionAccessEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one event entity per door from the device capabilities."""
    coordinator = entry.runtime_data.coordinator
    caps = coordinator.data.door_capabilities
    async_add_entities(
        HikvisionAccessDoorEvent(coordinator, door_no)
        for door_no in range(caps.door_min, caps.door_max + 1)
    )


class HikvisionAccessDoorEvent(HikvisionAccessEntity, EventEntity):
    """Card swipes at one door as a Home Assistant event entity.

    The device timestamp of the swipe is exposed as ``event_time`` so
    automations can schedule an exact delay from the moment the card was
    read instead of from the moment the poll caught it.
    """

    _attr_translation_key = "door"
    _attr_event_types = EVENT_TYPES

    def __init__(self, coordinator: HikvisionAccessCoordinator, door_no: int) -> None:
        """Bind to one door number."""
        super().__init__(coordinator, f"door_{door_no}_event")
        self._door_no = door_no
        self._attr_translation_placeholders = {"door_no": str(door_no)}

    @callback
    def _handle_coordinator_update(self) -> None:
        """Fire one HA event per new card event on this door."""
        data = self.coordinator.data
        if data:
            for event in data.new_events:
                if event.door_no != self._door_no:
                    continue
                event_type = event.event_type
                if event_type is None:
                    continue
                self._trigger_event(
                    event_type,
                    {
                        "person": event.name,
                        "door": event.door_no,
                        "card": self.redact(event.card_no),
                        "employee_no": self.redact(event.employee_no),
                        "event_time": event.time.isoformat(),
                        "minor": event.minor,
                    },
                )
        super()._handle_coordinator_update()
