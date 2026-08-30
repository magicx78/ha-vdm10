"""Polling coordinator for Hikvision Access Control.

Transport decision (M0, 2026-08-30, VDM10 firmware V3.7.1 build 251112):
the device never emits AccessControllerEvent on its alert stream and cannot
push access events to an HTTP host, so the only transport is polling the
AcsEvent log. Consecutive poll windows overlap and events are deduplicated
by their log identity, so no swipe is lost and none is reported twice.

The very first poll only primes state (last-granted timestamps, dedup
memory) and reports no new events — a Home Assistant restart must never
replay a swipe into automations.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    AccessEvent,
    DoorCapabilities,
    HikvisionAccessAPI,
    HikvisionAccessError,
    HikvisionAuthError,
    HikvisionDeviceInfo,
    HikvisionUser,
)
from .const import (
    AUTH_FAILURE_THRESHOLD,
    DEDUP_CACHE_SIZE,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    EVENT_MINOR_ACCEPTED,
    OPT_POLL_INTERVAL,
    POLL_OVERLAP_SECONDS,
    STARTUP_LOOKBACK_SECONDS,
    USER_REFRESH_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class HikvisionAccessData:
    """State shared with all entities. A fresh instance per poll."""

    device_info: HikvisionDeviceInfo
    door_capabilities: DoorCapabilities
    users: dict[str, HikvisionUser] = field(default_factory=dict)
    last_granted: dict[str, datetime] = field(default_factory=dict)
    new_events: list[AccessEvent] = field(default_factory=list)
    last_event: AccessEvent | None = None


class HikvisionAccessCoordinator(DataUpdateCoordinator[HikvisionAccessData]):
    """Polls the AcsEvent log and refreshes the person list hourly."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        api: HikvisionAccessAPI,
    ) -> None:
        """Initialize with the poll interval from the entry options."""
        poll_interval = config_entry.options.get(
            OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN}_{config_entry.title}",
            update_interval=timedelta(seconds=poll_interval),
        )
        self.api = api
        self._seen: OrderedDict[tuple, None] = OrderedDict()
        self._primed = False
        self._last_device_time: datetime | None = None
        self._last_user_refresh: datetime | None = None
        self._auth_failures = 0
        self._device_info: HikvisionDeviceInfo | None = None
        self._door_capabilities: DoorCapabilities | None = None
        self._users: dict[str, HikvisionUser] = {}
        self._last_granted: dict[str, datetime] = {}
        self._last_event: AccessEvent | None = None

    async def _async_setup(self) -> None:
        """One-time initialization: identity, door layout, person list."""
        try:
            self._device_info = await self.api.async_get_device_info()
            self._door_capabilities = await self.api.async_get_door_capabilities()
            self._users = {
                user.employee_no: user for user in await self.api.async_get_users()
            }
            self._last_user_refresh = datetime.now()
        except HikvisionAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except HikvisionAccessError as err:
            raise UpdateFailed(f"Device initialization failed: {err}") from err

    async def _async_update_data(self) -> HikvisionAccessData:
        """Poll the event log; return fresh data for the entities."""
        now = datetime.now()
        if self._last_device_time is not None:
            window_start = self._last_device_time - timedelta(
                seconds=POLL_OVERLAP_SECONDS
            )
        else:
            window_start = now - timedelta(seconds=STARTUP_LOOKBACK_SECONDS)

        try:
            events = await self.api.async_get_acs_events(window_start)
            if self._needs_user_refresh(now):
                await self._refresh_users(now)
        except HikvisionAuthError as err:
            self._auth_failures += 1
            if self._auth_failures >= AUTH_FAILURE_THRESHOLD:
                raise ConfigEntryAuthFailed(str(err)) from err
            raise UpdateFailed(
                f"Transient auth error "
                f"({self._auth_failures}/{AUTH_FAILURE_THRESHOLD}): {err}"
            ) from err
        except HikvisionAccessError as err:
            raise UpdateFailed(str(err)) from err

        self._auth_failures = 0
        new_events = self._extract_new_events(events)

        if self._device_info is None or self._door_capabilities is None:
            raise UpdateFailed("Coordinator polled before setup completed")
        return HikvisionAccessData(
            device_info=self._device_info,
            door_capabilities=self._door_capabilities,
            users=dict(self._users),
            last_granted=dict(self._last_granted),
            new_events=new_events,
            last_event=self._last_event,
        )

    def _extract_new_events(self, events: list[AccessEvent]) -> list[AccessEvent]:
        """Deduplicate a poll result and update derived state.

        Only events carrying a card number are reported (the device wraps
        every swipe in status events with empty card fields). On the priming
        poll everything is absorbed silently.
        """
        new_events: list[AccessEvent] = []
        for event in sorted(events, key=lambda item: item.time):
            key = event.dedup_key
            if key in self._seen:
                continue
            self._seen[key] = None
            while len(self._seen) > DEDUP_CACHE_SIZE:
                self._seen.popitem(last=False)
            if self._last_device_time is None or event.time > self._last_device_time:
                self._last_device_time = event.time
            if not event.card_no:
                continue
            if event.minor == EVENT_MINOR_ACCEPTED and event.employee_no:
                previous = self._last_granted.get(event.employee_no)
                if previous is None or event.time > previous:
                    self._last_granted[event.employee_no] = event.time
            if self._primed:
                new_events.append(event)
                self._last_event = event
        if not self._primed:
            self._primed = True
            if events:
                _LOGGER.debug(
                    "Primed with %d historical events; none reported", len(events)
                )
        return new_events

    def _needs_user_refresh(self, now: datetime) -> bool:
        """Return True when the hourly person refresh is due."""
        return (
            self._last_user_refresh is None
            or (now - self._last_user_refresh).total_seconds()
            >= USER_REFRESH_INTERVAL_SECONDS
        )

    async def _refresh_users(self, now: datetime) -> None:
        """Refresh the person list; failures keep the previous list."""
        try:
            self._users = {
                user.employee_no: user for user in await self.api.async_get_users()
            }
            self._last_user_refresh = now
        except HikvisionAuthError:
            raise
        except HikvisionAccessError as err:
            _LOGGER.debug("Person refresh failed, keeping previous list: %s", err)
