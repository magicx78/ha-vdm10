"""Tests for the per-person last-granted sensors."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.hikvision_access.api import HikvisionUser

from .conftest import (
    TEST_USERS,
    advance_time,
    make_burst,
    make_mock_api,
    resolve_entity_id,
    setup_integration,
)

PRIME_TIME = datetime.now().replace(microsecond=0) - timedelta(minutes=5)


def _as_state(when: datetime) -> str:
    """Expected sensor state for a device-local timestamp (HA renders UTC)."""
    aware = when.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_utc(aware).isoformat()


async def test_sensors_primed_from_history(hass: HomeAssistant) -> None:
    """Every person gets a sensor; primed history fills the value."""
    api = make_mock_api(make_burst(PRIME_TIME, "Alice", "1234567890"))
    entry = await setup_integration(hass, api)

    alice = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Alice"))
    assert alice.state == _as_state(PRIME_TIME)
    assert alice.attributes["employee_no"] == "****lice"
    assert alice.attributes["num_cards"] == 1

    bob = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Bob"))
    assert bob.state == STATE_UNKNOWN


async def test_sensor_updates_on_new_swipe(hass: HomeAssistant) -> None:
    """A new swipe moves the person's timestamp."""
    old_burst = make_burst(PRIME_TIME, "Alice", "1234567890")
    api = make_mock_api(old_burst)
    entry = await setup_integration(hass, api)

    new_time = PRIME_TIME + timedelta(minutes=2)
    api.async_get_acs_events.return_value = old_burst + make_burst(
        new_time, "Bob", "9876543210"
    )
    await advance_time(hass, 3)

    bob = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Bob"))
    assert bob.state == _as_state(new_time)


async def test_new_person_creates_sensor(hass: HomeAssistant) -> None:
    """A person added on the device gets a sensor without a reload."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)

    api.async_get_users.return_value = [
        *TEST_USERS,
        HikvisionUser(
            employee_no="Carol", name="Carol", user_type="normal", num_cards=1
        ),
    ]
    coordinator = entry.runtime_data.coordinator
    coordinator._last_user_refresh = datetime.now() - timedelta(hours=2)
    await advance_time(hass, 3)

    carol = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Carol"))
    assert carol is not None
    assert carol.state == STATE_UNKNOWN


async def test_removed_person_becomes_unavailable(hass: HomeAssistant) -> None:
    """A person removed from the device leaves an unavailable sensor."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)

    api.async_get_users.return_value = [TEST_USERS[0]]
    coordinator = entry.runtime_data.coordinator
    coordinator._last_user_refresh = datetime.now() - timedelta(hours=2)
    await advance_time(hass, 3)

    bob = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Bob"))
    assert bob.state == STATE_UNAVAILABLE
