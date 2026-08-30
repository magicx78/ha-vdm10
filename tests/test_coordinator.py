"""Tests for the polling coordinator: priming, dedup, windows, auth strikes."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api import AccessEvent, HikvisionAuthError
from custom_components.hikvision_access.const import (
    DOMAIN,
    OPT_POLL_INTERVAL,
    POLL_OVERLAP_SECONDS,
    STARTUP_LOOKBACK_SECONDS,
)
from custom_components.hikvision_access.coordinator import HikvisionAccessCoordinator

from .conftest import TEST_DEVICE, TEST_DOOR_CAPS, TEST_USERS


def make_burst(when: datetime, name: str, card: str) -> list[AccessEvent]:
    """One card swipe as the device logs it: accepted + three status events."""
    return [
        AccessEvent(
            time=when, minor=1, door_no=1, card_no=card, employee_no=name, name=name
        ),
        AccessEvent(
            time=when, minor=21, door_no=1, card_no="", employee_no="", name=""
        ),
        AccessEvent(
            time=when, minor=214, door_no=1, card_no="", employee_no="", name=""
        ),
        AccessEvent(
            time=when + timedelta(seconds=2),
            minor=22,
            door_no=1,
            card_no="",
            employee_no="",
            name="",
        ),
    ]


def make_mock_api(events: list[AccessEvent] | None = None) -> MagicMock:
    """API stub with sensible defaults for every coordinator call."""
    api = MagicMock()
    api.async_get_device_info = AsyncMock(return_value=TEST_DEVICE)
    api.async_get_door_capabilities = AsyncMock(return_value=TEST_DOOR_CAPS)
    api.async_get_users = AsyncMock(return_value=list(TEST_USERS))
    api.async_get_acs_events = AsyncMock(return_value=list(events or []))
    return api


async def make_coordinator(
    hass: HomeAssistant, api: MagicMock, options: dict | None = None
) -> HikvisionAccessCoordinator:
    """Build a coordinator on a mock entry and run the first refresh."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options=options or {})
    entry.add_to_hass(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_IN_PROGRESS)
    coordinator = HikvisionAccessCoordinator(hass, entry, api)
    await coordinator.async_config_entry_first_refresh()
    return coordinator


async def test_first_poll_primes_without_reporting(hass: HomeAssistant) -> None:
    """Historical events prime state but are never reported as new."""
    swipe_time = datetime.now().replace(microsecond=0) - timedelta(seconds=30)
    api = make_mock_api(make_burst(swipe_time, "Alice", "1234567890"))
    coordinator = await make_coordinator(hass, api)

    assert coordinator.last_update_success
    assert coordinator.data.new_events == []
    assert coordinator.data.last_event is None
    assert coordinator.data.last_granted["Alice"] == swipe_time
    assert coordinator.data.device_info == TEST_DEVICE
    assert coordinator.data.users["Alice"] == TEST_USERS[0]


async def test_new_swipe_is_reported_once(hass: HomeAssistant) -> None:
    """A new swipe appears exactly once; only card events are reported."""
    old_time = datetime.now().replace(microsecond=0) - timedelta(seconds=60)
    old_burst = make_burst(old_time, "Alice", "1234567890")
    api = make_mock_api(old_burst)
    coordinator = await make_coordinator(hass, api)

    new_time = old_time + timedelta(seconds=45)
    new_burst = make_burst(new_time, "Bob", "9876543210")
    api.async_get_acs_events.return_value = old_burst + new_burst
    await coordinator.async_refresh()

    assert coordinator.last_update_success
    assert len(coordinator.data.new_events) == 1
    event = coordinator.data.new_events[0]
    assert event.name == "Bob"
    assert event.minor == 1
    assert coordinator.data.last_event == event
    assert coordinator.data.last_granted["Bob"] == new_time

    # The same poll result again: everything deduplicated.
    await coordinator.async_refresh()
    assert coordinator.data.new_events == []
    assert coordinator.data.last_event == event


