"""All ISAPI network calls for Hikvision Access Control live here.

Iron rule: no other module talks HTTP. Entities read from the coordinator,
the coordinator and the config flow call this API class.

Coexistence design (the reference VDM10 firmware is stingy with concurrent
connections and throws sporadic 401s under parallel load from other
integrations): a semaphore caps our concurrent requests, timeouts are tight,
and a single 401 is answered with exactly one fresh-challenge retry before
it becomes an error. Response bodies are never embedded in error messages.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
from typing import Any
from xml.etree import ElementTree as ET

import aiohttp

from .auth import DigestAuthError, HikvisionDigestAuth
from .const import (
    ACS_EVENT_PAGE_SIZE,
    CLOCK_DRIFT_MARGIN_SECONDS,
    DEFAULT_TIMEOUT,
    EVENT_MAJOR_ACS,
    EVENT_MINOR_ACCEPTED,
    EVENT_TYPE_ACCEPTED,
    EVENT_TYPE_REJECTED,
    EVENT_TYPE_UNKNOWN,
    MAX_CONCURRENT_REQUESTS,
    MAX_SEARCH_PAGES,
    PATH_ACS_EVENT,
    PATH_DEVICE_INFO,
    PATH_DOOR_CAPABILITIES,
    PATH_REMOTE_DOOR,
    PATH_USER_COUNT,
    PATH_USER_SEARCH,
)

_LOGGER = logging.getLogger(__name__)


class HikvisionAccessError(Exception):
    """Base error for this API."""


class HikvisionAuthError(HikvisionAccessError):
    """Credentials rejected (401/403 after a fresh-challenge retry)."""


class HikvisionConnectionError(HikvisionAccessError):
    """Device unreachable or transport-level failure."""


class HikvisionTimeoutError(HikvisionAccessError):
    """Device did not answer within the timeout."""


class HikvisionResponseError(HikvisionAccessError):
    """Device answered with an unexpected status or unparsable body."""


@dataclass(frozen=True)
class HikvisionDeviceInfo:
    """Static device identity from /ISAPI/System/deviceInfo."""

    serial_number: str
    model: str
    firmware_version: str
    firmware_released: str
    device_name: str
    mac_address: str
    device_type: str


@dataclass(frozen=True)
class HikvisionUser:
    """A person configured on the device."""

    employee_no: str
    name: str
    user_type: str
    num_cards: int


@dataclass(frozen=True)
class AccessEvent:
    """One entry of the device's access event log (major 5)."""

    time: datetime
    minor: int
    door_no: int
    card_no: str
    employee_no: str
    name: str
    serial_no: str | None = None

    @property
    def dedup_key(self) -> tuple[str, str, int, int, str | None]:
        """Stable identity of this log entry across overlapping polls.

        The reference device has 1 s log resolution and no serialNo field;
        devices that do provide serialNo get it included automatically.
        """
        return (
            self.time.isoformat(),
            self.card_no,
            self.minor,
            self.door_no,
            self.serial_no,
        )

    @property
    def event_type(self) -> str | None:
        """Classify a card-carrying event; None for pure status events."""
        if not self.card_no:
            return None
        if self.minor == EVENT_MINOR_ACCEPTED and self.name:
            return EVENT_TYPE_ACCEPTED
        if not self.name:
            return EVENT_TYPE_UNKNOWN
        return EVENT_TYPE_REJECTED


@dataclass(frozen=True)
class DoorCapabilities:
    """Result of /ISAPI/AccessControl/RemoteControl/door/capabilities."""

    door_min: int
    door_max: int
    commands: tuple[str, ...]


def _xml_child_text(root: ET.Element, local_name: str) -> str:
    """Return a direct child's text, ignoring XML namespaces."""
    for child in root:
        if child.tag.split("}")[-1] == local_name:
            return (child.text or "").strip()
    return ""


def parse_device_info(xml_text: str) -> HikvisionDeviceInfo:
    """Parse the deviceInfo XML document (module-level for pure unit tests)."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise HikvisionResponseError(f"deviceInfo is not valid XML: {err}") from err
    serial = _xml_child_text(root, "serialNumber")
    if not serial:
        raise HikvisionResponseError("deviceInfo lacks a serialNumber")
    return HikvisionDeviceInfo(
        serial_number=serial,
        model=_xml_child_text(root, "model"),
        firmware_version=_xml_child_text(root, "firmwareVersion"),
        firmware_released=_xml_child_text(root, "firmwareReleasedDate"),
        device_name=_xml_child_text(root, "deviceName"),
        mac_address=_xml_child_text(root, "macAddress"),
        device_type=_xml_child_text(root, "subDeviceType")
        or _xml_child_text(root, "deviceType"),
    )


def parse_door_capabilities(xml_text: str) -> DoorCapabilities:
    """Parse the RemoteControlDoor capabilities XML."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as err:
        raise HikvisionResponseError(
            f"door capabilities is not valid XML: {err}"
        ) from err
    door_min, door_max = 1, 1
    commands: tuple[str, ...] = ("open",)
    for child in root:
        local = child.tag.split("}")[-1]
        if local == "doorNo":
            door_min = int(child.get("min", "1"))
            door_max = int(child.get("max", "1"))
        elif local == "cmd":
            opt = child.get("opt", "open")
            commands = tuple(part.strip() for part in opt.split(",") if part.strip())
    return DoorCapabilities(door_min=door_min, door_max=door_max, commands=commands)


