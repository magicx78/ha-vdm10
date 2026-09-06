"""Tests for the per-door open buttons and the restart button."""

from __future__ import annotations

from homeassistant.components.button import ButtonDeviceClass
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

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


async def test_restart_button_reboots_device(hass: HomeAssistant) -> None:
    """The restart button is a config-category restart control."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "button", entry, "restart_button")

    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry is not None
    assert registry_entry.entity_category == "config"
    assert hass.states.get(entity_id).attributes["device_class"] == (
        ButtonDeviceClass.RESTART
    )

    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    api.async_reboot.assert_awaited_once()
    api.async_open_door.assert_not_awaited()
