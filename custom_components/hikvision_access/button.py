"""One open button per door relay."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessConfigEntry
from .coordinator import HikvisionAccessCoordinator
from .entity import HikvisionAccessEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one button per door from the device capabilities."""
    coordinator = entry.runtime_data.coordinator
    caps = coordinator.data.door_capabilities
    async_add_entities(
        HikvisionAccessDoorButton(coordinator, door_no)
        for door_no in range(caps.door_min, caps.door_max + 1)
    )


class HikvisionAccessDoorButton(HikvisionAccessEntity, ButtonEntity):
    """Pressing sends the remote open command for this door."""

    _attr_translation_key = "open_door"

    def __init__(self, coordinator: HikvisionAccessCoordinator, door_no: int) -> None:
        """Bind to one door number."""
        super().__init__(coordinator, f"door_{door_no}_open_button")
        self._door_no = door_no
        self._attr_translation_placeholders = {"door_no": str(door_no)}

    async def async_press(self) -> None:
        """Send the open command."""
        await self.coordinator.api.async_open_door(self._door_no)
