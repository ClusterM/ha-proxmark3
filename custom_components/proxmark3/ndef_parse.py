"""Minimal NDEF parser for NTAG / Ultralight user memory."""

from __future__ import annotations

from typing import Any

URI_PREFIXES: tuple[str, ...] = (
    "",
    "http://www.",
    "https://www.",
    "http://",
    "https://",
    "tel:",
    "mailto:",
    "ftp://anonymous:anonymous@",
    "ftp://ftp.",
    "ftps://",
    "sftp://",
    "smb://",
    "nfs://",
    "ftp://",
    "dav://",
    "news:",
    "telnet://",
    "imap:",
    "rtsp://",
    "urn:",
    "pop:",
    "sip:",
    "sips:",
    "tftp:",
    "btspp://",
    "btl2cap://",
    "btgoep://",
    "tcpobex://",
    "irdaobex://",
    "file://",
    "urn:epc:id:",
    "urn:epc:tag:",
    "urn:epc:pat:",
    "urn:epc:raw:",
    "urn:epc:",
    "urn:nfc:",
)

TNF_NAMES: dict[int, str] = {
    0: "empty",
    1: "well_known",
    2: "mime",
    3: "absolute_uri",
    4: "external",
    5: "unknown",
    6: "unchanged",
}


def cc_max_ndef_bytes(cc: bytes) -> int:
    """Return NDEF area size from the capability container (page 3)."""
    if len(cc) < 3 or cc[0] not in (0xE1, 0xF1):
        return 0
    size_map = {0x06: 48, 0x12: 144, 0x3E: 496, 0x6D: 872}
    return size_map.get(cc[2], 0)


def _tlv_length(data: bytes, index: int) -> tuple[int, int]:
    if index >= len(data):
        return 0, index
    if data[index] == 0xFF:
        if index + 2 >= len(data):
            return 0, index
        return (data[index + 1] << 8) | data[index + 2], index + 3
    return data[index], index + 1


def _extract_ndef_message(user_area: bytes) -> bytes | None:
    """Return raw NDEF message bytes from Type 2 tag user area."""
    if not user_area:
        return None

    # Some dumps store records directly without TLV wrapper.
    header = user_area[0]
    if header & 0x80:
        return user_area

    index = 0
    while index < len(user_area):
        tag = user_area[index]
        index += 1
        if tag == 0x00:
            length, index = _tlv_length(user_area, index)
            index += length
            continue
        if tag == 0x03:
            length, index = _tlv_length(user_area, index)
            if length == 0:
                return b""
            end = index + length
            if end > len(user_area):
                return None
            return user_area[index:end]
        if tag in (0x01, 0x02, 0xFD):
            length, index = _tlv_length(user_area, index)
            index += length
            continue
        if tag == 0xFE:
            break
        index += 1
    return None


def _decode_header(data: bytes, offset: int) -> tuple[dict[str, Any], int] | None:
    if offset >= len(data):
        return None

    flags = data[offset]
    type_len = data[offset + 1]
    short_record = bool(flags & 0x10)
    id_present = bool(flags & 0x08)
    header_len = 3 + (1 if short_record else 4) + (1 if id_present else 0)
    if offset + header_len > len(data):
        return None

    if short_record:
        payload_len = data[offset + 2]
        type_start = offset + 3
    else:
        payload_len = int.from_bytes(data[offset + 2 : offset + 6], "big")
        type_start = offset + 6

    id_len = 0
    if id_present:
        id_len = data[type_start]
        type_start += 1

    type_end = type_start + type_len
    id_end = type_end + id_len
    payload_end = id_end + payload_len
    if payload_end > len(data):
        return None

    record = {
        "mb": bool(flags & 0x80),
        "me": bool(flags & 0x40),
        "tnf": TNF_NAMES.get(flags & 0x07, "reserved"),
        "type": data[type_start:type_end].decode("utf-8", errors="replace"),
        "payload": data[id_end:payload_end],
    }
    if id_len:
        record["id"] = data[type_end:id_end].hex().upper()

    return record, payload_end - offset


