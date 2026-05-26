"""Minimal Proxmark3 serial client (Iceman NG/MIX protocol)."""

from __future__ import annotations

import re
import struct
import time
from dataclasses import dataclass

import serial
import serial.tools.list_ports
from proxmark3 import Packet, Proxmark3Adapter

CMD_ACK = 0x00FF
CMD_VERSION = 0x0107
CMD_PING = 0x0109
CMD_HF_ISO14443A_READER = 0x0385
CMD_HF_DROPFIELD = 0x0430
CMD_HF_MIFARE_READBL = 0x0620
CMD_HF_MIFARE_WRITEBL = 0x0622
CMD_HF_MIFARE_READBL_EX = 0x0628

ISO14A_CONNECT = 1 << 0
ISO14A_NO_DISCONNECT = 1 << 1
ISO14A_CLEARTRACE = 1 << 17

MF_WAKE_WUPA = 1
MF_KEY_A = 0
ISO14443A_CMD_READBLOCK = 0x30

PM3_SUCCESS = 0
MIX_ARG_BYTES = 24
CARD_SELECT_SIZE = 271
DEFAULT_TIMEOUT = 2.0
HF_POLL_TIMEOUT = 1.0

RESPONSE_MAGIC = b"PM3b"
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")

CHIP_ID_TO_MCU: dict[int, str] = {
    0x270B0A40: "AT91SAM7S512 Rev A",
    0x270B0A4E: "AT91SAM7S512 Rev B",
    0x270B0A4F: "AT91SAM7S512 Rev B",
    0x270D0940: "AT91SAM7S256 Rev A",
    0x270B0941: "AT91SAM7S256 Rev B",
    0x270B0942: "AT91SAM7S256 Rev C",
    0x270B0943: "AT91SAM7S256 Rev D",
    0x270C0740: "AT91SAM7S128 Rev A",
    0x270A0741: "AT91SAM7S128 Rev B",
    0x270A0742: "AT91SAM7S128 Rev C",
    0x270A0743: "AT91SAM7S128 Rev D",
    0x27090540: "AT91SAM7S64 Rev A",
    0x27090543: "AT91SAM7S64 Rev B",
    0x27090544: "AT91SAM7S64 Rev C",
    0x27080342: "AT91SAM7S321 Rev A",
    0x27080340: "AT91SAM7S32 Rev A",
    0x27080341: "AT91SAM7S32 Rev B",
    0x27050241: "AT91SAM7S161 Rev A",
    0x27050240: "AT91SAM7S16 Rev A",
}

MEMORY_KB_BY_CHIP_CODE: dict[int, int] = {
    1: 8,
    2: 16,
    3: 32,
    5: 64,
    7: 128,
    9: 256,
    10: 512,
    12: 1024,
    14: 2048,
}


@dataclass(frozen=True)
class Pm3DeviceInfo:
    """Proxmark3 hardware and firmware details from CMD_VERSION."""

    chip_id: int
    mcu: str
    memory_kb: int
    memory_used_percent: float
    bootrom: str | None
    os_version: str | None
    compiler: str | None
    fpga: str | None

    @property
    def serial_number(self) -> str:
        return f"{self.chip_id:08X}"


def device_info_to_dict(info: Pm3DeviceInfo) -> dict[str, int | float | str | None]:
    """Serialize device info for persistent storage."""
    return {
        "chip_id": info.chip_id,
        "mcu": info.mcu,
        "memory_kb": info.memory_kb,
        "memory_used_percent": info.memory_used_percent,
        "bootrom": info.bootrom,
        "os_version": info.os_version,
        "compiler": info.compiler,
        "fpga": info.fpga,
    }


def device_info_from_dict(data: dict) -> Pm3DeviceInfo:
    """Restore device info from persistent storage."""
    return Pm3DeviceInfo(
        chip_id=int(data["chip_id"]),
        mcu=str(data["mcu"]),
        memory_kb=int(data["memory_kb"]),
        memory_used_percent=float(data["memory_used_percent"]),
        bootrom=data.get("bootrom"),
        os_version=data.get("os_version"),
        compiler=data.get("compiler"),
        fpga=data.get("fpga"),
    )


@dataclass(frozen=True)
class CardInfo:
    """Detected ISO14443-A card."""

    uid: bytes
    uidlen: int
    atqa: bytes
    sak: int
    card_type: str

    @property
    def uid_hex(self) -> str:
        return self.uid[: self.uidlen].hex().upper()


