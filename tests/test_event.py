"""Tests for the per-door event entities."""

from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from custom_components.hikvision_access.api import AccessEvent
from custom_components.hikvision_access.const import (
    EVENT_TYPE_ACCEPTED,
    EVENT_TYPE_REJECTED,
    EVENT_TYPE_UNKNOWN,
    EVENT_TYPES,
    OPT_MASK_CARD_DATA,
)

from .conftest import (
    advance_time,
    make_burst,
    make_mock_api,
    resolve_entity_id,
    setup_integration,
)

PRIME_TIME = datetime.now().replace(microsecond=0) - timedelta(minutes=5)


async def test_event_entities_created_per_door(hass: HomeAssistant) -> None:
    """One event entity per door from the capabilities, initially unknown."""
    api = make_mock_api(make_burst(PRIME_TIME, "Alice", "1234567890"))
    entry = await setup_integration(hass, api)

    for door_no in (1, 2):
        entity_id = resolve_entity_id(hass, "event", entry, f"door_{door_no}_event")
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN
        assert state.attributes["event_types"] == EVENT_TYPES


async def test_accepted_swipe_fires_with_masked_card(hass: HomeAssistant) -> None:
    """A new swipe fires card_accepted on its door with masked identifiers."""
    old_burst = make_burst(PRIME_TIME, "Alice", "1234567890")
    api = make_mock_api(old_burst)
    entry = await setup_integration(hass, api)

    new_time = PRIME_TIME + timedelta(minutes=1)
    api.async_get_acs_events.return_value = old_burst + make_burst(
        new_time, "Bob", "9876543210"
    )
    await advance_time(hass, 3)

    door1 = hass.states.get(resolve_entity_id(hass, "event", entry, "door_1_event"))
    assert door1.state != STATE_UNKNOWN
    assert door1.attributes["event_type"] == EVENT_TYPE_ACCEPTED
    assert door1.attributes["person"] == "Bob"
    assert door1.attributes["door"] == 1
    assert door1.attributes["card"] == "****3210"
    assert door1.attributes["employee_no"] == "****"
    assert door1.attributes["event_time"] == new_time.isoformat()
    assert door1.attributes["minor"] == 1

    door2 = hass.states.get(resolve_entity_id(hass, "event", entry, "door_2_event"))
    assert door2.state == STATE_UNKNOWN


async def test_masking_can_be_disabled(hass: HomeAssistant) -> None:
    """With the option off, full card and employee numbers are exposed."""
    old_burst = make_burst(PRIME_TIME, "Alice", "1234567890")
    api = make_mock_api(old_burst)
    entry = await setup_integration(hass, api, options={OPT_MASK_CARD_DATA: False})

    new_time = PRIME_TIME + timedelta(minutes=1)
    api.async_get_acs_events.return_value = old_burst + make_burst(
        new_time, "Bob", "9876543210"
    )
    await advance_time(hass, 3)

    door1 = hass.states.get(resolve_entity_id(hass, "event", entry, "door_1_event"))
    assert door1.attributes["card"] == "9876543210"
    assert door1.attributes["employee_no"] == "Bob"


async def test_unknown_and_rejected_classification(hass: HomeAssistant) -> None:
    """A card without a person is unknown; other minors are rejected."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "event", entry, "door_1_event")

    stranger_time = PRIME_TIME + timedelta(minutes=2)
    api.async_get_acs_events.return_value = [
        AccessEvent(
            time=stranger_time,
            minor=1,
            door_no=1,
            card_no="5555555555",
            employee_no="",
            name="",
        )
    ]
    await advance_time(hass, 3)
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == EVENT_TYPE_UNKNOWN
    assert state.attributes["person"] == ""

    rejected_time = stranger_time + timedelta(seconds=30)
    api.async_get_acs_events.return_value = [
        AccessEvent(
            time=rejected_time,
            minor=57,
            door_no=1,
            card_no="1234567890",
            employee_no="Alice",
            name="Alice",
        )
    ]
    await advance_time(hass, 3)
    state = hass.states.get(entity_id)
    assert state.attributes["event_type"] == EVENT_TYPE_REJECTED
    assert state.attributes["person"] == "Alice"
    assert state.attributes["minor"] == 57
