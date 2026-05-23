"""Sensor platform for Proxmark3 MF tag."""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_NTAG,
    ATTR_TAG_TYPE,
    BLOCK_OPTIONS,
    CONF_READ_RAW_BLOCKS,
    DOMAIN,
    block_attr_name,
    merged_options,
)
from .hub import Proxmark3Hub
from .pm3_client import Pm3DeviceInfo

MANUFACTURER = "RfidResearchGroup"
MANUFACTURER_URL = "https://github.com/RfidResearchGroup/proxmark3"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Proxmark3 sensor from a config entry."""
    hub: Proxmark3Hub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([Proxmark3TagSensor(entry, hub)])


def _build_device_info(entry: ConfigEntry, info: Pm3DeviceInfo | None) -> DeviceInfo:
    base = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Proxmark3",
        manufacturer=MANUFACTURER,
        configuration_url=MANUFACTURER_URL,
    )
    if info is None:
        return base

    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Proxmark3",
        manufacturer=MANUFACTURER,
        configuration_url=MANUFACTURER_URL,
        serial_number=info.serial_number,
        sw_version=info.os_version,
        hw_version=info.mcu,
    )


class Proxmark3TagSensor(SensorEntity):
    """MF tag UID and block data from Proxmark3."""

    _attr_has_entity_name = True
    _attr_name = "MF Tag"
    _attr_icon = "mdi:nfc-variant"
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, hub: Proxmark3Hub) -> None:
        self._entry = entry
        self._hub = hub
        self._attr_unique_id = f"{entry.entry_id}_mf_tag"
        self._unsub: Callable[[], None] | None = None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device registry info (cached firmware/hardware when offline)."""
        return _build_device_info(self._entry, self._hub.device_info)

    async def async_added_to_hass(self) -> None:
        """Subscribe to hub updates."""
        self._unsub = self._hub.async_add_listener(self._handle_hub_update)
        self._sync_from_hub()

    async def async_will_remove_from_hass(self) -> None:
        """Unsubscribe from hub updates."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_hub_update(self) -> None:
        self._sync_from_hub()
        self.async_write_ha_state()

    @callback
    def _sync_from_hub(self) -> None:
        state = self._hub.state
        info = self._hub.device_info
        self._attr_available = state.connected
        self._attr_native_value = state.uid

        options = merged_options(self._entry)
        attrs: dict[str, str | None] = {
            ATTR_TAG_TYPE: state.tag_type,
        }
        if info is not None:
            attrs["bootrom"] = info.bootrom
            attrs["compiler"] = info.compiler
            attrs["fpga"] = info.fpga
            if info.memory_kb:
                attrs["memory"] = f"{info.memory_kb} KB ({info.memory_used_percent:.0f}% used)"
        if options.get(CONF_READ_RAW_BLOCKS):
            for idx, conf_key in enumerate(BLOCK_OPTIONS):
                if options.get(conf_key):
                    attrs[block_attr_name(idx)] = state.blocks.get(idx)
        if state.ntag is not None:
            attrs[ATTR_NTAG] = state.ntag
        self._attr_extra_state_attributes = attrs
