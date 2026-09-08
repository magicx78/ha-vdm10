"""Tests for the diagnostics download."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import AUDIO_OFF, make_mock_api, setup_integration


async def test_diagnostics_redact_and_report_audio(
    hass: HomeAssistant, hass_client: ClientSessionGenerator
) -> None:
    """Credentials are redacted; the two-way audio block is complete."""
    api = make_mock_api([])
    api.async_get_two_way_audio.return_value = AUDIO_OFF
    entry = await setup_integration(hass, api, options={"two_way_audio_guard": False})

    diag = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    assert diag["entry"]["data"]["password"] == "**REDACTED**"
    assert diag["entry"]["data"]["username"] == "**REDACTED**"
    assert diag["device_info"]["serial_number"] == "**REDACTED**"
    assert diag["device_info"]["model"] == "VDM10-VM-2W-2.0"
    assert diag["persons"] == {"count": 2, "employee_nos": ["****lice", "****"]}

    audio = diag["two_way_audio"]
    assert audio["state"] == "off"
    assert audio["compression"] == "G.711ulaw"
    assert audio["check_interval_seconds"] == 60
    assert audio["guard_enabled"] is False
    assert audio["repairs_count"] == 0
    assert audio["repair_failures"] == 0
    assert audio["issue_open"] is False
    assert audio["last_checked"] is not None

    assert "secret" not in str(diag)
