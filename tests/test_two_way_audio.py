"""Tests for the two-way audio guard: API, coordinator, switches, repairs."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
    load_fixture,
)

from custom_components.hikvision_access.api import (
    HikvisionConnectionError,
    HikvisionResponseError,
    TwoWayAudioState,
    parse_response_status,
    parse_two_way_audio,
)
from custom_components.hikvision_access.const import (
    DOMAIN,
    EVENT_TWO_WAY_AUDIO_RESTORED,
    ISSUE_TWO_WAY_AUDIO_DISABLED,
    OPT_TWO_WAY_AUDIO_CHECK_INTERVAL,
    OPT_TWO_WAY_AUDIO_GUARD,
)

from .conftest import (
    AUDIO_OFF,
    AUDIO_ON,
    FakeResponse,
    FakeSession,
    advance_time,
    make_api,
    make_burst,
    make_mock_api,
    resolve_entity_id,
    setup_integration,
)

# --- API -------------------------------------------------------------------


def test_parse_two_way_audio_single_channel() -> None:
    """The single-channel document parses enabled flag and codec."""
    state = parse_two_way_audio(load_fixture("two_way_audio_channel.xml"))
    assert state == TwoWayAudioState(channel=1, enabled=False, compression="G.711ulaw")


def test_parse_two_way_audio_channel_list() -> None:
    """The channel list document yields the requested channel."""
    state = parse_two_way_audio(load_fixture("two_way_audio_channels.xml"), 1)
    assert state.enabled is True
    assert state.compression == "G.711ulaw"


def test_parse_two_way_audio_rejects_garbage() -> None:
    """Non-XML and XML without an enabled flag raise response errors."""
    with pytest.raises(HikvisionResponseError):
        parse_two_way_audio("not xml")
    with pytest.raises(HikvisionResponseError):
        parse_two_way_audio("<TwoWayAudioChannel><id>1</id></TwoWayAudioChannel>")
    with pytest.raises(HikvisionResponseError):
        parse_two_way_audio("<TwoWayAudioChannelList/>")


def test_parse_response_status() -> None:
    """ResponseStatus parses; empty or broken bodies are lenient."""
    assert parse_response_status(load_fixture("response_status_ok.xml")) == (1, "OK")
    assert parse_response_status("") == (None, "")
    assert parse_response_status("<broken") == (None, "")


async def test_get_two_way_audio_requests_channel() -> None:
    """GET hits the channel path and parses the state."""
    session = FakeSession(
        [FakeResponse(200, {}, load_fixture("two_way_audio_channel.xml"))]
    )
    state = await make_api(session).async_get_two_way_audio()
    assert state.enabled is False

    method, url, _kwargs = session.calls[0]
    assert method == "GET"
    assert url.endswith("/ISAPI/System/TwoWayAudio/channels/1")


async def test_set_two_way_audio_puts_xml_with_device_codec() -> None:
    """PUT carries the device's own codec and the requested flag."""
    session = FakeSession(
        [FakeResponse(200, {}, load_fixture("response_status_ok.xml"))]
    )
    await make_api(session).async_set_two_way_audio(1, True, compression="G.726")

    method, url, kwargs = session.calls[0]
    assert method == "PUT"
    assert url.endswith("/ISAPI/System/TwoWayAudio/channels/1")
    body = kwargs["data"].decode()
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<enabled>true</enabled>" in body
    assert "<audioCompressionType>G.726</audioCompressionType>" in body
    assert kwargs["headers"]["Content-Type"] == "application/xml"


async def test_set_two_way_audio_fetches_codec_when_missing() -> None:
    """Without a codec argument the current one is read first, never assumed."""
    session = FakeSession(
        [
            FakeResponse(200, {}, load_fixture("two_way_audio_channel.xml")),
            FakeResponse(200, {}, load_fixture("response_status_ok.xml")),
        ]
    )
    await make_api(session).async_set_two_way_audio(1, False)

    assert session.calls[0][0] == "GET"
    assert session.calls[1][0] == "PUT"
    body = session.calls[1][2]["data"].decode()
    assert "<enabled>false</enabled>" in body
    assert "<audioCompressionType>G.711ulaw</audioCompressionType>" in body


async def test_set_two_way_audio_rejects_error_status() -> None:
    """A ResponseStatus other than 1 is an error even with HTTP 200."""
    session = FakeSession(
        [
            FakeResponse(
                200,
                {},
                "<ResponseStatus><statusCode>4</statusCode>"
                "<statusString>Invalid Operation</statusString></ResponseStatus>",
            )
        ]
    )
    with pytest.raises(HikvisionResponseError):
        await make_api(session).async_set_two_way_audio(1, True, compression="x")


# --- coordinator -------------------------------------------------------------


