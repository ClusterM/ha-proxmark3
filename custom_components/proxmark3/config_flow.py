"""Config flow for Proxmark3."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .const import (
    CONF_BLOCK_0,
    CONF_BLOCK_1,
    CONF_BLOCK_2,
    CONF_BLOCK_3,
    CONF_CUSTOM_KEY,
    CONF_KEY_FF,
    CONF_KEY_MAD,
    CONF_KEY_NDEF,
    CONF_KEY_NULL,
    CONF_POLL_INTERVAL,
    CONF_READ_NTAG,
    CONF_READ_NTAG_CLASSIC,
    CONF_READ_RAW_BLOCKS,
    CONF_RECONNECT_INTERVAL,
    DEFAULT_BAUD,
    DEFAULT_OPTIONS,
    DOMAIN,
    build_block_list,
    build_key_list,
    finalize_options,
    merged_options,
)
from .pm3_client import test_connection

HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{12}$")

READING_KEYS: tuple[str, ...] = (
    CONF_READ_NTAG,
    CONF_READ_NTAG_CLASSIC,
    CONF_READ_RAW_BLOCKS,
    CONF_POLL_INTERVAL,
    CONF_RECONNECT_INTERVAL,
)

BLOCK_KEYS: tuple[str, ...] = (
    CONF_BLOCK_0,
    CONF_BLOCK_1,
    CONF_BLOCK_2,
    CONF_BLOCK_3,
)

KEY_OPTION_KEYS: tuple[str, ...] = (
    CONF_KEY_FF,
    CONF_KEY_MAD,
    CONF_KEY_NDEF,
    CONF_KEY_NULL,
    CONF_CUSTOM_KEY,
)


def _reading_fields(opts: dict[str, Any]) -> dict[vol.Marker, Any]:
    return {
        vol.Required(CONF_READ_NTAG, default=opts[CONF_READ_NTAG]): selector.BooleanSelector(),
        vol.Required(
            CONF_READ_NTAG_CLASSIC,
            default=opts[CONF_READ_NTAG_CLASSIC],
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_READ_RAW_BLOCKS,
            default=opts[CONF_READ_RAW_BLOCKS],
        ): selector.BooleanSelector(),
        vol.Required(
            CONF_POLL_INTERVAL,
            default=opts[CONF_POLL_INTERVAL],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.01,
                max=5.0,
                step=0.01,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
        vol.Required(
            CONF_RECONNECT_INTERVAL,
            default=opts[CONF_RECONNECT_INTERVAL],
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=1.0,
                max=60.0,
                step=1.0,
                mode=selector.NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        ),
    }


def _block_fields(opts: dict[str, Any]) -> dict[vol.Marker, Any]:
    return {
        vol.Required(CONF_BLOCK_0, default=opts[CONF_BLOCK_0]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_1, default=opts[CONF_BLOCK_1]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_2, default=opts[CONF_BLOCK_2]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_3, default=opts[CONF_BLOCK_3]): selector.BooleanSelector(),
    }


def _key_fields(opts: dict[str, Any]) -> dict[vol.Marker, Any]:
    return {
        vol.Required(CONF_KEY_FF, default=opts[CONF_KEY_FF]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_MAD, default=opts[CONF_KEY_MAD]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_NDEF, default=opts[CONF_KEY_NDEF]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_NULL, default=opts[CONF_KEY_NULL]): selector.BooleanSelector(),
        vol.Optional(CONF_CUSTOM_KEY, default=opts[CONF_CUSTOM_KEY]): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
    }


def _merge_step(options: dict[str, Any], user_input: dict[str, Any], keys: tuple[str, ...]) -> None:
    for key in keys:
        options[key] = user_input[key]


def _validate_keys(options: dict[str, Any]) -> str | None:
    custom = options.get(CONF_CUSTOM_KEY) or ""
    if custom and not HEX_KEY_RE.match(custom):
        return "invalid_key"
    if build_block_list(options) and not build_key_list(options):
        return "no_keys"
    return None


class Proxmark3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proxmark3."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize config flow."""
        self._options: dict[str, Any] = dict(DEFAULT_OPTIONS)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> Proxmark3OptionsFlowHandler:
        """Return the options flow handler."""
        return Proxmark3OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reading options (screen 1)."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            _merge_step(self._options, user_input, READING_KEYS)
            if self._options.get(CONF_READ_RAW_BLOCKS):
                return await self.async_step_blocks()
            return await self._async_create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_reading_fields(self._options)),
            errors=errors,
        )

    async def async_step_blocks(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Block selection (screen 2)."""
        if user_input is not None:
            _merge_step(self._options, user_input, BLOCK_KEYS)
            return await self.async_step_keys()

        return self.async_show_form(
            step_id="blocks",
            data_schema=vol.Schema(_block_fields(self._options)),
        )

    async def async_step_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Key selection (screen 3)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _merge_step(self._options, user_input, KEY_OPTION_KEYS)
            options = finalize_options(self._options)
            if error := _validate_keys(options):
                errors["base"] = error
            else:
                return await self._async_create_entry(options)

        return self.async_show_form(
            step_id="keys",
            data_schema=vol.Schema(_key_fields(self._options)),
            errors=errors,
        )

    async def _async_create_entry(
        self, options: dict[str, Any] | None = None
    ) -> FlowResult:
        """Validate connection and create the config entry."""
        resolved = finalize_options(options or self._options)
        try:
            await self.hass.async_add_executor_job(test_connection, None, DEFAULT_BAUD)
        except Exception:
            return self.async_show_form(
                step_id="user",
                data_schema=vol.Schema(_reading_fields(resolved)),
                errors={"base": "cannot_connect"},
            )

        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="Proxmark3",
            data={},
            options=resolved,
        )


class Proxmark3OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Proxmark3 options."""

    def __init__(self) -> None:
        """Initialize options flow."""
        self._options: dict[str, Any] = {}

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reading options (screen 1)."""
        if not self._options:
            self._options = merged_options(self.config_entry)

        if user_input is not None:
            _merge_step(self._options, user_input, READING_KEYS)
            if self._options.get(CONF_READ_RAW_BLOCKS):
                return await self.async_step_blocks()
            return self.async_create_entry(title="", data=finalize_options(self._options))

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(_reading_fields(self._options)),
        )

    async def async_step_blocks(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Block selection (screen 2)."""
        if user_input is not None:
            _merge_step(self._options, user_input, BLOCK_KEYS)
            return await self.async_step_keys()

        return self.async_show_form(
            step_id="blocks",
            data_schema=vol.Schema(_block_fields(self._options)),
        )

    async def async_step_keys(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Key selection (screen 3)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            _merge_step(self._options, user_input, KEY_OPTION_KEYS)
            options = finalize_options(self._options)
            if error := _validate_keys(options):
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=options)

        return self.async_show_form(
            step_id="keys",
            data_schema=vol.Schema(_key_fields(self._options)),
            errors=errors,
        )
