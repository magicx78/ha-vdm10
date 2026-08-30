"""Tests for the open-only door lock."""

from __future__ import annotations

from homeassistant.components.lock import LockEntityFeature
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNKNOWN
from homeassistant.core import HomeAssistant

from .conftest import make_mock_api, resolve_entity_id, setup_integration


async def test_lock_created_open_only(hass: HomeAssistant) -> None:
    """The opener lock exists, has no lock state and only supports OPEN."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)

    entity_id = resolve_entity_id(hass, "lock", entry, "door_1_lock")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["supported_features"] == LockEntityFeature.OPEN


async def test_lock_open_triggers_door(hass: HomeAssistant) -> None:
    """lock.open sends the remote open command for door 1."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "lock", entry, "door_1_lock")

    await hass.services.async_call(
        "lock", "open", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    api.async_open_door.assert_awaited_once_with(1)
