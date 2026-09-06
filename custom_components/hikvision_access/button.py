"""Buttons: one open button per door relay, plus a device restart."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.const import EntityCategory
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
    """Create one button per door from the device capabilities, plus restart."""
    coordinator = entry.runtime_data.coordinator
    caps = coordinator.data.door_capabilities
    async_add_entities(
        [
            *(
                HikvisionAccessDoorButton(coordinator, door_no)
                for door_no in range(caps.door_min, caps.door_max + 1)
            ),
            HikvisionAccessRestartButton(coordinator),
        ]
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


class HikvisionAccessRestartButton(HikvisionAccessEntity, ButtonEntity):
    """Reboots the device — meant for maintenance automations.

    The device drops off the network for a minute or two afterwards; the
    coordinator absorbs the failed polls in the meantime.
    """

    _attr_translation_key = "restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HikvisionAccessCoordinator) -> None:
        """Bind to the device."""
        super().__init__(coordinator, "restart_button")

    async def async_press(self) -> None:
        """Send the reboot command."""
        await self.coordinator.api.async_reboot()