def parse_acs_event_time(raw: str) -> datetime:
    """Parse an AcsEvent timestamp.

    The reference device writes naive local time ("2026-08-30 12:58:42") in
    JSON search results, while its alert stream stamps a wrong UTC offset —
    so offsets are stripped deliberately and the value is treated as device
    local time.
    """
    raw = raw.strip().replace("T", " ")
    for sep in ("+", "Z"):
        if sep in raw[10:]:
            raw = raw[: raw.index(sep, 10)]
            break
    return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")


def parse_acs_events(payload: dict[str, Any]) -> tuple[list[AccessEvent], bool]:
    """Parse one AcsEvent search page. Returns (events, more_pages)."""
    body = payload.get("AcsEvent")
    if not isinstance(body, dict):
        raise HikvisionResponseError("AcsEvent response lacks the AcsEvent object")
    events: list[AccessEvent] = []
    for info in body.get("InfoList") or []:
        raw_time = info.get("time")
        if not raw_time:
            continue
        try:
            when = parse_acs_event_time(str(raw_time))
        except ValueError:
            _LOGGER.debug("Skipping event with unparsable time %r", raw_time)
            continue
        events.append(
            AccessEvent(
                time=when,
                minor=int(info.get("minor", 0)),
                door_no=int(info.get("doorNo", 1)),
                card_no=str(info.get("cardNo", "") or ""),
                employee_no=str(info.get("employeeNoString", "") or ""),
                name=str(info.get("name", "") or ""),
                serial_no=(
                    str(info["serialNo"]) if info.get("serialNo") is not None else None
                ),
            )
        )
    return events, body.get("responseStatusStrg") == "MORE"


def parse_users(payload: dict[str, Any]) -> tuple[list[HikvisionUser], bool]:
    """Parse one UserInfo/Search page. Returns (users, more_pages)."""
    body = payload.get("UserInfoSearch")
    if not isinstance(body, dict):
        raise HikvisionResponseError(
            "UserInfo response lacks the UserInfoSearch object"
        )
    users = [
        HikvisionUser(
            employee_no=str(info.get("employeeNo", "") or ""),
            name=str(info.get("name", "") or ""),
            user_type=str(info.get("userType", "") or ""),
            num_cards=int(info.get("numOfCard", 0)),
        )
        for info in body.get("UserInfo") or []
        if info.get("employeeNo")
    ]
    return users, body.get("responseStatusStrg") == "MORE"