async def test_poll_window_follows_last_event(hass: HomeAssistant) -> None:
    """The window starts at lookback first, then trails the newest event."""
    api = make_mock_api([])
    before = datetime.now()
    coordinator = await make_coordinator(hass, api)

    first_start = api.async_get_acs_events.call_args[0][0]
    expected_first = before - timedelta(seconds=STARTUP_LOOKBACK_SECONDS)
    assert abs((first_start - expected_first).total_seconds()) < 5

    swipe_time = datetime.now().replace(microsecond=0)
    api.async_get_acs_events.return_value = make_burst(
        swipe_time, "Alice", "1234567890"
    )
    await coordinator.async_refresh()
    await coordinator.async_refresh()

    # The status event two seconds after the swipe is the newest log entry.
    last_start = api.async_get_acs_events.call_args[0][0]
    newest = swipe_time + timedelta(seconds=2)
    assert last_start == newest - timedelta(seconds=POLL_OVERLAP_SECONDS)


async def test_auth_errors_need_three_strikes(hass: HomeAssistant) -> None:
    """Sporadic 401s stay transient; the third in a row triggers re-auth."""
    api = make_mock_api([])
    coordinator = await make_coordinator(hass, api)

    api.async_get_acs_events.side_effect = HikvisionAuthError("denied")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)

    await coordinator.async_refresh()
    assert isinstance(coordinator.last_exception, UpdateFailed)

    await coordinator.async_refresh()
    assert isinstance(coordinator.last_exception, ConfigEntryAuthFailed)


async def test_success_resets_auth_strikes(hass: HomeAssistant) -> None:
    """A successful poll between 401s resets the strike counter."""
    api = make_mock_api([])
    coordinator = await make_coordinator(hass, api)

    api.async_get_acs_events.side_effect = HikvisionAuthError("denied")
    await coordinator.async_refresh()
    await coordinator.async_refresh()

    api.async_get_acs_events.side_effect = None
    api.async_get_acs_events.return_value = []
    await coordinator.async_refresh()
    assert coordinator.last_update_success

    api.async_get_acs_events.side_effect = HikvisionAuthError("denied")
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_connection_error_becomes_update_failed(hass: HomeAssistant) -> None:
    """Transport errors mark the update failed without touching re-auth."""
    from custom_components.hikvision_access.api import HikvisionConnectionError

    api = make_mock_api([])
    coordinator = await make_coordinator(hass, api)

    api.async_get_acs_events.side_effect = HikvisionConnectionError("down")
    await coordinator.async_refresh()
    assert not coordinator.last_update_success
    assert isinstance(coordinator.last_exception, UpdateFailed)


async def test_user_refresh_when_due(hass: HomeAssistant) -> None:
    """The person list refreshes once its interval elapsed; new persons appear."""
    api = make_mock_api([])
    coordinator = await make_coordinator(hass, api)
    assert set(coordinator.data.users) == {"Alice", "Bob"}
    assert api.async_get_users.await_count == 1

    from custom_components.hikvision_access.api import HikvisionUser

    api.async_get_users.return_value = [
        *TEST_USERS,
        HikvisionUser(
            employee_no="Carol", name="Carol", user_type="normal", num_cards=1
        ),
    ]
    coordinator._last_user_refresh = datetime.now() - timedelta(hours=2)
    await coordinator.async_refresh()

    assert set(coordinator.data.users) == {"Alice", "Bob", "Carol"}
    assert api.async_get_users.await_count == 2


async def test_poll_interval_comes_from_options(hass: HomeAssistant) -> None:
    """The options flow value drives the update interval."""
    api = make_mock_api([])
    coordinator = await make_coordinator(hass, api, options={OPT_POLL_INTERVAL: 7})
    assert coordinator.update_interval == timedelta(seconds=7)
