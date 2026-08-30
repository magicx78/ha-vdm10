"""Hikvision Access Control integration setup."""

from __future__ import annotations

from dataclasses import dataclass
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import HikvisionAccessAPI
from .const import DEFAULT_PORT, DEFAULT_USE_SSL, DEFAULT_VERIFY_SSL
from .coordinator import HikvisionAccessCoordinator

_LOGGER = logging.getLogger(__name__)

# Entity platforms arrive in M2 (event, sensor, lock, button).
PLATFORMS: list[Platform] = []


@dataclass
class HikvisionAccessRuntimeData:
    """Objects living for the lifetime of a config entry."""

    api: HikvisionAccessAPI
    coordinator: HikvisionAccessCoordinator


type HikvisionAccessConfigEntry = ConfigEntry[HikvisionAccessRuntimeData]


async def async_setup_entry(
    hass: HomeAssistant, entry: HikvisionAccessConfigEntry
) -> bool:
    """Set up one access-control device from a config entry."""
    api = HikvisionAccessAPI(
        host=entry.data[CONF_HOST],
        port=entry.data.get(CONF_PORT, DEFAULT_PORT),
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=async_get_clientsession(hass),
        use_ssl=entry.data.get(CONF_SSL, DEFAULT_USE_SSL),
        verify_ssl=entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )
    coordinator = HikvisionAccessCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = HikvisionAccessRuntimeData(api=api, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: HikvisionAccessConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_options_updated(
    hass: HomeAssistant, entry: HikvisionAccessConfigEntry
) -> None:
    """Apply changed options by reloading the entry."""
    await hass.config_entries.async_reload(entry.entry_id)
