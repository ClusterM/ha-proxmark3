"""Proxmark3 hub: async polling and tag state."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store

from .const import (
    ABSENT_CONFIRM,
    BLOCK_RETRY,
    CONF_POLL_INTERVAL,
    CONF_READ_NTAG,
    CONF_READ_NTAG_CLASSIC,
    CONF_RECONNECT_INTERVAL,
    DEFAULT_BAUD,
    DEVICE_INFO_STORAGE_VERSION,
    DOMAIN,
    build_block0_key_list,
    build_block_list,
    build_key_list,
    merged_options,
)
from .pm3_client import (
    CardInfo,
    MagicUidError,
    Pm3DeviceInfo,
    Proxmark3Adapter,
    close_adapter,
    device_info_from_dict,
    device_info_to_dict,
    find_proxmark_port,
    get_device_info,
    is_device_lost,
    open_adapter,
    poll_card,
    read_first_blocks,
    read_ndef,
    write_magic_uid,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class TagState:
    """Current Proxmark3 and tag snapshot."""

    connected: bool
    uid: str | None
    tag_type: str | None
    blocks: dict[int, str | None] = field(default_factory=dict)
    ntag: str | None = None


def _empty_tag_state(connected: bool) -> TagState:
    return TagState(
        connected=connected,
        uid=None,
        tag_type=None,
        blocks={},
        ntag=None,
    )


def _blocks_to_hex(
    raw_blocks: list[tuple[int, bytes | None]],
) -> dict[int, str | None]:
    return {
        block_no: (data.hex().upper() if data is not None else None)
        for block_no, data in raw_blocks
    }


def _read_card_blocks_sync(
    adapter: Proxmark3Adapter,
    card: CardInfo,
    blocks: tuple[int, ...],
    keys: tuple[bytes, ...],
) -> dict[int, str | None]:
    if not blocks:
        return {}
    raw = read_first_blocks(adapter, card, blocks, keys)
    if BLOCK_RETRY and any(data is None for _, data in raw):
        retry = read_first_blocks(adapter, card, blocks, keys)
        raw = [
            (block_no, data if data is not None else retry[idx][1])
            for idx, (block_no, data) in enumerate(raw)
        ]
    return _blocks_to_hex(raw)


def _read_tag_sync(
    adapter: Proxmark3Adapter,
    card: CardInfo,
    blocks: tuple[int, ...],
    keys: tuple[bytes, ...],
    read_ntag: bool,
    read_ntag_classic: bool,
) -> tuple[dict[int, str | None], str | None]:
    block_data = _read_card_blocks_sync(adapter, card, blocks, keys)
    ntag_json: str | None = None
    if read_ntag or read_ntag_classic:
        records = read_ndef(
            adapter,
            card,
            keys,
            ultralight=read_ntag,
            classic=read_ntag_classic,
        )
        if records is not None:
            ntag_json = json.dumps(records, ensure_ascii=False, separators=(",", ":"))
    return block_data, ntag_json


class Proxmark3Hub:
    """Background poll loop for a single Proxmark3."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self._listeners: list[Callable[[], None]] = []
        self._state = _empty_tag_state(False)
        self._device_info: Pm3DeviceInfo | None = None
        self._task: asyncio.Task | None = None
        self._adapter: Proxmark3Adapter | None = None
        self._stop_event = asyncio.Event()
        self._io_lock = asyncio.Lock()

    @property
    def state(self) -> TagState:
        """Return current tag state."""
        return self._state

    @property
    def device_info(self) -> Pm3DeviceInfo | None:
        """Return last known device details (cached across disconnects)."""
        return self._device_info

    def _device_info_store(self) -> Store:
        return Store(
            self.hass,
            DEVICE_INFO_STORAGE_VERSION,
            f"{DOMAIN}.{self.entry.entry_id}",
        )

    async def async_load_device_info_cache(self) -> None:
        """Load cached device info from disk."""
        stored = await self._device_info_store().async_load()
        if not stored:
            return
        try:
            self._device_info = device_info_from_dict(stored)
        except (KeyError, TypeError, ValueError) as exc:
            _LOGGER.debug("Ignoring invalid cached device info: %s", exc)

    @callback
    def async_add_listener(self, update_callback: Callable[[], None]) -> Callable[[], None]:
        """Register a listener for state changes."""

        self._listeners.append(update_callback)

        @callback
        def remove_listener() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return remove_listener

    async def async_start(self) -> None:
        """Start the background poll task."""
        await self.async_load_device_info_cache()
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def async_stop(self) -> None:
        """Stop polling and close the serial adapter."""
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        async with self._io_lock:
            adapter = self._adapter
            self._adapter = None
            if adapter is not None:
                await self.hass.async_add_executor_job(close_adapter, adapter)

        await self._set_state(_empty_tag_state(False))

    async def async_write_magic_uid(self, uid: bytes | None = None) -> dict[str, str]:
        """Write a new UID to a Magic tag block 0; returns uid and block_0."""
        async with self._io_lock:
            adapter = self._adapter
            if adapter is None:
                raise MagicUidError("not_connected")

            options = merged_options(self.entry)
            keys = build_block0_key_list(options)
            result = await self.hass.async_add_executor_job(
                write_magic_uid,
                adapter,
                keys,
                uid,
            )

        await self._set_state(
            TagState(
                connected=True,
                uid=result["uid"],
                tag_type=result.get("tag_type"),
                blocks={0: result["block_0"]} if result.get("block_0") else {},
                ntag=self._state.ntag,
            )
        )
        return result

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._connect_and_poll()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if not is_device_lost(exc):
                    _LOGGER.exception("Unexpected Proxmark3 error")
                await self._handle_disconnect()
                options = merged_options(self.entry)
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=float(options[CONF_RECONNECT_INTERVAL]),
                    )
                    break
                except asyncio.TimeoutError:
                    continue

    async def _connect_and_poll(self) -> None:
        baud = DEFAULT_BAUD
        options = merged_options(self.entry)
        poll_interval = float(options[CONF_POLL_INTERVAL])
        blocks = build_block_list(options)
        keys = build_key_list(options)
        read_ntag = bool(options.get(CONF_READ_NTAG))
        read_ntag_classic = bool(options.get(CONF_READ_NTAG_CLASSIC))

        while not self._stop_event.is_set():
            if self._adapter is None:
                port = await self.hass.async_add_executor_job(find_proxmark_port)
                if port is None:
                    await self._set_state(_empty_tag_state(False))
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=float(options[CONF_RECONNECT_INTERVAL]),
                        )
                        return
                    except asyncio.TimeoutError:
                        continue

                try:
                    self._adapter = await self.hass.async_add_executor_job(
                        open_adapter, port, baud
                    )
                    device_info = await self.hass.async_add_executor_job(
                        get_device_info, self._adapter
                    )
                    if device_info is not None:
                        await self._set_device_info(device_info)
                except Exception as exc:
                    _LOGGER.debug("Proxmark3 connect failed on %s: %s", port, exc)
                    await self._set_state(_empty_tag_state(False))
                    try:
                        await asyncio.wait_for(
                            self._stop_event.wait(),
                            timeout=float(options[CONF_RECONNECT_INTERVAL]),
                        )
                        return
                    except asyncio.TimeoutError:
                        continue

                _LOGGER.info("Proxmark3 connected on %s", port)
                await self._set_state(_empty_tag_state(True))

            adapter = self._adapter
            try:
                await self._poll_once(
                    adapter,
                    poll_interval,
                    blocks,
                    keys,
                    read_ntag,
                    read_ntag_classic,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if is_device_lost(exc):
                    raise
                _LOGGER.debug("Poll error: %s", exc)
                await asyncio.sleep(poll_interval)

    async def _poll_once(
        self,
        adapter: Proxmark3Adapter,
        poll_interval: float,
        blocks: tuple[int, ...],
        keys: tuple[bytes, ...],
        read_ntag: bool,
        read_ntag_classic: bool,
    ) -> None:
        active_uid = self._state.uid
        absent_hits = 0

        while not self._stop_event.is_set() and self._adapter is adapter:
            card = None
            block_data: dict[int, str] = {}
            ntag_json: str | None = None

            async with self._io_lock:
                if self._adapter is not adapter:
                    break
                card = await self.hass.async_add_executor_job(poll_card, adapter)
                if card is not None and active_uid != card.uid_hex:
                    block_data, ntag_json = await self.hass.async_add_executor_job(
                        _read_tag_sync,
                        adapter,
                        card,
                        blocks,
                        keys,
                        read_ntag,
                        read_ntag_classic,
                    )

            if card is None:
                if active_uid is not None:
                    absent_hits += 1
                    if absent_hits >= ABSENT_CONFIRM:
                        active_uid = None
                        await self._set_state(
                            TagState(
                                connected=True,
                                uid=None,
                                tag_type=None,
                                blocks={},
                                ntag=None,
                            )
                        )
                        absent_hits = 0
                else:
                    absent_hits = 0
                    if poll_interval > 0:
                        await asyncio.sleep(poll_interval)
                continue

            absent_hits = 0
            current_uid = card.uid_hex

            if active_uid == current_uid:
                await asyncio.sleep(max(poll_interval, 0.1))
                continue

            active_uid = current_uid
            await self._set_state(
                TagState(
                    connected=True,
                    uid=current_uid,
                    tag_type=card.card_type,
                    blocks=block_data,
                    ntag=ntag_json,
                )
            )

    async def _handle_disconnect(self) -> None:
        adapter = self._adapter
        self._adapter = None
        if adapter is not None:
            await self.hass.async_add_executor_job(close_adapter, adapter)
        await self._set_state(_empty_tag_state(False))
        _LOGGER.warning("Proxmark3 disconnected")

    async def _set_device_info(self, info: Pm3DeviceInfo) -> None:
        if self._device_info == info:
            return
        self._device_info = info
        await self._device_info_store().async_save(device_info_to_dict(info))
        self._notify_listeners()

    async def _set_state(self, state: TagState) -> None:
        if (
            self._state.connected == state.connected
            and self._state.uid == state.uid
            and self._state.tag_type == state.tag_type
            and self._state.blocks == state.blocks
            and self._state.ntag == state.ntag
        ):
            return
        self._state = state
        self._notify_listeners()

    @callback
    def _notify_listeners(self) -> None:
        for listener in self._listeners:
            listener()
