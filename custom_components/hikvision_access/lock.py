"""Open-only lock for door 1 (the door opener relay).

The device performs its own opening pulse for the RemoteControlDoor
command, so the configurable pulse length option is not needed here; it
exists for possible relay-style fallbacks on other firmwares.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.lock import LockEntity, LockEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessConfigEntry
from .coordinator import HikvisionAccessCoordinator
from .entity import HikvisionAccessEntity

DOOR_OPENER_NO = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the door opener lock."""
    async_add_entities([HikvisionAccessDoorLock(entry.runtime_data.coordinator)])


class HikvisionAccessDoorLock(HikvisionAccessEntity, LockEntity):
    """Door 1 as an open-only lock: no lock/unlock, only OPEN."""

    _attr_translation_key = "door_opener"
    _attr_supported_features = LockEntityFeature.OPEN

    def __init__(self, coordinator: HikvisionAccessCoordinator) -> None:
        """Bind to the opener door."""
        super().__init__(coordinator, f"door_{DOOR_OPENER_NO}_lock")

    @property
    def is_locked(self) -> bool | None:
        """The device reports no lock state — always unknown."""
        return None

    async def async_open(self, **kwargs: Any) -> None:
        """Trigger the door opener."""
        await self.coordinator.api.async_open_door(DOOR_OPENER_NO)