class HikvisionAccessAPI:
    """Async client for the access-control part of Hikvision ISAPI."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        session: aiohttp.ClientSession,
        *,
        use_ssl: bool = False,
        verify_ssl: bool = True,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """Store connection settings; the shared session is injected."""
        scheme = "https" if use_ssl else "http"
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        self._base_url = f"{scheme}://{host}:{port}"
        self._session = session
        self._ssl: bool | None = None if not use_ssl else verify_ssl
        self._timeout = aiohttp.ClientTimeout(
            total=timeout, connect=min(5, timeout), sock_read=timeout
        )
        self._auth = HikvisionDigestAuth(username=username, password=password)
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_payload: dict[str, Any] | None = None,
        xml_payload: str | None = None,
    ) -> str:
        """Perform one ISAPI request with Digest auth and return the body text.

        A 401 is answered with exactly one retry against the fresh challenge
        (also covering nonce expiry via ``stale``); a second 401 raises
        HikvisionAuthError.
        """
        uri = path.split("?")[0]
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._ssl is not None:
            kwargs["ssl"] = self._ssl
        if json_payload is not None:
            kwargs["json"] = json_payload
        elif xml_payload is not None:
            kwargs["data"] = xml_payload.encode("utf-8")
            kwargs["headers"] = {"Content-Type": "application/xml"}

        async with self._semaphore:
            try:
                for attempt in (1, 2):
                    headers = dict(kwargs.get("headers") or {})
                    if self._auth.has_challenge:
                        headers["Authorization"] = self._auth.authorization_header(
                            method, uri
                        )
                    request_kwargs = {**kwargs, "headers": headers}
                    async with self._session.request(
                        method, self._base_url + path, **request_kwargs
                    ) as resp:
                        if resp.status == 401:
                            if attempt == 2:
                                self._auth.clear()
                                raise HikvisionAuthError(
                                    f"Credentials rejected for {method} {uri}"
                                )
                            try:
                                self._auth.handle_401(
                                    resp.headers.get("WWW-Authenticate")
                                )
                            except DigestAuthError as err:
                                # A 401 without a parsable Digest challenge is
                                # the firmware refusing service (busy/lockout
                                # protection), not a credential verdict.
                                self._auth.clear()
                                raise HikvisionResponseError(
                                    f"401 without Digest challenge for "
                                    f"{method} {uri} (device busy?)"
                                ) from err
                            continue
                        if resp.status == 403:
                            raise HikvisionAuthError(
                                f"Access forbidden for {method} {uri}"
                            )
                        if resp.status >= 400:
                            raise HikvisionResponseError(
                                f"HTTP {resp.status} for {method} {uri}"
                            )
                        body = await resp.text()
                        _LOGGER.debug(
                            "%s %s -> %s (%d bytes)",
                            method,
                            uri,
                            resp.status,
                            len(body),
                        )
                        return body
            except DigestAuthError as err:
                raise HikvisionAuthError(str(err)) from err
            except TimeoutError as err:
                raise HikvisionTimeoutError(f"Timeout for {method} {uri}") from err
            except aiohttp.ClientError as err:
                raise HikvisionConnectionError(
                    f"Cannot reach device for {method} {uri}: {err}"
                ) from err
        raise HikvisionResponseError(f"Unreachable state for {method} {uri}")

    async def async_get_device_info(self) -> HikvisionDeviceInfo:
        """Fetch device identity; also serves as the connection test."""
        return parse_device_info(await self._request("GET", PATH_DEVICE_INFO))

    async def async_get_door_capabilities(self) -> DoorCapabilities:
        """Fetch how many doors exist and which remote commands they accept."""
        return parse_door_capabilities(
            await self._request("GET", PATH_DOOR_CAPABILITIES)
        )

    async def async_get_users(self) -> list[HikvisionUser]:
        """Fetch all configured persons (paginated)."""
        users: list[HikvisionUser] = []
        for _ in range(MAX_SEARCH_PAGES):
            payload = {
                "UserInfoSearchCond": {
                    "searchID": "hikvision_access",
                    "searchResultPosition": len(users),
                    "maxResults": ACS_EVENT_PAGE_SIZE,
                }
            }
            body = await self._request("POST", PATH_USER_SEARCH, json_payload=payload)
            page_users, more = parse_users(self._decode_json(body))
            users.extend(page_users)
            if not more or not page_users:
                break
        else:
            _LOGGER.warning(
                "User search stopped after %d pages; person list may be incomplete",
                MAX_SEARCH_PAGES,
            )
        return users

    async def async_get_user_count(self) -> int:
        """Fetch how many persons the device knows."""
        body = self._decode_json(await self._request("GET", PATH_USER_COUNT))
        try:
            return int(body["UserInfoCount"]["userNumber"])
        except (KeyError, TypeError, ValueError) as err:
            raise HikvisionResponseError("Unexpected UserInfoCount response") from err

    async def async_get_acs_events(
        self, start: datetime, end: datetime | None = None
    ) -> list[AccessEvent]:
        """Fetch access events (major 5, all minors) in [start, end], paginated.

        ``end`` defaults to now plus a clock-drift margin. Timestamps are
        device-local naive datetimes throughout.
        """
        if end is None:
            end = datetime.now() + timedelta(seconds=CLOCK_DRIFT_MARGIN_SECONDS)
        events: list[AccessEvent] = []
        position = 0
        for _ in range(MAX_SEARCH_PAGES):
            payload = {
                "AcsEventCond": {
                    "searchID": "hikvision_access",
                    "searchResultPosition": position,
                    "maxResults": ACS_EVENT_PAGE_SIZE,
                    "major": EVENT_MAJOR_ACS,
                    "minor": 0,
                    "startTime": start.strftime("%Y-%m-%dT%H:%M:%S"),
                    "endTime": end.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            }
            body = await self._request("POST", PATH_ACS_EVENT, json_payload=payload)
            page_events, more = parse_acs_events(self._decode_json(body))
            events.extend(page_events)
            position += len(page_events)
            if not more or not page_events:
                break
        else:
            _LOGGER.warning(
                "Event search stopped after %d pages; poll window too large?",
                MAX_SEARCH_PAGES,
            )
        return events

    async def async_open_door(self, door_no: int, command: str = "open") -> None:
        """Send a remote door command (open/close/alwaysOpen/resume)."""
        xml = (
            '<RemoteControlDoor version="2.0" '
            'xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f"<cmd>{command}</cmd></RemoteControlDoor>"
        )
        await self._request(
            "PUT", PATH_REMOTE_DOOR.format(door_no=door_no), xml_payload=xml
        )

    @staticmethod
    def _decode_json(body: str) -> dict[str, Any]:
        """Decode a JSON body, mapping failures to HikvisionResponseError."""
        import json

        try:
            decoded = json.loads(body)
        except ValueError as err:
            raise HikvisionResponseError("Device sent unparsable JSON") from err
        if not isinstance(decoded, dict):
            raise HikvisionResponseError("Device sent unexpected JSON shape")
        return decoded
