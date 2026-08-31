"""Config flow for Hikvision Access Control."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import (
    HikvisionAccessAPI,
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionDeviceInfo,
    HikvisionResponseError,
    HikvisionTimeoutError,
)
from .const import (
    DEFAULT_DOOR_PULSE_SECONDS,
    DEFAULT_MASK_CARD_DATA,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    MAX_POLL_INTERVAL,
    MIN_POLL_INTERVAL,
    OPT_DOOR_PULSE_SECONDS,
    OPT_MASK_CARD_DATA,
    OPT_POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_TIMEOUT = "timeout_connect"
ERROR_UNKNOWN = "unknown"


def _build_user_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    """User step schema, prefilled with prior input after an error."""
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(
                CONF_PORT, default=defaults.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin")
            ): str,
            vol.Required(CONF_PASSWORD): str,
            vol.Required(
                CONF_SSL, default=defaults.get(CONF_SSL, DEFAULT_USE_SSL)
            ): bool,
            vol.Required(
                CONF_VERIFY_SSL,
                default=defaults.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ): bool,
        }
    )


class HikvisionAccessConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup of one access-control device."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> HikvisionAccessOptionsFlow:
        """Return the options flow handler."""
        return HikvisionAccessOptionsFlow()

    async def _async_validate(
        self, user_input: dict[str, Any]
    ) -> tuple[HikvisionDeviceInfo | None, dict[str, str]]:
        """Try to reach the device; map API errors to form error keys."""
        api = HikvisionAccessAPI(
            host=user_input[CONF_HOST],
            port=user_input[CONF_PORT],
            username=user_input[CONF_USERNAME],
            password=user_input[CONF_PASSWORD],
            session=async_get_clientsession(self.hass),
            use_ssl=user_input.get(CONF_SSL, DEFAULT_USE_SSL),
            verify_ssl=user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
        )
        try:
            return await api.async_get_device_info(), {}
        except HikvisionAuthError:
            return None, {"base": ERROR_INVALID_AUTH}
        except HikvisionTimeoutError:
            return None, {"base": ERROR_TIMEOUT}
        except (HikvisionConnectionError, HikvisionResponseError):
            return None, {"base": ERROR_CANNOT_CONNECT}
        except Exception:
            _LOGGER.exception("Unexpected error validating device")
            return None, {"base": ERROR_UNKNOWN}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Initial setup step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device, errors = await self._async_validate(user_input)
            if device is not None:
                await self.async_set_unique_id(device.serial_number)
                self._abort_if_unique_id_configured(
                    updates={
                        CONF_HOST: user_input[CONF_HOST],
                        CONF_PORT: user_input[CONF_PORT],
                    }
                )
                return self.async_create_entry(
                    title=device.device_name or device.model,
                    data=user_input,
                )
        return self.async_show_form(
            step_id="user",
            data_schema=_build_user_schema(user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change connection settings of an existing entry.

        The common case is a device that moved to a new IP. The serial
        number must still match, otherwise this would silently repoint the
        entry at a different device.
        """
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        if user_input is not None:
            device, errors = await self._async_validate(user_input)
            if device is not None:
                await self.async_set_unique_id(device.serial_number)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_build_user_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start re-authentication after repeated credential rejections."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for fresh credentials and revalidate."""
        errors: dict[str, str] = {}
        reauth_entry = self._get_reauth_entry()
        if user_input is not None:
            merged = {**reauth_entry.data, **user_input}
            device, errors = await self._async_validate(merged)
            if device is not None:
                await self.async_set_unique_id(device.serial_number)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reauth_entry, data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=reauth_entry.data.get(CONF_USERNAME, "admin"),
                    ): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )


class HikvisionAccessOptionsFlow(OptionsFlow):
    """Options: poll interval, card masking, door pulse length."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show and store the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        options = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        OPT_POLL_INTERVAL,
                        default=options.get(OPT_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                    ): vol.All(
                        vol.Coerce(int),
                        vol.Range(min=MIN_POLL_INTERVAL, max=MAX_POLL_INTERVAL),
                    ),
                    vol.Required(
                        OPT_MASK_CARD_DATA,
                        default=options.get(OPT_MASK_CARD_DATA, DEFAULT_MASK_CARD_DATA),
                    ): bool,
                    vol.Required(
                        OPT_DOOR_PULSE_SECONDS,
                        default=options.get(
                            OPT_DOOR_PULSE_SECONDS, DEFAULT_DOOR_PULSE_SECONDS
                        ),
                    ): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=10.0)),
                }
            ),
        )
