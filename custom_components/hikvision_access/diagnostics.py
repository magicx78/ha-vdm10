"""Diagnostics for Hikvision Access Control.

Credentials are redacted, card and employee numbers are masked (regardless
of the masking option) and person names are left out — a diagnostics
download ends up in GitHub issues.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import HikvisionAccessConfigEntry
from .entity import mask_identifier

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, "serial_number", "mac_address"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikvisionAccessConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data
    audio = data.two_way_audio if data else None
    last_event = data.last_event if data else None
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "device_info": (
            async_redact_data(asdict(data.device_info), TO_REDACT) if data else None
        ),
        "door_capabilities": asdict(data.door_capabilities) if data else None,
        "persons": {
            "count": len(data.users) if data else 0,
            "employee_nos": (
                [mask_identifier(no) for no in data.users] if data else []
            ),
        },
        "last_event": (
            {
                "time": last_event.time.isoformat(),
                "minor": last_event.minor,
                "door_no": last_event.door_no,
                "card_no": mask_identifier(last_event.card_no),
                "event_type": last_event.event_type,
            }
            if last_event
            else None
        ),
        "two_way_audio": {
            "state": (
                "on" if audio and audio.enabled else "off" if audio else "unknown"
            ),
            "channel": audio.channel if audio else None,
            "compression": audio.compression if audio else None,
            "last_checked": (
                data.two_way_audio_last_checked.isoformat()
                if data and data.two_way_audio_last_checked
                else None
            ),
            "check_interval_seconds": coordinator.two_way_audio_check_interval,
            "guard_enabled": coordinator.two_way_audio_guard_enabled,
            "repairs_count": data.two_way_audio_repairs if data else 0,
            "repair_failures": coordinator.two_way_audio_repair_failures,
            "issue_open": coordinator.two_way_audio_issue_open,
        },
    }