async def test_check_runs_once_at_setup_then_throttled(hass: HomeAssistant) -> None:
    """The channel is checked on the first poll and not again before the interval."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    assert api.async_get_two_way_audio.await_count == 1

    for _ in range(5):
        await advance_time(hass, 3)
    assert api.async_get_two_way_audio.await_count == 1
    assert api.async_get_acs_events.await_count >= 6

    switch = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert switch.state == STATE_ON
    assert switch.attributes["audio_compression"] == "G.711ulaw"
    assert switch.attributes["repairs_count"] == 0
    assert switch.attributes["guard_enabled"] is True
    assert switch.attributes["last_checked"] is not None


async def test_check_repeats_after_interval(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Once the configured interval elapsed, the next poll checks again."""
    api = make_mock_api([])
    await setup_integration(hass, api, options={OPT_TWO_WAY_AUDIO_CHECK_INTERVAL: 10})
    assert api.async_get_two_way_audio.await_count == 1

    freezer.tick(timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert api.async_get_two_way_audio.await_count == 1

    freezer.tick(timedelta(seconds=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert api.async_get_two_way_audio.await_count == 2


async def test_guard_repairs_and_fires_event(hass: HomeAssistant) -> None:
    """A disabled channel is switched back on, counted and announced."""
    api = make_mock_api([])
    api.async_get_two_way_audio.side_effect = [AUDIO_OFF, AUDIO_ON]
    captured = async_capture_events(hass, EVENT_TWO_WAY_AUDIO_RESTORED)
    before = dt_util.utcnow()

    entry = await setup_integration(hass, api)

    api.async_set_two_way_audio.assert_awaited_once_with(
        1, True, compression="G.711ulaw"
    )
    assert api.async_get_two_way_audio.await_count == 2
    assert len(captured) == 1
    data = captured[0].data
    assert data["entry_id"] == entry.entry_id
    assert data["device"] == "Front Door"
    assert data["repairs"] == 1
    assert dt_util.parse_datetime(data["timestamp"]) >= before

    switch = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert switch.state == STATE_ON
    assert switch.attributes["repairs_count"] == 1
    assert (
        ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_TWO_WAY_AUDIO_DISABLED}_{entry.entry_id}"
        )
        is None
    )


async def test_guard_off_only_reports_state(hass: HomeAssistant) -> None:
    """With the guard off a disabled channel is shown, never touched."""
    api = make_mock_api([])
    api.async_get_two_way_audio.return_value = AUDIO_OFF
    captured = async_capture_events(hass, EVENT_TWO_WAY_AUDIO_RESTORED)

    entry = await setup_integration(hass, api, options={OPT_TWO_WAY_AUDIO_GUARD: False})

    api.async_set_two_way_audio.assert_not_awaited()
    assert captured == []
    switch = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert switch.state == STATE_OFF
    assert switch.attributes["guard_enabled"] is False
    guard = hass.states.get(
        resolve_entity_id(hass, "switch", entry, "two_way_audio_guard")
    )
    assert guard.state == STATE_OFF


async def test_check_failure_does_not_touch_event_poll(hass: HomeAssistant) -> None:
    """A failing channel check leaves the poll, sensors and events intact."""
    api = make_mock_api([])
    api.async_get_two_way_audio.side_effect = HikvisionConnectionError("down")
    entry = await setup_integration(hass, api)
    coordinator = entry.runtime_data.coordinator
    assert coordinator.last_update_success

    switch = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert switch.state == STATE_UNKNOWN

    # The event poll keeps delivering swipes although the check keeps failing.
    coordinator._two_way_audio_last_check = None
    swipe_time = dt_util.now().replace(microsecond=0, tzinfo=None)
    api.async_get_acs_events.return_value = make_burst(
        swipe_time, "Alice", "1234567890"
    )
    await advance_time(hass, 3)
    assert coordinator.last_update_success
    assert api.async_get_two_way_audio.await_count == 2
    alice = hass.states.get(resolve_entity_id(hass, "sensor", entry, "person_Alice"))
    assert alice.state != STATE_UNKNOWN
    assert coordinator._poll_failures == 0


async def test_repairs_issue_after_three_failed_repairs(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    """Three failed repairs raise the issue; a healthy channel clears it."""
    api = make_mock_api([])
    api.async_get_two_way_audio.return_value = AUDIO_OFF
    entry = await setup_integration(
        hass, api, options={OPT_TWO_WAY_AUDIO_CHECK_INTERVAL: 10}
    )
    issue_id = f"{ISSUE_TWO_WAY_AUDIO_DISABLED}_{entry.entry_id}"
    registry = ir.async_get(hass)

    # Attempt 1 happened at setup; two more checks complete the streak.
    assert api.async_set_two_way_audio.await_count == 1
    assert registry.async_get_issue(DOMAIN, issue_id) is None

    for expected_attempts in (2, 3):
        freezer.tick(timedelta(seconds=11))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert api.async_set_two_way_audio.await_count == expected_attempts

    issue = registry.async_get_issue(DOMAIN, issue_id)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == ISSUE_TWO_WAY_AUDIO_DISABLED
    assert issue.translation_placeholders == {"device": "Front Door"}

    # Fourth attempt succeeds -> counted, event fired, issue gone.
    captured = async_capture_events(hass, EVENT_TWO_WAY_AUDIO_RESTORED)
    api.async_get_two_way_audio.side_effect = [AUDIO_OFF, AUDIO_ON]
    freezer.tick(timedelta(seconds=11))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    assert registry.async_get_issue(DOMAIN, issue_id) is None
    assert len(captured) == 1
    switch = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert switch.state == STATE_ON
    assert switch.attributes["repairs_count"] == 1


async def test_repair_request_failure_counts_as_attempt(hass: HomeAssistant) -> None:
    """A PUT that errors is a failed attempt, not a coordinator failure."""
    api = make_mock_api([])
    api.async_get_two_way_audio.return_value = AUDIO_OFF
    api.async_set_two_way_audio.side_effect = HikvisionResponseError("refused")
    entry = await setup_integration(hass, api)
    coordinator = entry.runtime_data.coordinator

    assert coordinator.last_update_success
    assert coordinator.two_way_audio_repair_failures == 1
    assert coordinator.two_way_audio_issue_open is False


# --- switches ------------------------------------------------------------------


async def test_two_way_audio_switch_turn_on_off(hass: HomeAssistant) -> None:
    """turn_on/turn_off PUT the flag and re-read only this value."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "switch", entry, "two_way_audio")
    registry_entry = er.async_get(hass).async_get(entity_id)
    assert registry_entry.entity_category == "config"
    assert hass.states.get(entity_id).attributes["icon"] == "mdi:account-voice"
    polls_before = api.async_get_acs_events.await_count

    api.async_get_two_way_audio.return_value = AUDIO_OFF
    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    api.async_set_two_way_audio.assert_awaited_once_with(
        1, False, compression="G.711ulaw"
    )
    assert hass.states.get(entity_id).state == STATE_OFF
    assert api.async_get_two_way_audio.await_count == 2
    # No full event poll was triggered by the entity action.
    assert api.async_get_acs_events.await_count == polls_before

    api.async_get_two_way_audio.return_value = AUDIO_ON
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    api.async_set_two_way_audio.assert_awaited_with(1, True, compression="G.711ulaw")
    assert hass.states.get(entity_id).state == STATE_ON


async def test_switch_action_does_not_replay_events(hass: HomeAssistant) -> None:
    """Publishing the audio state out-of-band never re-fires the last swipe."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    event_entity = resolve_entity_id(hass, "event", entry, "door_1_event")

    swipe_time = dt_util.now().replace(microsecond=0, tzinfo=None)
    api.async_get_acs_events.return_value = make_burst(
        swipe_time, "Alice", "1234567890"
    )
    await advance_time(hass, 3)
    fired_at = hass.states.get(event_entity).state
    assert fired_at != STATE_UNKNOWN

    switch = resolve_entity_id(hass, "switch", entry, "two_way_audio")
    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: switch}, blocking=True
    )
    assert hass.states.get(event_entity).state == fired_at
    assert (
        hass.states.get(event_entity).last_updated
        == hass.states.get(event_entity).last_updated
    )


