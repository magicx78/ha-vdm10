"""Tests for the config, re-auth and options flows."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hikvision_access.api import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionDeviceInfo,
    HikvisionTimeoutError,
)
from custom_components.hikvision_access.const import (
    DOMAIN,
    OPT_DOOR_PULSE_SECONDS,
    OPT_MASK_CARD_DATA,
    OPT_POLL_INTERVAL,
)

from .conftest import TEST_DEVICE

VALIDATE = (
    "custom_components.hikvision_access.api.HikvisionAccessAPI.async_get_device_info"
)
SETUP_ENTRY = "custom_components.hikvision_access.async_setup_entry"

USER_INPUT = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 80,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


async def test_user_flow_happy_path(hass: HomeAssistant) -> None:
    """Full setup: form, validation, entry with the serial as unique_id."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    with (
        patch(VALIDATE, return_value=TEST_DEVICE),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Front Door"
    entry = result["result"]
    assert entry.unique_id == TEST_DEVICE.serial_number
    assert entry.data == USER_INPUT


async def test_user_flow_error_then_recovery(hass: HomeAssistant) -> None:
    """Each API error maps to its form error; a retry can still succeed."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    for exception, error_key in (
        (HikvisionAuthError("denied"), "invalid_auth"),
        (HikvisionTimeoutError("slow"), "timeout_connect"),
        (HikvisionConnectionError("down"), "cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ):
        with patch(VALIDATE, side_effect=exception):
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], USER_INPUT
            )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": error_key}

    with (
        patch(VALIDATE, return_value=TEST_DEVICE),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_duplicate_aborts_and_updates_host(
    hass: HomeAssistant,
) -> None:
    """The same serial aborts and refreshes host/port on the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    moved = {**USER_INPUT, CONF_HOST: "192.0.2.99"}
    with patch(VALIDATE, return_value=TEST_DEVICE):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], moved
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert entry.data[CONF_HOST] == "192.0.2.99"


async def test_reauth_flow_updates_credentials(hass: HomeAssistant) -> None:
    """Re-auth stores the new credentials on the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with (
        patch(VALIDATE, return_value=TEST_DEVICE),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "newpass"},
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "newpass"


async def test_reauth_flow_rejects_different_device(hass: HomeAssistant) -> None:
    """Re-auth against a different device (other serial) aborts."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    other_device = HikvisionDeviceInfo(
        serial_number="OTHER-SERIAL",
        model=TEST_DEVICE.model,
        firmware_version=TEST_DEVICE.firmware_version,
        firmware_released=TEST_DEVICE.firmware_released,
        device_name=TEST_DEVICE.device_name,
        mac_address=TEST_DEVICE.mac_address,
        device_type=TEST_DEVICE.device_type,
    )
    with patch(VALIDATE, return_value=other_device):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "newpass"},
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_PASSWORD] == "secret"


async def test_reconfigure_updates_host(hass: HomeAssistant) -> None:
    """Reconfigure repoints the entry at the device's new IP address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    moved = {**USER_INPUT, CONF_HOST: "10.0.0.99"}
    with (
        patch(VALIDATE, return_value=TEST_DEVICE),
        patch(SETUP_ENTRY, return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], moved
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "10.0.0.99"


async def test_reconfigure_rejects_different_device(hass: HomeAssistant) -> None:
    """Pointing the entry at a different serial number is refused."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    other_device = HikvisionDeviceInfo(
        serial_number="SOME-OTHER-DEVICE",
        model=TEST_DEVICE.model,
        firmware_version=TEST_DEVICE.firmware_version,
        firmware_released=TEST_DEVICE.firmware_released,
        device_name=TEST_DEVICE.device_name,
        mac_address=TEST_DEVICE.mac_address,
        device_type=TEST_DEVICE.device_type,
    )
    with patch(VALIDATE, return_value=other_device):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {**USER_INPUT, CONF_HOST: "10.0.0.99"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_HOST] == "192.0.2.10"


async def test_options_flow_roundtrip(hass: HomeAssistant) -> None:
    """Options flow stores poll interval, masking and pulse length."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_DEVICE.serial_number,
        data=USER_INPUT,
    )
    entry.add_to_hass(hass)
    with patch(SETUP_ENTRY, return_value=True):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"

        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                OPT_POLL_INTERVAL: 5,
                OPT_MASK_CARD_DATA: False,
                OPT_DOOR_PULSE_SECONDS: 3.0,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {
        OPT_POLL_INTERVAL: 5,
        OPT_MASK_CARD_DATA: False,
        OPT_DOOR_PULSE_SECONDS: 3.0,
    }
