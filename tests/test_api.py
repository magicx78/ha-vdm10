"""Tests for the ISAPI API layer: parsing, Digest flow, errors, semaphore."""

from __future__ import annotations

import asyncio
from datetime import datetime
import json

import aiohttp
import pytest
from pytest_homeassistant_custom_component.common import load_fixture

from custom_components.hikvision_access.api import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionResponseError,
    HikvisionTimeoutError,
    parse_acs_event_time,
    parse_acs_events,
    parse_device_info,
    parse_door_capabilities,
    parse_users,
)

from .conftest import (
    DIGEST_CHALLENGE_MD5,
    CountingSession,
    FakeResponse,
    FakeSession,
    make_api,
)

# --- pure parsers -----------------------------------------------------------


def test_parse_device_info() -> None:
    """deviceInfo XML maps to the dataclass, namespaces ignored."""
    info = parse_device_info(load_fixture("device_info.xml"))
    assert info.serial_number == "VDM10-TEST0000000000000001"
    assert info.model == "VDM10-VM-2W-2.0"
    assert info.firmware_version == "V3.7.1"
    assert info.firmware_released == "build 251112"
    assert info.device_name == "Front Door"
    assert info.device_type == "doorStation"


def test_parse_device_info_requires_serial() -> None:
    """A deviceInfo without serialNumber is unusable as unique_id."""
    with pytest.raises(HikvisionResponseError):
        parse_device_info("<DeviceInfo><model>X</model></DeviceInfo>")


def test_parse_device_info_rejects_garbage() -> None:
    """Non-XML raises a response error, not a crash."""
    with pytest.raises(HikvisionResponseError):
        parse_device_info("not xml at all")


def test_parse_door_capabilities() -> None:
    """Door range and command set come from the capabilities XML."""
    caps = parse_door_capabilities(load_fixture("door_capabilities.xml"))
    assert caps.door_min == 1
    assert caps.door_max == 2
    assert caps.commands == ("open", "close", "alwaysOpen", "resume")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-08-30 12:58:42", datetime(2026, 8, 30, 12, 58, 42)),
        ("2026-08-30T12:58:42", datetime(2026, 8, 30, 12, 58, 42)),
        # The device stamps a wrong UTC offset; it must be stripped, the
        # wall-clock time kept.
        ("2026-08-30T17:46:15+01:00", datetime(2026, 8, 30, 17, 46, 15)),
        ("2026-08-30T17:46:15Z", datetime(2026, 8, 30, 17, 46, 15)),
    ],
)
def test_parse_acs_event_time(raw: str, expected: datetime) -> None:
    """Device timestamps parse as naive local wall-clock time."""
    assert parse_acs_event_time(raw) == expected


def test_parse_acs_events_fixture() -> None:
    """A real-shaped event page parses fully, empty-card events included."""
    events, more = parse_acs_events(json.loads(load_fixture("acs_events_page.json")))
    assert more is False
    assert len(events) == 8
    accepted = [event for event in events if event.minor == 1]
    assert [event.name for event in accepted] == ["Alice", "Bob"]
    assert accepted[0].card_no == "1234567890"
    assert accepted[0].door_no == 1
    # Status events carry no card number but are still parsed (the
    # coordinator decides what to report).
    assert sum(1 for event in events if not event.card_no) == 6


def test_parse_acs_events_bad_shape() -> None:
    """A response without the AcsEvent object raises."""
    with pytest.raises(HikvisionResponseError):
        parse_acs_events({"unexpected": {}})


def test_parse_users_fixture() -> None:
    """The person list parses with card counts."""
    users, more = parse_users(json.loads(load_fixture("user_info_search.json")))
    assert more is False
    assert [user.employee_no for user in users] == ["Alice", "Bob", "Carol"]
    assert users[0].num_cards == 1


# --- request layer ----------------------------------------------------------


async def test_digest_handshake_and_header_reuse() -> None:
    """First call answers the 401 challenge; later calls reuse the nonce."""
    device_xml = load_fixture("device_info.xml")
    session = FakeSession(
        [
            FakeResponse(401, {"WWW-Authenticate": DIGEST_CHALLENGE_MD5}),
            FakeResponse(200, {}, device_xml),
            FakeResponse(200, {}, device_xml),
        ]
    )
    api = make_api(session)

    info = await api.async_get_device_info()
    assert info.device_name == "Front Door"
    # Call 1: no Authorization yet; call 2: answered challenge.
    assert "Authorization" not in session.calls[0][2]["headers"]
    auth_header = session.calls[1][2]["headers"]["Authorization"]
    assert auth_header.startswith("Digest ")
    assert 'username="admin"' in auth_header

    await api.async_get_device_info()
    # Call 3 reuses the cached challenge without a new 401 round trip.
    reused = session.calls[2][2]["headers"]["Authorization"]
    assert "nc=00000002" in reused


