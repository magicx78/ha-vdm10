"""Shared fixtures for the hikvision_access test suite.

Uses pytest-homeassistant-custom-component for the real ``hass`` fixture —
no namespace-package hacks needed. The fake aiohttp session below scripts
exact response sequences (including Digest 401 challenges), which keeps the
API tests independent of aiohttp internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.hikvision_access.api import (
    AccessEvent,
    DoorCapabilities,
    HikvisionAccessAPI,
    HikvisionDeviceInfo,
    HikvisionUser,
)
from custom_components.hikvision_access.const import DOMAIN

DIGEST_CHALLENGE_MD5 = (
    'Digest qop="auth", realm="testrealm", nonce="abc123", '
    'algorithm=MD5, opaque="xyz789"'
)
DIGEST_CHALLENGE_SHA256 = (
    'Digest qop="auth", realm="testrealm", nonce="abc123", '
    'algorithm=SHA-256, opaque="xyz789"'
)

TEST_DEVICE = HikvisionDeviceInfo(
    serial_number="VDM10-TEST0000000000000001",
    model="VDM10-VM-2W-2.0",
    firmware_version="V3.7.1",
    firmware_released="build 251112",
    device_name="Front Door",
    mac_address="00:11:22:33:44:55",
    device_type="doorStation",
)

TEST_DOOR_CAPS = DoorCapabilities(
    door_min=1, door_max=2, commands=("open", "close", "alwaysOpen", "resume")
)

TEST_USERS = [
    HikvisionUser(employee_no="Alice", name="Alice", user_type="normal", num_cards=1),
    HikvisionUser(employee_no="Bob", name="Bob", user_type="normal", num_cards=1),
]

TEST_ENTRY_DATA = {
    CONF_HOST: "192.0.2.10",
    CONF_PORT: 80,
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
    CONF_SSL: False,
    CONF_VERIFY_SSL: True,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make Home Assistant load custom_components/ from this repo."""
    return


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
    api.async_open_door = AsyncMock()
    return api


async def setup_integration(
    hass: HomeAssistant,
    api: MagicMock,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set the integration up for real against a mocked API."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Front Door",
        unique_id=TEST_DEVICE.serial_number,
        data=dict(TEST_ENTRY_DATA),
        options=options or {},
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.hikvision_access.HikvisionAccessAPI", return_value=api
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


def resolve_entity_id(
    hass: HomeAssistant, platform: str, entry: MockConfigEntry, suffix: str
) -> str:
    """Look an entity id up by its stable unique id."""
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{suffix}"
    )
    assert entity_id is not None, f"No {platform} entity for suffix {suffix}"
    return entity_id


async def advance_time(hass: HomeAssistant, seconds: float) -> None:
    """Fire the coordinator's timer and settle all resulting work."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


@dataclass
class FakeResponse:
    """A scripted HTTP response."""

    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    async def text(self) -> str:
        """Return the scripted body."""
        return self.body


class _ResponseContext:
    """Async context manager wrapping one scripted response."""

    def __init__(self, item: FakeResponse | Exception) -> None:
        self._item = item

    async def __aenter__(self) -> FakeResponse:
        if isinstance(self._item, Exception):
            raise self._item
        return self._item

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class FakeSession:
    """A fake aiohttp session replaying scripted responses in order.

    Also tracks concurrency so the semaphore cap is testable.
    """

    def __init__(self, responses: list[FakeResponse | Exception]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict[str, Any]]] = []
        self.active = 0
        self.max_active = 0

    def request(self, method: str, url: str, **kwargs: Any) -> _ResponseContext:
        """Record the call and hand out the next scripted response."""
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"No scripted response left for {method} {url}")
        return _ResponseContext(self.responses.pop(0))


class CountingSession(FakeSession):
    """FakeSession that yields control to measure real concurrency."""

    def request(self, method: str, url: str, **kwargs: Any) -> _ResponseContext:
        """Track peak concurrency around an await point."""
        outer = self

        class _CountingContext(_ResponseContext):
            async def __aenter__(self) -> FakeResponse:
                outer.active += 1
                outer.max_active = max(outer.max_active, outer.active)
                await asyncio.sleep(0)
                return await super().__aenter__()

            async def __aexit__(self, *_exc: object) -> bool:
                outer.active -= 1
                return False

        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"No scripted response left for {method} {url}")
        return _CountingContext(self.responses.pop(0))


def make_api(session: FakeSession, **kwargs: Any) -> HikvisionAccessAPI:
    """Build an API instance against a fake session."""
    return HikvisionAccessAPI(
        host="192.0.2.10",
        port=80,
        username="admin",
        password="secret",
        session=session,  # type: ignore[arg-type]
        **kwargs,
    )