async def test_guard_switch_updates_option_without_reload(
    hass: HomeAssistant,
) -> None:
    """The guard switch mirrors the option; toggling it does not reload."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    entity_id = resolve_entity_id(hass, "switch", entry, "two_way_audio_guard")
    assert hass.states.get(entity_id).state == STATE_ON
    assert er.async_get(hass).async_get(entity_id).entity_category == "config"
    coordinator = entry.runtime_data.coordinator

    await hass.services.async_call(
        "switch", "turn_off", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()

    assert entry.options[OPT_TWO_WAY_AUDIO_GUARD] is False
    assert hass.states.get(entity_id).state == STATE_OFF
    assert coordinator.two_way_audio_guard_enabled is False
    # Same coordinator instance, no second device-info fetch: no reload.
    assert entry.runtime_data.coordinator is coordinator
    assert api.async_get_device_info.await_count == 1
    audio = hass.states.get(resolve_entity_id(hass, "switch", entry, "two_way_audio"))
    assert audio.attributes["guard_enabled"] is False

    await hass.services.async_call(
        "switch", "turn_on", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert entry.options[OPT_TWO_WAY_AUDIO_GUARD] is True
    assert hass.states.get(entity_id).state == STATE_ON
    assert api.async_get_device_info.await_count == 1


async def test_other_option_change_still_reloads(hass: HomeAssistant) -> None:
    """Changing a non-live option (poll interval) reloads the entry."""
    api = make_mock_api([])
    entry = await setup_integration(hass, api)
    coordinator = entry.runtime_data.coordinator

    with patch(
        "custom_components.hikvision_access.HikvisionAccessAPI", return_value=api
    ):
        hass.config_entries.async_update_entry(
            entry, options={**entry.options, "poll_interval": 5}
        )
        await hass.async_block_till_done()

    assert entry.runtime_data.coordinator is not coordinator
    assert api.async_get_device_info.await_count == 2
