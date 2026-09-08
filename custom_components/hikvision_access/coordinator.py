"""Polling coordinator for Hikvision Access Control.

Transport decision (M0, 2026-08-30, VDM10 firmware V3.7.1 build 251112):
the device never emits AccessControllerEvent on its alert stream and cannot
push access events to an HTTP host, so the only transport is polling the
AcsEvent log. Consecutive poll windows overlap and events are deduplicated
by their log identity, so no swipe is lost and none is reported twice.

The very first poll only primes state (last-granted timestamps, dedup
memory) and reports no new events — a Home Assistant restart must never
replay a swipe into automations.

Two-way audio guard: the reference firmware switches its ISAPI two-way
audio channel off on its own (after firmware updates, torn ISAPI sessions,
go2rtc restarts), which silently kills intercom and announcements. The
coordinator checks the channel on a throttled schedule inside the normal
poll and, if the guard option is on, switches it back on. That check is
fully isolated: whatever happens to it, the event poll and the existing
entities are unaffected.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AccessEvent,
    DoorCapabilities,
    HikvisionAccessAPI,
    HikvisionAccessError,
    HikvisionAuthError,
    HikvisionDeviceInfo,
    HikvisionUser,
    TwoWayAudioState,
)
from .const import (
    AUTH_FAILURE_MIN_SECONDS,
    AUTH_FAILURE_THRESHOLD,
    DEDUP_CACHE_SIZE,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_TWO_WAY_AUDIO_CHANNEL,
    DEFAULT_TWO_WAY_AUDIO_CHECK_INTERVAL,
    DEFAULT_TWO_WAY_AUDIO_GUARD,
    DOMAIN,
    EVENT_MINOR_ACCEPTED,
    EVENT_TWO_WAY_AUDIO_RESTORED,
    ISSUE_TWO_WAY_AUDIO_DISABLED,
    MAX_TWO_WAY_AUDIO_CHECK_INTERVAL,
    MIN_TWO_WAY_AUDIO_CHECK_INTERVAL,
    OPT_POLL_INTERVAL,
    OPT_TWO_WAY_AUDIO_CHECK_INTERVAL,
    OPT_TWO_WAY_AUDIO_GUARD,
    POLL_OVERLAP_SECONDS,
    STARTUP_LOOKBACK_SECONDS,
    TRANSIENT_FAILURE_TOLERANCE,
    TWO_WAY_AUDIO_REPAIR_ATTEMPTS,
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
    # Two-way audio channel: None = unknown (never checked or last check failed).
    two_way_audio: TwoWayAudioState | None = None
    two_way_audio_last_checked: datetime | None = None
    two_way_audio_repairs: int = 0


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
        self._first_auth_failure: datetime | None = None
        self._poll_failures = 0
        self._device_info: HikvisionDeviceInfo | None = None
        self._door_capabilities: DoorCapabilities | None = None
        self._users: dict[str, HikvisionUser] = {}
        self._last_granted: dict[str, datetime] = {}
        self._last_event: AccessEvent | None = None
        self._two_way_audio: TwoWayAudioState | None = None
        self._two_way_audio_last_check: datetime | None = None
        self._two_way_audio_repairs = 0
        self._two_way_audio_repair_failures = 0
        self._two_way_audio_issue_open = False

    # --- live options ------------------------------------------------------

    @property
    def two_way_audio_guard_enabled(self) -> bool:
        """Whether the guard re-enables a switched-off channel (read live)."""
        return bool(
            self.config_entry.options.get(
                OPT_TWO_WAY_AUDIO_GUARD, DEFAULT_TWO_WAY_AUDIO_GUARD
            )
        )

    @property
    def two_way_audio_check_interval(self) -> int:
        """Seconds between two channel checks (read live, clamped)."""
        raw = self.config_entry.options.get(
            OPT_TWO_WAY_AUDIO_CHECK_INTERVAL, DEFAULT_TWO_WAY_AUDIO_CHECK_INTERVAL
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = DEFAULT_TWO_WAY_AUDIO_CHECK_INTERVAL
        return max(
            MIN_TWO_WAY_AUDIO_CHECK_INTERVAL,
            min(MAX_TWO_WAY_AUDIO_CHECK_INTERVAL, value),
        )

    @property
    def two_way_audio_repair_failures(self) -> int:
        """Consecutive failed repair attempts (diagnostics)."""
        return self._two_way_audio_repair_failures

    @property
    def two_way_audio_issue_open(self) -> bool:
        """Whether the repairs issue is currently raised (diagnostics)."""
        return self._two_way_audio_issue_open

    async def _async_setup(self) -> None:
        """One-time initialization: identity, door layout, person list."""
        try:
            self._device_info = await self.api.async_get_device_info()
            self._door_capabilities = await self.api.async_get_door_capabilities()
            self._users = {
                user.employee_no: user for user in await self.api.async_get_users()
            }
            self._last_user_refresh = datetime.now()
        except HikvisionAccessError as err:
            # Also for auth errors: during setup a rejection is far more
            # likely a firmware hiccup than a wrong password (the config
            # flow validated the credentials moments earlier). UpdateFailed
            # lets Home Assistant retry with backoff instead of nagging for
            # credentials; re-auth only ever starts from the gated streak
            # logic in _async_update_data.
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
            if self._first_auth_failure is None:
                self._first_auth_failure = now
            streak_seconds = (now - self._first_auth_failure).total_seconds()
            if (
                self._auth_failures >= AUTH_FAILURE_THRESHOLD
                and streak_seconds >= AUTH_FAILURE_MIN_SECONDS
            ):
                raise ConfigEntryAuthFailed(str(err)) from err
            return self._transient_failure(
                f"auth error ({self._auth_failures}/{AUTH_FAILURE_THRESHOLD}, "
                f"{streak_seconds:.0f}s): {err}",
                err,
            )
        except HikvisionAccessError as err:
            return self._transient_failure(str(err), err)

        self._auth_failures = 0
        self._first_auth_failure = None
        self._poll_failures = 0
        new_events = self._extract_new_events(events)

        # Strictly after the event poll and outside its error handling: a
        # failing channel check must never cost a swipe or an entity — not
        # even on a bug in the guard itself, hence the broad catch.
        try:
            await self._async_guard_two_way_audio()
        except Exception:
            _LOGGER.exception("Two-way audio guard failed unexpectedly")

        if self._device_info is None or self._door_capabilities is None:
            raise UpdateFailed("Coordinator polled before setup completed")
        return self._build_data(new_events)

    def _build_data(self, new_events: list[AccessEvent]) -> HikvisionAccessData:
        """Assemble the data object from the coordinator's current state."""
        assert self._device_info is not None
        assert self._door_capabilities is not None
        return HikvisionAccessData(
            device_info=self._device_info,
            door_capabilities=self._door_capabilities,
            users=dict(self._users),
            last_granted=dict(self._last_granted),
            new_events=new_events,
            last_event=self._last_event,
            two_way_audio=self._two_way_audio,
            two_way_audio_last_checked=self._two_way_audio_last_check,
            two_way_audio_repairs=self._two_way_audio_repairs,
        )

    def _transient_failure(
        self, message: str, err: HikvisionAccessError
    ) -> HikvisionAccessData:
        """Absorb a failed poll, keeping the previous data alive.

        The firmware sporadically refuses single requests while other
        clients poll it (observed live: ~1 refusal/minute); entities must
        not flicker unavailable for that. Only a streak of failures — or
        having no data at all yet — becomes UpdateFailed.
        """
        self._poll_failures += 1
        if (
            self.data is None
            or self._device_info is None
            or self._door_capabilities is None
            or self._poll_failures >= TRANSIENT_FAILURE_TOLERANCE
        ):
            raise UpdateFailed(message) from err
        _LOGGER.debug(
            "Transient poll failure (%d/%d), keeping previous data: %s",
            self._poll_failures,
            TRANSIENT_FAILURE_TOLERANCE,
            message,
        )
        return self._build_data([])

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

    # --- two-way audio guard -------------------------------------------------

    def _two_way_audio_check_due(self, now: datetime) -> bool:
        """Return True when the throttled channel check should run."""
        last = self._two_way_audio_last_check
        return (
            last is None
            or (now - last).total_seconds() >= self.two_way_audio_check_interval
        )

    async def _async_guard_two_way_audio(self) -> None:
        """Throttled channel check plus repair; never raises."""
        now = dt_util.utcnow()
        if not self._two_way_audio_check_due(now):
            return
        # Stamp before the request: a failing check waits a full interval
        # instead of adding a doomed request to every 2 s poll cycle.
        self._two_way_audio_last_check = now
        try:
            state = await self.api.async_get_two_way_audio(
                DEFAULT_TWO_WAY_AUDIO_CHANNEL
            )
        except HikvisionAccessError as err:
            _LOGGER.debug("Two-way audio check failed, state unknown: %s", err)
            self._two_way_audio = None
            return
        self._two_way_audio = state
        if state.enabled:
            self._async_two_way_audio_healthy()
            return
        if not self.two_way_audio_guard_enabled:
            _LOGGER.debug("Two-way audio is disabled on the device; guard is off")
            return
        await self._async_repair_two_way_audio(state)

    async def _async_repair_two_way_audio(self, state: TwoWayAudioState) -> None:
        """Switch the channel back on and confirm it; track failures."""
        try:
            await self.api.async_set_two_way_audio(
                state.channel, True, compression=state.compression
            )
            confirmed = await self.api.async_get_two_way_audio(state.channel)
        except HikvisionAccessError as err:
            self._async_two_way_audio_repair_failed(f"request failed: {err}")
            return
        self._two_way_audio = confirmed
        if not confirmed.enabled:
            self._async_two_way_audio_repair_failed(
                "device reported the channel still disabled"
            )
            return
        self._two_way_audio_repairs += 1
        _LOGGER.info("Two-way audio was disabled by the device, re-enabled")
        self.hass.bus.async_fire(
            EVENT_TWO_WAY_AUDIO_RESTORED,
            {
                "entry_id": self.config_entry.entry_id,
                "device": self.config_entry.title,
                "channel": confirmed.channel,
                "timestamp": dt_util.utcnow().isoformat(),
                "repairs": self._two_way_audio_repairs,
            },
        )
        self._async_two_way_audio_healthy()

    def _async_two_way_audio_repair_failed(self, reason: str) -> None:
        """Count a failed repair; raise the repairs issue after a streak."""
        self._two_way_audio_repair_failures += 1
        _LOGGER.warning(
            "Two-way audio is disabled and could not be re-enabled (%d/%d): %s",
            self._two_way_audio_repair_failures,
            TWO_WAY_AUDIO_REPAIR_ATTEMPTS,
            reason,
        )
        if (
            self._two_way_audio_repair_failures >= TWO_WAY_AUDIO_REPAIR_ATTEMPTS
            and not self._two_way_audio_issue_open
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self._two_way_audio_issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=ISSUE_TWO_WAY_AUDIO_DISABLED,
                translation_placeholders={"device": self.config_entry.title},
            )
            self._two_way_audio_issue_open = True

    def _async_two_way_audio_healthy(self) -> None:
        """Channel is on: reset the failure streak, clear the issue."""
        self._two_way_audio_repair_failures = 0
        if self._two_way_audio_issue_open:
            ir.async_delete_issue(self.hass, DOMAIN, self._two_way_audio_issue_id)
            self._two_way_audio_issue_open = False

    @property
    def _two_way_audio_issue_id(self) -> str:
        return f"{ISSUE_TWO_WAY_AUDIO_DISABLED}_{self.config_entry.entry_id}"

    async def async_set_two_way_audio(self, enabled: bool) -> None:
        """Entity-driven change: PUT, then re-read only this value.

        No full event poll is triggered; the poll timer keeps its rhythm.
        """
        current = self._two_way_audio
        await self.api.async_set_two_way_audio(
            current.channel if current else DEFAULT_TWO_WAY_AUDIO_CHANNEL,
            enabled,
            compression=current.compression if current else None,
        )
        await self.async_refresh_two_way_audio()

    async def async_refresh_two_way_audio(self) -> None:
        """Re-read the channel state and publish it without an event poll."""
        self._two_way_audio_last_check = dt_util.utcnow()
        try:
            self._two_way_audio = await self.api.async_get_two_way_audio(
                DEFAULT_TWO_WAY_AUDIO_CHANNEL
            )
        except HikvisionAccessError as err:
            _LOGGER.debug("Two-way audio refresh failed, state unknown: %s", err)
            self._two_way_audio = None
        else:
            if self._two_way_audio.enabled:
                self._async_two_way_audio_healthy()
        self.async_publish_state()

    def async_publish_state(self) -> None:
        """Push the current state to entities outside a poll.

        ``new_events`` is cleared deliberately: the event entities fire one
        Home Assistant event per entry in it on every listener call, so an
        out-of-band publish must never carry the last poll's swipes again.
        """
        if self.data is None:
            return
        self.data = replace(
            self.data,
            new_events=[],
            two_way_audio=self._two_way_audio,
            two_way_audio_last_checked=self._two_way_audio_last_check,
            two_way_audio_repairs=self._two_way_audio_repairs,
        )
        self.async_update_listeners()
