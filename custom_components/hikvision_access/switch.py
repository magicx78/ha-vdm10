"""Switches: the two-way audio channel and its guard.

The device switches its ISAPI two-way audio channel off by itself every
now and then (firmware update, torn ISAPI sessions, go2rtc restarts); the
first switch shows and controls that channel, the second one toggles the
guard that switches it back on automatically.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessConfigEntry
from .const import OPT_TWO_WAY_AUDIO_GUARD
from .coordinator import HikvisionAccessCoordinator
from .entity import HikvisionAccessEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the two-way audio switch and the guard switch."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        [
            HikvisionAccessTwoWayAudioSwitch(coordinator),
            HikvisionAccessTwoWayAudioGuardSwitch(coordinator),
        ]
    )


class HikvisionAccessTwoWayAudioSwitch(HikvisionAccessEntity, SwitchEntity):
    """The ISAPI two-way audio channel (intercom / announcements)."""

    _attr_translation_key = "two_way_audio"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:account-voice"

    def __init__(self, coordinator: HikvisionAccessCoordinator) -> None:
        """Bind to the device."""
        super().__init__(coordinator, "two_way_audio")

    @property
    def is_on(self) -> bool | None:
        """Channel state from the last check; None while unknown."""
        data = self.coordinator.data
        if data is None or data.two_way_audio is None:
            return None
        return data.two_way_audio.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Codec, last check time, repair counter and guard state."""
        data = self.coordinator.data
        state = data.two_way_audio if data else None
        last_checked = data.two_way_audio_last_checked if data else None
        return {
            "audio_compression": state.compression if state else None,
            "last_checked": last_checked.isoformat() if last_checked else None,
            "repairs_count": data.two_way_audio_repairs if data else 0,
            "guard_enabled": self.coordinator.two_way_audio_guard_enabled,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable the channel and re-read only this value."""
        await self.coordinator.async_set_two_way_audio(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable the channel (the guard, if on, will re-enable it)."""
        await self.coordinator.async_set_two_way_audio(False)


class HikvisionAccessTwoWayAudioGuardSwitch(HikvisionAccessEntity, SwitchEntity):
    """Mirrors the ``two_way_audio_guard`` option; toggling needs no reload."""

    _attr_translation_key = "two_way_audio_guard"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:auto-fix"

    def __init__(self, coordinator: HikvisionAccessCoordinator) -> None:
        """Bind to the device."""
        super().__init__(coordinator, "two_way_audio_guard")

    @property
    def is_on(self) -> bool:
        """The option value, read live."""
        return self.coordinator.two_way_audio_guard_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Store the option as enabled."""
        self._set_guard(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Store the option as disabled."""
        self._set_guard(False)

    def _set_guard(self, enabled: bool) -> None:
        entry = self.coordinator.config_entry
        self.hass.config_entries.async_update_entry(
            entry, options={**entry.options, OPT_TWO_WAY_AUDIO_GUARD: enabled}
        )
        self.async_write_ha_state()
