"""Tests for the per-door open buttons."""

from __future__ import annotations

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant

from .conftest import make_mock_api, resolve_entity_id, setup_integration


async def test_buttons_created_per_door(hass: HomeAssistant) -> None:
    """One button per door from the device capabilities."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)

    for door_no in (1, 2):
        entity_id = resolve_entity_id(
            hass, "button", entry, f"door_{door_no}_open_button"
        )
        assert hass.states.get(entity_id) is not None


async def test_button_press_opens_its_door(hass: HomeAssistant) -> None:
    """Pressing the door 2 button opens door 2."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "button", entry, "door_2_open_button")

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    api.async_open_door.assert_awaited_once_with(2)