def find_proxmark_port() -> str | None:
    """Return the first serial port that looks like a Proxmark3."""
    for port in serial.tools.list_ports.comports():
        desc = f"{port.description} {port.manufacturer or ''} {port.product or ''}".lower()
        if "proxmark" in desc or (port.vid == 0x9AC4 and port.pid == 0x4B8F):
            return port.device
    return None


def is_device_lost(exc: BaseException) -> bool:
    """Return True when the serial link needs a reconnect."""
    if isinstance(exc, (serial.SerialException, OSError)):
        return True
    message = str(exc).lower()
    return "already open" in message


def _force_close_serial(adapter: Proxmark3Adapter | None) -> None:
    """Best-effort close of the underlying serial port."""
    if adapter is None:
        return
    try:
        if adapter.is_open:
            adapter.close()
    except (serial.SerialException, OSError, AttributeError):
        pass
    serial_port = getattr(adapter, "ser", None)
    if serial_port is not None:
        try:
            serial_port.close()
        except (serial.SerialException, OSError):
            pass


def close_adapter(adapter: Proxmark3Adapter | None) -> None:
    """Close adapter; always discard the object after calling this."""
    if adapter is None:
        return
    _force_close_serial(adapter)


def open_adapter(
    port: str,
    baudrate: int = 115200,
    timeout: float = DEFAULT_TIMEOUT,
) -> Proxmark3Adapter:
    """Open a fresh Proxmark3Adapter and verify connectivity.

    Proxmark3Adapter subclasses pyserial.Serial, which auto-opens the port in
    __init__ when a port name is passed — do not call open() again.
    """
    last_error: BaseException | None = None
    for attempt in range(3):
        adapter: Proxmark3Adapter | None = None
        try:
            adapter = Proxmark3Adapter(port, baudrate=baudrate, timeout=timeout)
            sync_device(adapter)
            return adapter
        except (serial.SerialException, RuntimeError, OSError) as exc:
            last_error = exc
            _force_close_serial(adapter)
            if attempt < 2:
                time.sleep(0.1 + 0.05 * attempt)
                continue
            raise
    if last_error is not None:
        raise last_error
    raise RuntimeError("Failed to open Proxmark3")


def flush_input(adapter: Proxmark3Adapter) -> None:
    adapter.reset_input_buffer()


def drain_input(adapter: Proxmark3Adapter, idle_timeout: float = 0.01) -> None:
    """Discard any stale bytes already sitting in the serial RX buffer."""
    old_timeout = adapter.timeout
    try:
        adapter.timeout = idle_timeout
        while adapter.read(512):
            pass
    finally:
        adapter.timeout = old_timeout
    adapter.reset_input_buffer()


