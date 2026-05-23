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
    CONF_RECONNECT_INTERVAL,
    DEFAULT_BAUD,
    DEFAULT_OPTIONS,
    DOMAIN,
    build_block_list,
    build_key_list,
    merged_options,
)
from .pm3_client import test_connection

HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{12}$")

OPTION_KEYS: tuple[str, ...] = (
    CONF_KEY_FF,
    CONF_KEY_MAD,
    CONF_KEY_NDEF,
    CONF_KEY_NULL,
    CONF_CUSTOM_KEY,
    CONF_BLOCK_0,
    CONF_BLOCK_1,
    CONF_BLOCK_2,
    CONF_BLOCK_3,
    CONF_POLL_INTERVAL,
    CONF_RECONNECT_INTERVAL,
)


def _option_fields(opts: dict[str, Any]) -> dict[vol.Marker, Any]:
    return {
        vol.Required(CONF_KEY_FF, default=opts[CONF_KEY_FF]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_MAD, default=opts[CONF_KEY_MAD]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_NDEF, default=opts[CONF_KEY_NDEF]): selector.BooleanSelector(),
        vol.Required(CONF_KEY_NULL, default=opts[CONF_KEY_NULL]): selector.BooleanSelector(),
        vol.Optional(CONF_CUSTOM_KEY, default=opts[CONF_CUSTOM_KEY]): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
        ),
        vol.Required(CONF_BLOCK_0, default=opts[CONF_BLOCK_0]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_1, default=opts[CONF_BLOCK_1]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_2, default=opts[CONF_BLOCK_2]): selector.BooleanSelector(),
        vol.Required(CONF_BLOCK_3, default=opts[CONF_BLOCK_3]): selector.BooleanSelector(),
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


def _options_from_input(user_input: dict[str, Any]) -> dict[str, Any]:
    out = {key: user_input[key] for key in OPTION_KEYS}
    out[CONF_CUSTOM_KEY] = (out.get(CONF_CUSTOM_KEY) or "").strip().lower()
    out[CONF_POLL_INTERVAL] = float(out[CONF_POLL_INTERVAL])
    out[CONF_RECONNECT_INTERVAL] = float(out[CONF_RECONNECT_INTERVAL])
    return out


def _validate_options(options: dict[str, Any]) -> str | None:
    custom = options.get(CONF_CUSTOM_KEY) or ""
    if custom and not HEX_KEY_RE.match(custom):
        return "invalid_key"
    if not build_key_list(options):
        return "no_keys"
    if not build_block_list(options):
        return "no_blocks"
    return None


class Proxmark3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Proxmark3."""

    VERSION = 1

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
        """Set up Proxmark3."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        errors: dict[str, str] = {}

        if user_input is not None:
            options = _options_from_input(user_input)
            if error := _validate_options(options):
                errors["base"] = error
            else:
                try:
                    await self.hass.async_add_executor_job(
                        test_connection, None, DEFAULT_BAUD
                    )
                except Exception:
                    errors["base"] = "cannot_connect"
                else:
                    await self.async_set_unique_id(DOMAIN)
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title="Proxmark3",
                        data={},
                        options=options,
                    )

        opts = dict(DEFAULT_OPTIONS)
        schema = vol.Schema(_option_fields(opts))

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )


class Proxmark3OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Proxmark3 options."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            options = _options_from_input(user_input)
            if error := _validate_options(options):
                errors["base"] = error
            else:
                return self.async_create_entry(title="", data=options)

        opts = merged_options(self.config_entry)
        schema = vol.Schema(_option_fields(opts))

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
