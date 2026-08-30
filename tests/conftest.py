"""Shared fixtures for the hikvision_access test suite.

Uses pytest-homeassistant-custom-component for the real ``hass`` fixture —
no namespace-package hacks needed. The fake aiohttp session below scripts
exact response sequences (including Digest 401 challenges), which keeps the
API tests independent of aiohttp internals.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from custom_components.hikvision_access.api import (
    DoorCapabilities,
    HikvisionAccessAPI,
    HikvisionDeviceInfo,
    HikvisionUser,
)

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


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Make Home Assistant load custom_components/ from this repo."""
    return


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