def read_exact(adapter: Proxmark3Adapter, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = adapter.read(size - len(buf))
        if not chunk:
            raise TimeoutError(f"Incomplete frame: got {len(buf)} of {size} bytes")
        buf.extend(chunk)
    return bytes(buf)


def read_response_ng(adapter: Proxmark3Adapter, timeout: float | None = None) -> dict:
    old_timeout = adapter.timeout
    if timeout is not None:
        adapter.timeout = timeout

    try:
        deadline = time.monotonic() + float(adapter.timeout or DEFAULT_TIMEOUT)
        buf = bytearray()

        while time.monotonic() < deadline:
            if len(buf) < 10:
                chunk = adapter.read(10 - len(buf))
            else:
                chunk = adapter.read(512)
            if chunk:
                buf.extend(chunk)

            magic_at = buf.find(RESPONSE_MAGIC)
            if magic_at >= 0 and len(buf) >= magic_at + 10:
                preamble = bytes(buf[magic_at : magic_at + 10])
                length_ng = struct.unpack_from("<H", preamble, 4)[0]
                payload_len = length_ng & 0x7FFF
                frame_len = 10 + payload_len + 2
                frame = bytes(buf[magic_at:])
                if len(frame) < frame_len:
                    frame += read_exact(adapter, frame_len - len(frame))

                payload = frame[10 : 10 + payload_len]
                status = struct.unpack_from("<b", frame, 6)[0]
                reason = struct.unpack_from("<b", frame, 7)[0]
                cmd = struct.unpack_from("<H", frame, 8)[0]
                ng = bool(length_ng & 0x8000)
                oldarg = [0, 0, 0]
                data = payload
                if not ng and payload_len >= MIX_ARG_BYTES:
                    oldarg[0] = struct.unpack_from("<Q", payload, 0)[0]
                    oldarg[1] = struct.unpack_from("<Q", payload, 8)[0]
                    oldarg[2] = struct.unpack_from("<Q", payload, 16)[0]
                    data = payload[MIX_ARG_BYTES:]

                return {
                    "cmd": cmd,
                    "status": status,
                    "reason": reason,
                    "ng": ng,
                    "oldarg": oldarg,
                    "data": data,
                }

        raise TimeoutError("Timed out waiting for Proxmark3 response")
    finally:
        adapter.timeout = old_timeout


def sync_device(adapter: Proxmark3Adapter) -> None:
    """Drain stale data and verify the device responds (safe when no tag present)."""
    drain_input(adapter)
    resp = send_command_ng(adapter, CMD_PING, flush_only=False)
    if resp["cmd"] != CMD_PING or resp["status"] != PM3_SUCCESS:
        raise RuntimeError("Proxmark3 did not respond to PING")


def send_command_ng(
    adapter: Proxmark3Adapter,
    cmd: int,
    data: bytes | None = None,
    timeout: float | None = None,
    *,
    flush_only: bool = True,
) -> dict:
    packet = Packet(data) if data else None
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            if attempt == 0 and flush_only:
                flush_input(adapter)
            else:
                drain_input(adapter)
            adapter.sendCommandNG(cmd, packet)
            return read_response_ng(adapter, timeout=timeout)
        except (TimeoutError, RuntimeError) as exc:
            last_error = exc
            drain_input(adapter)
            if attempt == 0:
                time.sleep(0.02)

    raise RuntimeError(f"Proxmark3 command 0x{cmd:04x} failed: {last_error}")


def send_command_mix(
    adapter: Proxmark3Adapter,
    cmd: int,
    arg0: int = 0,
    arg1: int = 0,
    arg2: int = 0,
    data: bytes = b"",
    timeout: float | None = None,
    *,
    flush_only: bool = True,
) -> dict:
    packet = Packet(len(data) + 24)
    if data:
        packet[24:] = data
    packet.set_uint64(0, arg0)
    packet.set_uint64(8, arg1)
    packet.set_uint64(16, arg2)

    last_error: Exception | None = None
    for attempt in range(2):
        try:
            if attempt == 0 and flush_only:
                flush_input(adapter)
            else:
                drain_input(adapter)
            adapter.sendCommandNG(cmd, packet, False)
            return read_response_ng(adapter, timeout=timeout)
        except (TimeoutError, RuntimeError) as exc:
            last_error = exc
            drain_input(adapter)
            if attempt == 0:
                time.sleep(0.02)

    raise RuntimeError(f"Proxmark3 MIX command 0x{cmd:04x} failed: {last_error}")


def drop_field(adapter: Proxmark3Adapter) -> None:
    """Turn off HF field; ignore missing/late responses when already off."""
    try:
        send_command_ng(adapter, CMD_HF_DROPFIELD, timeout=0.3)
    except RuntimeError:
        drain_input(adapter)


def poll_card(adapter: Proxmark3Adapter, keep_field: bool = False) -> CardInfo | None:
    flags = ISO14A_CONNECT | ISO14A_CLEARTRACE
    if keep_field:
        flags |= ISO14A_NO_DISCONNECT

    try:
        resp = send_command_mix(
            adapter,
            CMD_HF_ISO14443A_READER,
            flags,
            0,
            0,
            timeout=HF_POLL_TIMEOUT,
        )
    except RuntimeError:
        return None

    if resp["cmd"] != CMD_ACK or resp["oldarg"][0] == 0:
        return None

    return parse_card_select(resp["data"][:CARD_SELECT_SIZE])


def parse_card_select(raw: bytes) -> CardInfo | None:
    if len(raw) < 15:
        return None

    uid = raw[0:10]
    uidlen = raw[10]
    if uidlen == 0 or uidlen > 10:
        return None

    atqa = raw[11:13]
    sak = raw[13]
    card_type = guess_card_type(sak, atqa, uidlen)
    return CardInfo(uid=uid, uidlen=uidlen, atqa=atqa, sak=sak, card_type=card_type)


def guess_card_type(sak: int, atqa: bytes, uidlen: int) -> str:
    atqa_val = (atqa[1] << 8) | atqa[0]

    if sak == 0x08 and atqa_val in (0x0004, 0x0400):
        return "MIFARE Classic 1K"
    if sak == 0x18 and atqa_val in (0x0002, 0x0200):
        return "MIFARE Classic 4K"
    if sak == 0x09:
        return "MIFARE Mini"
    if sak == 0x00 and atqa_val in (0x0044, 0x4400):
        return "MIFARE Ultralight / NTAG"
    if sak == 0x20 and atqa_val == 0x0400:
        return "MIFARE DESFire / JCOP"
    if sak == 0x28:
        return "MIFARE Classic 1K (7-byte UID)"
    if sak == 0x38:
        return "MIFARE Classic 4K (7-byte UID)"
    if uidlen == 7:
        return f"ISO14443-A 7-byte UID (SAK={sak:02X}, ATQA={atqa_val:04X})"
    return f"ISO14443-A (SAK={sak:02X}, ATQA={atqa_val:04X})"


def is_ultralight(card: CardInfo) -> bool:
    atqa_val = (card.atqa[1] << 8) | card.atqa[0]
    return card.sak == 0x00 and atqa_val in (0x0044, 0x4400)


def is_mifare_classic(card: CardInfo) -> bool:
    return "Classic" in card.card_type or "Mini" in card.card_type


def _unique_keys(*key_groups: tuple[bytes, ...]) -> tuple[bytes, ...]:
    seen: set[bytes] = set()
    ordered: list[bytes] = []
    for group in key_groups:
        for key in group:
            if key in seen:
                continue
            seen.add(key)
            ordered.append(key)
    return tuple(ordered)


def read_classic_block(
    adapter: Proxmark3Adapter,
    block_no: int,
    keys: tuple[bytes, ...],
) -> bytes | None:
    """Try keys in order; stop on first successful read."""
    for key in keys:
        payload = struct.pack("<BB", block_no, MF_KEY_A) + key
        try:
            resp = send_command_ng(adapter, CMD_HF_MIFARE_READBL, payload, timeout=1.5)
        except RuntimeError:
            continue
        data = resp["data"]
        if (
            resp["cmd"] == CMD_HF_MIFARE_READBL
            and resp["status"] == PM3_SUCCESS
            and len(data) >= 16
            and not data.startswith(b"\x01\x00Au")
        ):
            return data[:16]
    return None


def write_classic_block(
    adapter: Proxmark3Adapter,
    block_no: int,
    block_data: bytes,
    keys: tuple[bytes, ...],
    *,
    key_type: int = MF_KEY_A,
) -> bool:
    """Write one MIFARE Classic block; try keys in order."""
    if len(block_data) != 16:
        raise ValueError("block_data must be 16 bytes")

    for key in keys:
        payload = bytearray(26)
        payload[0:6] = key
        payload[10:26] = block_data
        try:
            resp = send_command_mix(
                adapter,
                CMD_HF_MIFARE_WRITEBL,
                block_no,
                key_type,
                0,
                bytes(payload),
                timeout=2.0,
            )
        except RuntimeError:
            continue
        if resp["cmd"] == CMD_ACK and resp["oldarg"][0] > 0:
            return True
    return False


def write_magic_uid(
    adapter: Proxmark3Adapter,
    keys: tuple[bytes, ...],
    uid: bytes | None = None,
) -> dict[str, str]:
    """Write a random (or given) UID to a Magic MIFARE Classic block 0."""
    from .magic_block0 import build_block0_from_uid, validate_block0

    card = poll_card(adapter, keep_field=True)
    if card is None:
        raise MagicUidError("no_card")
    if not is_mifare_classic(card):
        raise MagicUidError("not_classic")
    if card.uidlen not in (4, 7):
        raise MagicUidError("unsupported_uid_len")

    block_before = read_classic_block(adapter, 0, keys)
    if block_before is None:
        raise MagicUidError("block0_read_failed")

    try:
        plan = build_block0_from_uid(block_before, uid, uid_len=card.uidlen)
    except ValueError:
        raise MagicUidError("invalid_uid_len") from None

    if block_before != plan.block and not write_classic_block(adapter, 0, plan.block, keys):
        drop_field(adapter)
        raise MagicUidError("block0_write_failed")

    drop_field(adapter)
    time.sleep(0.15)

    card_after = poll_card(adapter, keep_field=True)
    block_after = read_classic_block(adapter, 0, keys)
    drop_field(adapter)

    if card_after is None or block_after is None:
        raise MagicUidError("verify_failed")
    if block_after != plan.block or not validate_block0(block_after, card.uidlen):
        raise MagicUidError("verify_failed")
    if card_after.uid_hex != plan.uid.hex().upper():
        raise MagicUidError("verify_failed")

    return {
        "uid": plan.uid.hex().upper(),
        "block_0": block_after.hex().upper(),
        "tag_type": card_after.card_type,
    }


class MagicUidError(Exception):
    """Magic tag block 0 write failed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def read_classic_sector(
    adapter: Proxmark3Adapter,
    sector_no: int,
    keys: tuple[bytes, ...],
) -> bytes | None:
    """Read all four blocks of a Classic sector with Key A."""
    first_block = sector_no * 4
    for key in keys:
        sector = bytearray()
        for offset in range(4):
            block = read_classic_block(adapter, first_block + offset, (key,))
            if block is None:
                break
            sector.extend(block)
        if len(sector) == 64:
            return bytes(sector)
    return None


def read_ultralight_block(adapter: Proxmark3Adapter, block_no: int) -> bytes | None:
    payload = struct.pack("<BB6sBB", MF_WAKE_WUPA, 0, b"\x00" * 6, ISO14443A_CMD_READBLOCK, block_no)
    try:
        resp = send_command_ng(adapter, CMD_HF_MIFARE_READBL_EX, payload, timeout=1.5)
    except RuntimeError:
        return None
    if resp["cmd"] == CMD_HF_MIFARE_READBL_EX and resp["status"] == PM3_SUCCESS and len(resp["data"]) >= 16:
        return resp["data"][:16]
    return None


def read_first_blocks(
    adapter: Proxmark3Adapter,
    card: CardInfo,
    blocks: tuple[int, ...],
    keys: tuple[bytes, ...],
) -> list[tuple[int, bytes | None]]:
    result: list[tuple[int, bytes | None]] = []
    if is_ultralight(card):
        for block_no in blocks:
            result.append((block_no, read_ultralight_block(adapter, block_no)))
        return result

    for block_no in blocks:
        result.append((block_no, read_classic_block(adapter, block_no, keys)))
    return result


def read_ultralight_user_area(adapter: Proxmark3Adapter, num_bytes: int) -> bytes | None:
    """Read Ultralight/NTAG user memory starting at page 4."""
    if num_bytes <= 0:
        return b""
    buf = bytearray()
    page = 4
    while len(buf) < num_bytes:
        chunk = read_ultralight_block(adapter, page)
        if chunk is None:
            return None
        buf.extend(chunk)
        page += 4
    return bytes(buf[:num_bytes])


def _read_ultralight_ndef(adapter: Proxmark3Adapter) -> list[dict] | None:
    from .ndef_parse import cc_max_ndef_bytes, parse_ntag_user_area

    header = read_ultralight_block(adapter, 0)
    if header is None or len(header) < 16:
        return None

    maxsize = cc_max_ndef_bytes(header[12:16])
    if maxsize <= 0:
        maxsize = 144
    maxsize = min(maxsize, 872)

    user_area = read_ultralight_user_area(adapter, maxsize)
    if user_area is None:
        return None
    return parse_ntag_user_area(user_area)


def _read_classic_ndef(
    adapter: Proxmark3Adapter,
    keys: tuple[bytes, ...],
) -> list[dict] | None:
    """Read NDEF from a MIFARE Classic tag via MAD (hf mf ndefread logic)."""
    from .mad import (
        MAD_KEY,
        MF_MAD1_SECTOR,
        MF_MAD2_SECTOR,
        MFBLOCK_SIZE,
        NDEF_KEY,
        NDEF_MFC_AID,
        mad_decode,
    )
    from .ndef_parse import parse_ndef_buffer

    mad_keys = _unique_keys((MAD_KEY,), keys)
    sector0 = read_classic_sector(adapter, MF_MAD1_SECTOR, mad_keys)
    if sector0 is None:
        return None

    sector16 = read_classic_sector(adapter, MF_MAD2_SECTOR, mad_keys)
    aids = mad_decode(sector0, sector16)
    if aids is None:
        aids = mad_decode(sector0, sector16, override=True)
    if not aids:
        return None

    ndef_keys = _unique_keys((NDEF_KEY, bytes.fromhex("ffffffffffff")), keys)
    payload = bytearray()
    for index, aid in enumerate(aids):
        if aid != NDEF_MFC_AID:
            continue
        sector = read_classic_sector(adapter, index + 1, ndef_keys)
        if sector is None:
            return None
        payload.extend(sector[: MFBLOCK_SIZE * 3])

    if not payload:
        return []
    return parse_ndef_buffer(bytes(payload))


def read_ndef(
    adapter: Proxmark3Adapter,
    card: CardInfo,
    keys: tuple[bytes, ...] = (),
    *,
    ultralight: bool = True,
    classic: bool = False,
) -> list[dict] | None:
    """Read and decode NDEF records when enabled for the detected tag type."""
    if is_ultralight(card) and ultralight:
        return _read_ultralight_ndef(adapter)
    if is_mifare_classic(card) and classic:
        return _read_classic_ndef(adapter, keys)
    return None


def read_ntag_ndef(
    adapter: Proxmark3Adapter,
    card: CardInfo,
    keys: tuple[bytes, ...] = (),
) -> list[dict] | None:
    """Backward-compatible helper: read NDEF for any supported tag type."""
    return read_ndef(adapter, card, keys, ultralight=True, classic=True)


def _strip_ansi(text: str) -> str:
    return ANSI_ESCAPE_RE.sub("", text)


def _memory_kb_from_chip_id(chip_id: int) -> int:
    code = (chip_id & 0xF00) >> 8
    return MEMORY_KB_BY_CHIP_CODE.get(code, 0)


def _lookup_mcu(chip_id: int) -> str:
    return CHIP_ID_TO_MCU.get(chip_id, f"Unknown (0x{chip_id:08X})")


def _parse_labeled_field(text: str, label: str) -> str | None:
    match = re.search(rf"{re.escape(label)}\.+\s*(.+)", text)
    if not match:
        return None
    return match.group(1).strip()


def _parse_fpga_summary(text: str) -> str | None:
    if "[ FPGA ]" not in text:
        return None
    lines: list[str] = []
    in_fpga = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if "[ FPGA ]" in line:
            in_fpga = True
            continue
        if not in_fpga or not line:
            continue
        if line.startswith("["):
            break
        lines.append(line)
    return "; ".join(lines) if lines else None


def parse_version_response(data: bytes) -> Pm3DeviceInfo | None:
    """Parse CMD_VERSION payload (Iceman NG binary header + text block)."""
    if len(data) < 12:
        return None

    chip_id, section_size, versionstr_len = struct.unpack_from("<III", data, 0)
    text_end = 12 + max(versionstr_len, 0)
    version_blob = data[12:text_end] if text_end <= len(data) else data[12:]
    version_text = _strip_ansi(version_blob.split(b"\x00", 1)[0].decode("utf-8", errors="replace"))

    memory_kb = _memory_kb_from_chip_id(chip_id)
    if memory_kb:
        memory_used_percent = (section_size / (memory_kb * 1024)) * 100
    else:
        memory_used_percent = 0.0

    return Pm3DeviceInfo(
        chip_id=chip_id,
        mcu=_lookup_mcu(chip_id),
        memory_kb=memory_kb,
        memory_used_percent=memory_used_percent,
        bootrom=_parse_labeled_field(version_text, "Bootrom"),
        os_version=_parse_labeled_field(version_text, "OS"),
        compiler=_parse_labeled_field(version_text, "Compiler"),
        fpga=_parse_fpga_summary(version_text),
    )


def get_device_info(adapter: Proxmark3Adapter) -> Pm3DeviceInfo | None:
    """Read Proxmark3 hardware and firmware information."""
    try:
        resp = send_command_ng(adapter, CMD_VERSION, timeout=3.0)
    except RuntimeError:
        return None
    if resp["status"] != PM3_SUCCESS or not resp["data"]:
        return None
    return parse_version_response(resp["data"])


def test_connection(port: str | None, baudrate: int) -> None:
    """Open port, PING, and close. Raises on failure."""
    resolved = port or find_proxmark_port()
    if not resolved:
        raise ConnectionError("Proxmark3 serial port not found")
    adapter: Proxmark3Adapter | None = None
    try:
        adapter = open_adapter(resolved, baudrate=baudrate)
    finally:
        close_adapter(adapter)
