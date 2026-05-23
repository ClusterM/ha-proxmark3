"""MIFARE Application Directory (MAD) helpers for Classic NDEF tags."""

from __future__ import annotations

MFBLOCK_SIZE = 16
MF_MAD1_SECTOR = 0
MF_MAD2_SECTOR = 16
NDEF_MFC_AID = 0xE103

MAD_KEY = bytes.fromhex("a0a1a2a3a4a5")
NDEF_KEY = bytes.fromhex("d3f7d3f7d3f7")


def crc8_mad(data: bytes) -> int:
    """CRC-8/MIFARE-MAD (poly=0x1d, init=0xc7)."""
    crc = 0xC7
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x1D) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _mad_crc_check(sector: bytes, mad_ver: int) -> bool:
    if mad_ver == 1:
        expected = sector[16]
        computed = crc8_mad(sector[17 : 16 + 1 + 15 + 16])
        return computed == expected
    expected = sector[0]
    computed = crc8_mad(sector[1 : 1 + 15 + 16 + 16])
    return computed == expected


def _mad_get_aid(sector: bytes, mad_ver: int, sector_no: int) -> int:
    if mad_ver == 1:
        base = 16 + 2 + (sector_no - 1) * 2
    else:
        base = 2 + (sector_no - 1) * 2
    return (sector[base + 1] << 8) | sector[base]


def mad_check(sector0: bytes, sector16: bytes | None) -> tuple[bool, bool]:
    """Return (valid, have_mad2). Matches Proxmark3 MADCheck (non-verbose)."""
    if len(sector0) < MFBLOCK_SIZE * 4:
        return False, False

    gpb = sector0[(3 * MFBLOCK_SIZE) + 9]
    if (gpb & 0x80) == 0:
        return False, False

    mad_ver = gpb & 0x03
    if mad_ver not in (0x01, 0x02):
        return False, False

    have_mad2 = mad_ver == 2
    ok = _mad_crc_check(sector0, 1)
    if have_mad2 and sector16 is not None and len(sector16) >= MFBLOCK_SIZE * 4:
        ok = ok and _mad_crc_check(sector16, 2)
    elif have_mad2:
        have_mad2 = False
    return ok, have_mad2


def mad_decode(
    sector0: bytes,
    sector16: bytes | None,
    *,
    override: bool = False,
) -> list[int] | None:
    """Decode MAD AIDs. Returns None when MAD is invalid and override is False."""
    valid, have_mad2 = mad_check(sector0, sector16)
    if not valid and not override:
        return None

    aids: list[int] = []
    for i in range(1, 17):
        aids.append(_mad_get_aid(sector0, 1, i))

    if have_mad2 and sector16 is not None and len(sector16) >= MFBLOCK_SIZE * 4:
        aids.append(0x0005)
        for i in range(1, 24):
            aids.append(_mad_get_aid(sector16, 2, i))
    return aids
