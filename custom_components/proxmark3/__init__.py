"""The Proxmark3 integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, SERVICE_WRITE_MAGIC_UID
from .hub import Proxmark3Hub
from .magic_block0 import parse_uid_hex
from .pm3_client import MagicUidError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]

SERVICE_SCHEMA = vol.Schema({vol.Optional("uid"): str}, extra=vol.ALLOW_EXTRA)

_MAGIC_UID_ERRORS: dict[str, str] = {
    "not_connected": "Proxmark3 is not connected",
    "no_card": "No tag on the reader",
    "not_classic": "Tag is not MIFARE Classic",
    "unsupported_uid_len": "UID length is not supported (4 or 7 bytes only)",
    "invalid_uid_len": "UID length does not match the tag (4 bytes = 8 hex, 7 bytes = 14 hex)",
    "block0_read_failed": "Failed to read block 0 (check Classic keys)",
    "block0_write_failed": "Failed to write block 0 (tag may not be a writable Magic tag)",
    "verify_failed": "Block 0 write could not be verified",
}


def _get_hub(hass: HomeAssistant) -> Proxmark3Hub:
    domain_data = hass.data.get(DOMAIN)
    if not domain_data:
        raise HomeAssistantError("Proxmark3 integration is not loaded")
    return next(iter(domain_data.values()))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Proxmark3 from a config entry."""
    hub = Proxmark3Hub(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub

    await hub.async_start()
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if not hass.services.has_service(DOMAIN, SERVICE_WRITE_MAGIC_UID):

        async def handle_write_magic_uid(call: ServiceCall) -> dict[str, str]:
            hub = _get_hub(hass)
            uid_value = call.data.get("uid")
            uid: bytes | None = None
            if uid_value:
                try:
                    uid = parse_uid_hex(str(uid_value))
                except ValueError as exc:
                    raise HomeAssistantError(f"Invalid UID: {uid_value}") from exc
            try:
                return await hub.async_write_magic_uid(uid)
            except MagicUidError as exc:
                message = _MAGIC_UID_ERRORS.get(exc.code, exc.code)
                raise HomeAssistantError(message) from exc

        hass.services.async_register(
            DOMAIN,
            SERVICE_WRITE_MAGIC_UID,
            handle_write_magic_uid,
            schema=SERVICE_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    hub: Proxmark3Hub | None = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if hub is not None:
        await hub.async_stop()

    if not hass.data.get(DOMAIN):
        hass.data.pop(DOMAIN, None)
        if hass.services.has_service(DOMAIN, SERVICE_WRITE_MAGIC_UID):
            hass.services.async_remove(DOMAIN, SERVICE_WRITE_MAGIC_UID)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
