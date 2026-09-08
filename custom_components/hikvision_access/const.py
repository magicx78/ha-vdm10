"""Constants for the Hikvision Access Control integration."""

from __future__ import annotations

DOMAIN = "hikvision_access"
MANUFACTURER = "Hikvision"

DEFAULT_PORT = 80
DEFAULT_USE_SSL = False
DEFAULT_VERIFY_SSL = True
DEFAULT_TIMEOUT = 10

# Polling: M0 (2026-08-30, firmware V3.7.1 build 251112) proved the device
# never pushes AccessControllerEvent to /ISAPI/Event/notification/alertStream,
# so polling AcsEvent is the only transport. 2 s keeps reaction time low while
# staying gentle on the single-digit connection budget of the firmware.
DEFAULT_POLL_INTERVAL = 2
MIN_POLL_INTERVAL = 2
MAX_POLL_INTERVAL = 30

# One request at a time: the firmware is stingy with concurrent connections
# (hikvision_next already produces sporadic 401s when polls overlap).
MAX_CONCURRENT_REQUESTS = 1

# Options
OPT_POLL_INTERVAL = "poll_interval"
OPT_MASK_CARD_DATA = "mask_card_data"
OPT_DOOR_PULSE_SECONDS = "door_pulse_seconds"
OPT_TWO_WAY_AUDIO_GUARD = "two_way_audio_guard"
OPT_TWO_WAY_AUDIO_CHECK_INTERVAL = "two_way_audio_check_interval"
DEFAULT_MASK_CARD_DATA = True
DEFAULT_DOOR_PULSE_SECONDS = 2.0
DEFAULT_TWO_WAY_AUDIO_GUARD = True
DEFAULT_TWO_WAY_AUDIO_CHECK_INTERVAL = 60
MIN_TWO_WAY_AUDIO_CHECK_INTERVAL = 10
MAX_TWO_WAY_AUDIO_CHECK_INTERVAL = 3600

# Options the coordinator reads live on every use. Changing only these must
# not reload the entry (the guard switch toggles one of them from an entity).
LIVE_OPTIONS = frozenset({OPT_TWO_WAY_AUDIO_GUARD, OPT_TWO_WAY_AUDIO_CHECK_INTERVAL})

# ISAPI paths
PATH_DEVICE_INFO = "/ISAPI/System/deviceInfo"
PATH_ACS_CAPABILITIES = "/ISAPI/AccessControl/capabilities"
PATH_DOOR_CAPABILITIES = "/ISAPI/AccessControl/RemoteControl/door/capabilities"
PATH_REMOTE_DOOR = "/ISAPI/AccessControl/RemoteControl/door/{door_no}"
PATH_ACS_EVENT = "/ISAPI/AccessControl/AcsEvent?format=json"
PATH_USER_SEARCH = "/ISAPI/AccessControl/UserInfo/Search?format=json"
PATH_USER_COUNT = "/ISAPI/AccessControl/UserInfo/Count?format=json"
PATH_REBOOT = "/ISAPI/System/reboot"
PATH_TWO_WAY_AUDIO_CHANNEL = "/ISAPI/System/TwoWayAudio/channels/{channel}"

# Two-way audio guard. The reference firmware silently switches its ISAPI
# two-way audio channel off — seen after a firmware update, after hard-torn
# ISAPI sessions and after a go2rtc move (three times within a week). The
# isapi:// backchannel of go2rtc then dies without any error, so intercom
# and announcements at the door station go mute. The guard re-enables the
# channel as soon as a throttled check finds it off.
DEFAULT_TWO_WAY_AUDIO_CHANNEL = 1
TWO_WAY_AUDIO_REPAIR_ATTEMPTS = 3
EVENT_TWO_WAY_AUDIO_RESTORED = f"{DOMAIN}_two_way_audio_restored"
ISSUE_TWO_WAY_AUDIO_DISABLED = "two_way_audio_disabled"

# AcsEvent codes (verified against VDM10-VM-2W-2.0, V3.7.1 build 251112).
# Every card swipe produces a burst: minor 1 (accepted, carries the name),
# minor 21 (door open signal), minor 214 (empty companion event) and
# minor 22 (door closed). Only events carrying a card number matter to us.
EVENT_MAJOR_ACS = 5
EVENT_MINOR_ACCEPTED = 1

# Event entity types. Classification (see AccessEvent.event_type): minor 1
# with a name = accepted; a card number without a name = unknown card; any
# other minor on a card-carrying event = rejected. The concrete reject
# minors of the reference firmware get verified during the M3 acceptance
# test (a foreign card swipe).
EVENT_TYPE_ACCEPTED = "card_accepted"
EVENT_TYPE_REJECTED = "card_rejected"
EVENT_TYPE_UNKNOWN = "card_unknown"
EVENT_TYPES = [EVENT_TYPE_ACCEPTED, EVENT_TYPE_REJECTED, EVENT_TYPE_UNKNOWN]

# Search pagination
ACS_EVENT_PAGE_SIZE = 30
USER_SEARCH_PAGE_SIZE = 30
MAX_SEARCH_PAGES = 10

# Poll window handling: overlap consecutive windows so a slow poll cannot
# drop events; the device clock may also drift, hence the future margin.
POLL_OVERLAP_SECONDS = 90
CLOCK_DRIFT_MARGIN_SECONDS = 60
STARTUP_LOOKBACK_SECONDS = 120

# Refresh the person list this often (new persons create sensors in M2).
USER_REFRESH_INTERVAL_SECONDS = 3600

# Cap for the deduplication memory (a burst is 4 events, so this covers
# hundreds of swipes between refreshes without growing unbounded).
DEDUP_CACHE_SIZE = 1000

# Re-auth gating: the firmware throws sporadic 401s under concurrent load
# even with valid credentials (observed live: ~1 refusal per minute while
# hikvision_next and the legacy poller run alongside). Re-auth is only
# triggered after this many consecutive failures AND this much elapsed
# time since the first failure of the streak — a short burst never
# invalidates the entry, a genuinely wrong password does within a minute.
AUTH_FAILURE_THRESHOLD = 5
AUTH_FAILURE_MIN_SECONDS = 60

# Single failed polls keep the previous data (with no new events) so the
# entities do not flicker unavailable on every firmware hiccup; only this
# many consecutive failures mark the update as failed.
TRANSIENT_FAILURE_TOLERANCE = 5
