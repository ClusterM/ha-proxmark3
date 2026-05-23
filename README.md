# Proxmark3 for Home Assistant

Home Assistant custom integration for [Proxmark3](https://github.com/RfidResearchGroup/proxmark3) (Iceman firmware). Polls for MIFARE / ISO14443-A tags and exposes UID, tag type, and optional block data as a sensor.

## Features

- Single Proxmark3 via USB serial (auto-detect)
- Configurable MIFARE Classic keys and blocks to read
- Device info (MCU, firmware, memory) cached while offline
- Automatic reconnect when the device is unplugged
- Options flow to tune keys, blocks, and poll intervals without re-adding the integration

## Installation

### HACS

> **Note:** Inclusion in the [default HACS repository](https://github.com/hacs/integration) is expected but not available yet. Until then, add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) (category: **Integration**), then install **Proxmark3**.

### Manual

Copy `custom_components/proxmark3` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Requirements

- Proxmark3 with Iceman firmware ([RfidResearchGroup](https://github.com/RfidResearchGroup/proxmark3))
- USB access from the Home Assistant host (Docker: pass through `/dev/ttyACM0` or the device by-id path)
- Python packages `pyserial` and `proxmark3py` (installed automatically via manifest)

## Configuration

1. **Settings → Devices & services → Add integration → Proxmark3**
2. Choose keys, blocks, and poll intervals (USB port is auto-detected)
3. Use **Options** later to change keys, blocks, and intervals

More enabled keys and blocks increase read time on each new tag presentation.

### Keys

The integration reads **MIFARE Classic** blocks with **Key A**, trying keys **in the order** they appear in settings. It stops at the first key that works.

| Preset | Hex | When to enable |
|--------|-----|----------------|
| Factory default | `ffffffffffff` | **Most blank or retail tags.** Default choice; enable this first. |
| MAD key A | `a0a1a2a3a4a5` | Multi-application MAD sectors, some transport cards. |
| NDEF | `d3f7d3f7d3f7` | NTAG / NDEF-formatted tags (often used with MAD). |
| Null key | `000000000000` | Tags explicitly programmed with an all-zero key. |
| Custom | 12 hex chars | Your own key; appended **after** the checked presets. |

**Tips**

- Start with **factory default only** for fastest reads.
- Add more presets only if reads fail (`block_*` attributes are empty or missing while UID is present).
- Put the most likely key first (order in the UI = try order).
- Custom key is useful when you already know the sector key from `hf mf chk` / `hf mf autopwn` in the PM3 client.

Keys are **not** used for MIFARE Ultralight / NTAG (see blocks below); those tag types use a plain read without authentication.

### Blocks (0–3)

The integration can read **blocks 0–3** (the first sector on MIFARE Classic 1K, or pages 0–3 on Ultralight/NTAG).

| Block | MIFARE Classic 1K (sector 0) | Typical use in automations |
|-------|--------------------------------|----------------------------|
| **0** | Manufacturer data, UID/BCC (partly read-only) | Read-only for most users; **do not write** without knowing the format |
| **1** | User data | **Good default** — often used for custom bytes, counters, magic markers |
| **2** | User data | Second user data block in sector 0 |
| **3** | Sector trailer (Key A / access bits / Key B) | Advanced; contains keys and ACLs, not arbitrary user data |

**How to use in Home Assistant**

- Write a pattern to block 1 on the tag (see PM3 examples below), e.g. `A1B2C3D4…`.
- Enable **block 1** (and the correct key) in integration options.
- Trigger automations on `sensor.proxmark3_mf_tag` state (UID) and/or `block_1` attribute.

**Tag types**

| Tag type | Blocks 0–3 in this integration |
|----------|--------------------------------|
| MIFARE Classic 1K / 4K / Mini | Yes — Classic read with keys |
| MIFARE Ultralight / NTAG | Yes — read as Ultralight pages (no key) |
| MIFARE DESFire, Java cards, random ISO14443-A | **No** — UID/type may appear, but Classic blocks are not applicable |

Disable unused blocks to speed up polling (each block is a separate read).

**Block 0** on MIFARE Classic holds manufacturer bytes and UID-related data (often only part of the block is writable). Writing garbage there can corrupt the tag identity or make it unreadable. On Ultralight/NTAG, page 0 is similarly reserved (UID, lock bits). Treat block/page 0 as **read-only** unless you know exactly what you are changing.

**Block 3** on Classic is the sector trailer (keys and access bits) — see warnings in the writing section below.

### Intervals

| Option | Default | Description |
|--------|---------|-------------|
| Poll interval | 0.05 s | Delay when idle (no tag / tag held). Lower = more responsive, more USB traffic. |
| Reconnect interval | 5 s | Delay between attempts when the device is unplugged or the port is missing. |

## Sensor

| State | Description |
|-------|-------------|
| UID hex string | Tag on the reader |
| `unknown` / empty | No tag present |
| unavailable | Proxmark3 disconnected (device info still cached on the device page) |

**Attributes:** `tag_type`, `block_0` … `block_3` (hex, enabled blocks only), plus cached `bootrom`, `compiler`, `fpga`, `memory` when known.

Default entity id is usually `sensor.proxmark3_mf_tag` (depends on your device name).

## Writing blocks with the PM3 client

Examples use the Iceman **client** (`pm3` / `proxmark3`) on the same machine. Stop Home Assistant polling or unload the integration while writing, so nothing else holds the serial port.

**Read block 1 (verify):**

```text
hf mf rdbl --blk 1 -k FFFFFFFFFFFF
```

**Write 16 bytes to block 1** (factory key, sector 0):

```text
hf mf wrbl --blk 1 -k FFFFFFFFFFFF -d 0102030405060708090a0b0c0d0e0f
```

**Write ASCII marker** (e.g. `HA` + padding to 16 bytes):

```text
hf mf wrbl --blk 1 -k FFFFFFFFFFFF -d 4841FFFFFFFFFFFFFFFFFFFFFFFFFFFF
```

**Ultralight / NTAG page 4** (user area often starts at page 4; pages 0–3 are reserved — integration still reads 0–3 if enabled):

```text
hf mf wrbl --blk 4 -d 0102030405060708090a0b0c0d0e0f
```

Do **not** write **block 0** or **block 3** on Classic unless you understand the data layout. Block 0 carries manufacturer/UID-related data; block 3 is the sector trailer (keys and access conditions). A bad write to either can brick the tag or the whole sector. On Ultralight/NTAG, avoid writing pages 0–2 for the same reason — use page 4+ for user data.

## Example automation

Notify when a tag is placed on the reader (UID + block 1). Adjust `entity_id` to match your entity.

```yaml
alias: MF Tag
description: ""
triggers:
  - trigger: state
    entity_id:
      - sensor.proxmark3_mf_tag
conditions:
  - condition: not
    conditions:
      - condition: state
        entity_id: sensor.proxmark3_mf_tag
        state:
          - unknown
          - unavailable
actions:
  - action: notify.persistent_notification
    data:
      title: MF Tag
      message: >-
        Detected MF tag, ID={{ states('sensor.proxmark3_mf_tag') }},
        block_1={{ state_attr('sensor.proxmark3_mf_tag', 'block_1') }}
mode: single
```

To react only to a specific payload in block 1, add a template condition on `state_attr('sensor.proxmark3_mf_tag', 'block_1')`.


## License

GPLv3 License

## Support the Developer and the Project

* [GitHub Sponsors](https://github.com/sponsors/ClusterM)

* [Patreon](https://www.patreon.com/c/ClusterMeerkat)

* [Buy Me A Coffee](https://www.buymeacoffee.com/cluster)

* [Sber](https://messenger.online.sberbank.ru/sl/Lnb2OLE4JsyiEhQgC)

* [Donation Alerts](https://www.donationalerts.com/r/clustermeerkat)

* [Boosty](https://boosty.to/cluster)
