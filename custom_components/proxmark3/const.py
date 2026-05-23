"""Constants for the Proxmark3 integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry

DOMAIN = "proxmark3"

DEVICE_INFO_STORAGE_VERSION = 1

CONF_CUSTOM_KEY = "custom_key"
CONF_POLL_INTERVAL = "poll_interval"
CONF_RECONNECT_INTERVAL = "reconnect_interval"

CONF_KEY_FF = "key_ff"
CONF_KEY_MAD = "key_mad"
CONF_KEY_NDEF = "key_ndef"
CONF_KEY_NULL = "key_null"

CONF_BLOCK_0 = "block_0"
CONF_BLOCK_1 = "block_1"
CONF_BLOCK_2 = "block_2"
CONF_BLOCK_3 = "block_3"
CONF_READ_NTAG = "read_ntag"
CONF_READ_NTAG_CLASSIC = "read_ntag_classic"
CONF_READ_RAW_BLOCKS = "read_raw_blocks"

DEFAULT_BAUD = 115200
DEFAULT_POLL_INTERVAL = 0.05
DEFAULT_RECONNECT_INTERVAL = 5.0

DEFAULT_KEY_FF = True
DEFAULT_KEY_MAD = False
DEFAULT_KEY_NDEF = False
DEFAULT_KEY_NULL = False
DEFAULT_CUSTOM_KEY = ""

DEFAULT_BLOCK_0 = False
DEFAULT_BLOCK_1 = False
DEFAULT_BLOCK_2 = False
DEFAULT_BLOCK_3 = False
DEFAULT_READ_NTAG = False
DEFAULT_READ_NTAG_CLASSIC = False
DEFAULT_READ_RAW_BLOCKS = False

PRESET_KEY_FF = "ffffffffffff"
PRESET_KEY_MAD = "a0a1a2a3a4a5"
PRESET_KEY_NDEF = "d3f7d3f7d3f7"
PRESET_KEY_NULL = "000000000000"

BLOCK_OPTIONS = (
    CONF_BLOCK_0,
    CONF_BLOCK_1,
    CONF_BLOCK_2,
    CONF_BLOCK_3,
)

ATTR_TAG_TYPE = "tag_type"
ATTR_NTAG = "ntag"

ABSENT_CONFIRM = 2
BLOCK_RETRY = 1

DEFAULT_OPTIONS: dict[str, Any] = {
    CONF_KEY_FF: DEFAULT_KEY_FF,
    CONF_KEY_MAD: DEFAULT_KEY_MAD,
    CONF_KEY_NDEF: DEFAULT_KEY_NDEF,
    CONF_KEY_NULL: DEFAULT_KEY_NULL,
    CONF_CUSTOM_KEY: DEFAULT_CUSTOM_KEY,
    CONF_BLOCK_0: DEFAULT_BLOCK_0,
    CONF_BLOCK_1: DEFAULT_BLOCK_1,
    CONF_BLOCK_2: DEFAULT_BLOCK_2,
    CONF_BLOCK_3: DEFAULT_BLOCK_3,
    CONF_READ_NTAG: DEFAULT_READ_NTAG,
    CONF_READ_NTAG_CLASSIC: DEFAULT_READ_NTAG_CLASSIC,
    CONF_READ_RAW_BLOCKS: DEFAULT_READ_RAW_BLOCKS,
    CONF_POLL_INTERVAL: DEFAULT_POLL_INTERVAL,
    CONF_RECONNECT_INTERVAL: DEFAULT_RECONNECT_INTERVAL,
}


def merged_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return options merged with defaults and legacy migration."""
    raw = dict(entry.options or {})
    out = dict(DEFAULT_OPTIONS)
    for key, default in DEFAULT_OPTIONS.items():
        if key not in raw:
            continue
        val = raw[key]
        out[key] = default if val is None else val

    if CONF_READ_RAW_BLOCKS not in raw and any(raw.get(key) for key in BLOCK_OPTIONS):
        out[CONF_READ_RAW_BLOCKS] = True

    if CONF_READ_NTAG_CLASSIC not in raw and raw.get(CONF_READ_NTAG):
        out[CONF_READ_NTAG_CLASSIC] = True

    if CONF_POLL_INTERVAL in out:
        out[CONF_POLL_INTERVAL] = float(out[CONF_POLL_INTERVAL])
    if CONF_RECONNECT_INTERVAL in out:
        out[CONF_RECONNECT_INTERVAL] = float(out[CONF_RECONNECT_INTERVAL])
    return finalize_options(out)


def finalize_options(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize option values before save or use."""
    out = dict(options)
    out[CONF_CUSTOM_KEY] = (out.get(CONF_CUSTOM_KEY) or "").strip().lower()
    out[CONF_POLL_INTERVAL] = float(out[CONF_POLL_INTERVAL])
    out[CONF_RECONNECT_INTERVAL] = float(out[CONF_RECONNECT_INTERVAL])

    if not out.get(CONF_READ_RAW_BLOCKS):
        for key in BLOCK_OPTIONS:
            out[key] = False
    return out


def build_key_list(options: dict[str, Any]) -> tuple[bytes, ...]:
    """Build ordered key list from option flags."""
    keys: list[bytes] = []
    if options.get(CONF_KEY_FF):
        keys.append(bytes.fromhex(PRESET_KEY_FF))
    if options.get(CONF_KEY_MAD):
        keys.append(bytes.fromhex(PRESET_KEY_MAD))
    if options.get(CONF_KEY_NDEF):
        keys.append(bytes.fromhex(PRESET_KEY_NDEF))
    if options.get(CONF_KEY_NULL):
        keys.append(bytes.fromhex(PRESET_KEY_NULL))
    custom = (options.get(CONF_CUSTOM_KEY) or "").strip().lower()
    if custom:
        keys.append(bytes.fromhex(custom))
    return tuple(keys)


def build_block_list(options: dict[str, Any]) -> tuple[int, ...]:
    """Return enabled block numbers in order."""
    if not options.get(CONF_READ_RAW_BLOCKS):
        return ()
    blocks: list[int] = []
    for idx, conf_key in enumerate(BLOCK_OPTIONS):
        if options.get(conf_key):
            blocks.append(idx)
    return tuple(blocks)


def block_attr_name(block_no: int) -> str:
    """Sensor attribute name for a MIFARE block."""
    return f"block_{block_no}"
