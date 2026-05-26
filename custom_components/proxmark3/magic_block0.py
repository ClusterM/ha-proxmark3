"""MIFARE Classic block 0 helpers for Magic (writable UID) tags."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

ISO14443A_CASCADE_TAG = 0x88


@dataclass(frozen=True)
class Block0Plan:
    """Planned block 0 write derived from a UID."""

    uid_len: int
    block: bytes
    uid: bytes


def mifare_bcc(data: bytes) -> int:
    """Block Check Character: XOR of all bytes (Proxmark3 `analyse lrc`)."""
    value = 0
    for byte in data:
        value ^= byte
    return value & 0xFF


def mifare_bcc4(uid4: bytes) -> int:
    if len(uid4) != 4:
        raise ValueError("UID must be exactly 4 bytes")
    return mifare_bcc(uid4)


def mifare_bcc1_7(uid7: bytes) -> int:
    """Cascade-2 BCC for a 7-byte UID (UID3..UID6)."""
    if len(uid7) != 7:
        raise ValueError("UID must be exactly 7 bytes")
    return mifare_bcc(uid7[3:7])


def validate_block0(block: bytes, uid_len: int) -> bool:
    """Return True when BCC fields match the UID in block 0."""
    if len(block) != 16:
        return False
    if uid_len == 4:
        uid = block[0:4]
        return block[4] == mifare_bcc4(uid)
    if uid_len == 7:
        uid = block[0:7]
        return block[8] == mifare_bcc1_7(uid)
    return False


def parse_uid_hex(value: str) -> bytes:
    """Parse a hex UID string (optional spaces or colons)."""
    cleaned = value.replace(" ", "").replace(":", "").strip()
    if not cleaned:
        raise ValueError("UID is empty")
    if len(cleaned) % 2:
        raise ValueError("UID hex length must be even")
    try:
        return bytes.fromhex(cleaned)
    except ValueError as exc:
        raise ValueError("UID must be hexadecimal") from exc


def build_block0_from_uid(
    current: bytes,
    uid: bytes | None = None,
    *,
    uid_len: int,
) -> Block0Plan:
    """Build a valid manufacturer block for the given or random UID."""
    if len(current) != 16:
        raise ValueError("Current block 0 must be 16 bytes")
    if uid_len not in (4, 7):
        raise ValueError("uid_len must be 4 or 7")

    if uid is None:
        uid = secrets.token_bytes(uid_len)
    elif len(uid) != uid_len:
        raise ValueError(f"UID must be exactly {uid_len} bytes")

    block = bytearray(current)
    if uid_len == 4:
        block[0:4] = uid
        block[4] = mifare_bcc4(uid)
    else:
        block[0:7] = uid
        block[8] = mifare_bcc1_7(uid)

    result = bytes(block)
    if not validate_block0(result, uid_len):
        raise ValueError("Generated block 0 failed local BCC validation")

    return Block0Plan(uid_len=uid_len, block=result, uid=uid)