def _decode_well_known(record: dict[str, Any]) -> dict[str, Any]:
    payload = record["payload"]
    rec_type = record["type"]
    out: dict[str, Any] = {
        "tnf": record["tnf"],
        "type": rec_type,
    }

    if rec_type == "U" and payload:
        prefix_id = payload[0]
        prefix = URI_PREFIXES[prefix_id] if prefix_id < len(URI_PREFIXES) else ""
        out["uri"] = prefix + payload[1:].decode("utf-8", errors="replace")
        return out

    if rec_type == "T" and payload:
        status = payload[0]
        lang_len = status & 0x3F
        lang = payload[1 : 1 + lang_len].decode("utf-8", errors="replace")
        text = payload[1 + lang_len :].decode("utf-8" if status >> 7 == 0 else "utf-16-be", errors="replace")
        out["language"] = lang
        out["text"] = text
        return out

    if rec_type == "Sp" and payload:
        out["records"] = parse_ndef_records(payload)
        return out

    out["payload_hex"] = payload.hex().upper()
    return out


def _decode_record(record: dict[str, Any]) -> dict[str, Any]:
    tnf = record["tnf"]
    payload = record["payload"]
    rec_type = record.get("type", "")

    if tnf == "empty":
        return {"tnf": "empty"}

    if tnf == "well_known":
        return _decode_well_known(record)

    if tnf == "mime":
        return {
            "tnf": "mime",
            "type": rec_type,
            "payload_text": payload.decode("utf-8", errors="replace"),
            "payload_hex": payload.hex().upper(),
        }

    if tnf == "absolute_uri":
        uri = rec_type.encode("utf-8") + payload
        return {"tnf": "absolute_uri", "uri": uri.decode("utf-8", errors="replace")}

    if tnf == "external":
        return {
            "tnf": "external",
            "type": rec_type,
            "payload_hex": payload.hex().upper(),
        }

    return {
        "tnf": tnf,
        "type": rec_type,
        "payload_hex": payload.hex().upper(),
    }


def parse_ndef_records(message: bytes) -> list[dict[str, Any]]:
    """Parse a raw NDEF message into decoded record dicts."""
    records: list[dict[str, Any]] = []
    offset = 0
    while offset < len(message):
        parsed = _decode_header(message, offset)
        if parsed is None:
            break
        header, record_len = parsed
        records.append(_decode_record(header))
        offset += record_len
        if header.get("me"):
            break
    return records


def parse_ntag_user_area(user_area: bytes) -> list[dict[str, Any]] | None:
    """Parse NDEF records from Ultralight/NTAG user memory (from page 4)."""
    message = _extract_ndef_message(user_area)
    if message is None:
        return None
    if not message:
        return []
    return parse_ndef_records(message)


def parse_ndef_buffer(data: bytes) -> list[dict[str, Any]] | None:
    """Parse NDEF from MFC MAD payload or Type 2 user area."""
    if not data:
        return []

    records: list[dict[str, Any]] = []
    index = 0
    found_tlv = False

    while index < len(data):
        tag = data[index]
        if tag == 0x00:
            index += 1
            length, index = _tlv_length(data, index)
            index += length
            continue
        if tag == 0x03:
            found_tlv = True
            index += 1
            length, index = _tlv_length(data, index)
            if length and index + length <= len(data):
                records.extend(parse_ndef_records(data[index : index + length]))
            index += length
            continue
        if tag in (0x01, 0x02, 0xFD):
            index += 1
            length, index = _tlv_length(data, index)
            index += length
            continue
        if tag == 0xFE:
            break
        break

    if found_tlv:
        return records

    if data[0] & 0x80:
        return parse_ndef_records(data)

    return parse_ntag_user_area(data)