async def test_repeated_401_raises_auth_error() -> None:
    """A 401 after answering the fresh challenge means bad credentials."""
    session = FakeSession(
        [
            FakeResponse(401, {"WWW-Authenticate": DIGEST_CHALLENGE_MD5}),
            FakeResponse(401, {"WWW-Authenticate": DIGEST_CHALLENGE_MD5}),
        ]
    )
    with pytest.raises(HikvisionAuthError):
        await make_api(session).async_get_device_info()


async def test_401_without_challenge_is_transient_not_auth() -> None:
    """A 401 lacking a Digest challenge is a busy device, not bad creds."""
    session = FakeSession([FakeResponse(401, {}, "device busy")])
    with pytest.raises(HikvisionResponseError):
        await make_api(session).async_get_device_info()


async def test_403_raises_auth_error() -> None:
    """403 (permission) maps to the auth error."""
    session = FakeSession([FakeResponse(403)])
    with pytest.raises(HikvisionAuthError):
        await make_api(session).async_get_device_info()


async def test_http_error_status() -> None:
    """5xx maps to a response error and never leaks the body."""
    session = FakeSession([FakeResponse(500, {}, "secret-internals")])
    with pytest.raises(HikvisionResponseError) as excinfo:
        await make_api(session).async_get_device_info()
    assert "secret-internals" not in str(excinfo.value)


async def test_timeout_maps_to_timeout_error() -> None:
    """A transport timeout raises HikvisionTimeoutError."""
    session = FakeSession([TimeoutError()])
    with pytest.raises(HikvisionTimeoutError):
        await make_api(session).async_get_device_info()


async def test_client_error_maps_to_connection_error() -> None:
    """Generic aiohttp client errors raise HikvisionConnectionError."""
    session = FakeSession([aiohttp.ClientError("boom")])
    with pytest.raises(HikvisionConnectionError):
        await make_api(session).async_get_device_info()


async def test_unparsable_json_raises_response_error() -> None:
    """Broken JSON from the device maps to a response error."""
    session = FakeSession([FakeResponse(200, {}, "<html>login</html>")])
    with pytest.raises(HikvisionResponseError):
        await make_api(session).async_get_user_count()


async def test_semaphore_caps_concurrency() -> None:
    """Parallel calls are serialized down to MAX_CONCURRENT_REQUESTS."""
    count_body = json.dumps({"UserInfoCount": {"userNumber": 3}})
    session = CountingSession([FakeResponse(200, {}, count_body) for _ in range(4)])
    api = make_api(session)

    results = await asyncio.gather(*(api.async_get_user_count() for _ in range(4)))
    assert results == [3, 3, 3, 3]
    assert session.max_active == 1


async def test_user_pagination() -> None:
    """MORE pages are followed with a moving searchResultPosition."""
    page1 = json.loads(load_fixture("user_info_search.json"))
    page1["UserInfoSearch"]["responseStatusStrg"] = "MORE"
    page2 = json.loads(load_fixture("user_info_search.json"))
    page2["UserInfoSearch"]["UserInfo"] = [
        {"employeeNo": "Dan", "name": "Dan", "userType": "normal", "numOfCard": 2}
    ]
    session = FakeSession(
        [
            FakeResponse(200, {}, json.dumps(page1)),
            FakeResponse(200, {}, json.dumps(page2)),
        ]
    )
    users = await make_api(session).async_get_users()
    assert [user.employee_no for user in users] == ["Alice", "Bob", "Carol", "Dan"]

    second_cond = session.calls[1][2]["json"]["UserInfoSearchCond"]
    assert second_cond["searchResultPosition"] == 3


async def test_acs_event_query_window() -> None:
    """The event search sends the requested window and major 5."""
    empty = json.dumps(
        {"AcsEvent": {"responseStatusStrg": "OK", "numOfMatches": 0, "InfoList": []}}
    )
    session = FakeSession([FakeResponse(200, {}, empty)])
    start = datetime(2026, 8, 30, 18, 0, 0)
    end = datetime(2026, 8, 30, 18, 5, 0)
    events = await make_api(session).async_get_acs_events(start, end)
    assert events == []

    cond = session.calls[0][2]["json"]["AcsEventCond"]
    assert cond["major"] == 5
    assert cond["minor"] == 0
    assert cond["startTime"] == "2026-08-30T18:00:00"
    assert cond["endTime"] == "2026-08-30T18:05:00"


async def test_open_door_sends_put_with_xml() -> None:
    """The door command is a PUT with the RemoteControlDoor XML body."""
    session = FakeSession([FakeResponse(200, {}, "<ResponseStatus/>")])
    await make_api(session).async_open_door(1)

    method, url, kwargs = session.calls[0]
    assert method == "PUT"
    assert url.endswith("/ISAPI/AccessControl/RemoteControl/door/1")
    assert b"<cmd>open</cmd>" in kwargs["data"]
    assert kwargs["headers"]["Content-Type"] == "application/xml"
