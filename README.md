# Proxmark3 for Home Assistant

Home Assistant custom integration for [Proxmark3](https://github.com/RfidResearchGroup/proxmark3) (Iceman firmware). Polls for MIFARE / ISO14443-A tags and exposes UID, tag type, and optional block data as a sensor.

## Features

- Single Proxmark3 via USB serial (auto-detect)
- Configurable MIFARE Classic keys and blocks to read
- NDEF decoding (Ultralight/NTAG and optional MIFARE Classic) into a JSON `ntag` attribute
- Device info (MCU, firmware, memory) cached while offline
- Automatic reconnect when the device is unplugged
- Options flow to tune keys, blocks, and poll intervals without re-adding the integration
- **Write Magic tag UID** action — change the UID on writable MIFARE Classic Magic tags (block 0) from automations, with optional fixed UID or random generation

## Installation

### HACS

> **Note:** The integration is not in the [default HACS repository](https://github.com/hacs/integration) yet. Until it is included, add this repo as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) (category: **Integration**), then install **Proxmark3**.

### Manual

Copy `custom_components/proxmark3` into your Home Assistant `config/custom_components/` directory and restart Home Assistant.

## Requirements

- Proxmark3 with Iceman firmware ([RfidResearchGroup](https://github.com/RfidResearchGroup/proxmark3))
- USB access from the Home Assistant host (Docker: pass through `/dev/ttyACM0` or the device by-id path)
- Python packages `pyserial` and `proxmark3py` (installed automatically via manifest)

## Configuration

Setup is a short wizard (USB port is auto-detected):

1. **Reading** — NDEF options, raw MIFARE Classic blocks toggle, poll intervals
2. **Raw blocks** (only if enabled) — blocks 0–3 of MIFARE Classic sector 0
3. **MIFARE Classic keys** (only if raw blocks enabled) — Key A presets for block reads

Use **Options** later to change the same settings.

**UID only:** leave everything off on screen 1. The sensor state is always the tag UID; `tag_type` is still reported.

| Option | Tag types | Speed |
|--------|-----------|-------|
| Read NDEF (Ultralight / NTAG) | Type 2 tags | Fast |
| Read NDEF on MIFARE Classic (slow) | Phone-formatted MIFARE Classic MAD/NDEF | Slow (several sector reads) |
| Read raw blocks on MIFARE Classic | MIFARE Classic sector 0 blocks | Depends on enabled blocks + keys |

More enabled options increase read time on each new tag presentation.

### NDEF

- **Ultralight / NTAG:** enable **Read NDEF** — no keys required.
- **MIFARE Classic (phone NDEF):** enable **Read NDEF on MIFARE Classic (slow)** separately. Uses MAD key (`a0a1a2a3a4a5`) and NDEF sector keys automatically (same as `hf mf ndefread`).

#### Writing NDEF (phone is easiest)

For **Ultralight / NTAG** and **MIFARE Classic tags formatted as NDEF**, writing on a phone is usually the simplest option — no Proxmark3 required for programming.

1. Install a free NFC app, e.g. **[NFC Tools](https://www.wakdev.com/en/apps/nfc-tools.html)** (Android / iOS).
2. Choose **Write** → add a record:
   - **URL / URI** — links, `https://…`
   - **Text** — plain text with language code
   - **Custom** — other NDEF types supported by the app
3. Hold the tag to the phone and write.
4. In Home Assistant, enable **Read NDEF** (and **Read NDEF on MIFARE Classic** for MIFARE Classic tags), then verify the `ntag` attribute on `sensor.proxmark3_nfc_tag`.

NFC Tools (and similar apps) produce standard NDEF that this integration parses into JSON. MIFARE Classic tags are written as MAD/NDEF automatically by the phone OS or app.

**Example `ntag` values** (attribute is a JSON **string** containing an array):

```json
[{"tnf":"well_known","type":"U","uri":"https://example.com/room/kitchen"}]
```

```json
[{"tnf":"well_known","type":"T","language":"en","text":"Kitchen light"}]
```

Multiple records in one tag appear as several objects in the array.

### Keys

Keys appear on **screen 3** only when **Read raw blocks on MIFARE Classic** is enabled.

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

Keys are **not** used for UID detection, Ultralight/NTAG NDEF, or MIFARE Classic NDEF (those paths use built-in MAD/NDEF keys).

### Raw blocks (MIFARE Classic, 0–3)

Enable **Read raw blocks on MIFARE Classic** on screen 1, then pick blocks on screen 2.

The integration reads **blocks 0–3** (sector 0 on MIFARE Classic 1K). Block reading is **optional** — leave the toggle off for fastest UID-only polling.

| Block | MIFARE Classic 1K (sector 0) | Typical use in automations |
|-------|--------------------------------|----------------------------|
| **0** | Manufacturer data, UID/BCC (partly read-only) | Read-only for most users; **do not write** without knowing the format |
| **1** | User data | **Good default** — often used for custom bytes, counters, magic markers |
| **2** | User data | Second user data block in sector 0 |
| **3** | Sector trailer (Key A / access bits / Key B) | Advanced; contains keys and ACLs, not arbitrary user data |

**How to use in Home Assistant**

- Write a pattern to block 1 on the tag (see PM3 examples below), e.g. `A1B2C3D4…`.
- Enable **block 1** (and the correct key) in integration options.
- Trigger automations on `sensor.proxmark3_nfc_tag` state (UID) and/or `block_1` attribute.

**Tag types**

| Tag type | Raw blocks 0–3 | NDEF |
|----------|----------------|------|
| MIFARE Classic 1K / 4K / Mini | Yes — with keys | Optional (**MIFARE Classic NDEF**, slow) |
| MIFARE Ultralight / NTAG | No (MIFARE Classic-only toggle) | Optional (**Read NDEF**, fast) |
| MIFARE DESFire, Java cards, random ISO14443-A | **No** | **No** |

Disable unused blocks to speed up polling (each block is a separate read).

**Block 0** on MIFARE Classic holds manufacturer bytes and UID-related data (often only part of the block is writable). Writing garbage there can corrupt the tag identity or make it unreadable. On Ultralight/NTAG, page 0 is similarly reserved (UID, lock bits). Treat block/page 0 as **read-only** unless you know exactly what you are changing — or use the **`write_magic_uid`** action below for **writable Magic MIFARE Classic** tags.

**Block 3** on MIFARE Classic is the sector trailer (keys and access bits) — see warnings in the writing section below.

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

**Attributes:** `tag_type`, `block_0` … `block_3` (when raw MIFARE Classic blocks are enabled), `ntag` (JSON array of decoded NDEF records), plus cached `bootrom`, `compiler`, `fpga`, `memory` when known.

`ntag` is filled when **Read NDEF** and/or **Read NDEF on MIFARE Classic** matches the detected tag type.

Default entity id is usually `sensor.proxmark3_nfc_tag` (depends on your device name).

## Writing raw blocks (Proxmark3 client)

Use the Iceman **client** (`pm3` / `proxmark3`) when you need **raw hex bytes** in MIFARE Classic blocks — not for everyday NDEF (use a phone; see above). Stop Home Assistant polling or unload the integration while writing, so nothing else holds the serial port.

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

Do **not** write **block 0** or **block 3** on MIFARE Classic unless you understand the data layout. Block 0 carries manufacturer/UID-related data; block 3 is the sector trailer (keys and access conditions). A bad write to either can brick the tag or the whole sector. For **writable Magic MIFARE Classic** tags, prefer the **`write_magic_uid`** action instead of raw `hf mf wrbl` on block 0. On Ultralight/NTAG, avoid writing pages 0–2 for the same reason — use page 4+ for user data, or prefer **NDEF via NFC Tools** instead of raw page writes.

## Write Magic tag UID (action)

Changes the **UID** on a **writable Magic MIFARE Classic** tag by updating block 0 (UID bytes + BCC only). SAK, ATQA, and manufacturer data are **preserved** from the current tag. Works with common Magic tags (Gen1, Gen2, CUID, etc.) that allow block 0 writes — **not** with normal (non-Magic) Classic tags or Ultralight/NTAG.

| Item | Details |
|------|---------|
| Action | `proxmark3.write_magic_uid` |
| Target | None (single Proxmark3 instance) |
| Tag on reader | Required — place the Magic tag before calling |
| Optional `uid` | Hex string: **8** chars for 4-byte UID, **14** for 7-byte. Spaces/colons allowed. Random UID if omitted. |
| Response | `uid`, `block_0`, `tag_type` |
| Keys | Your integration keys plus common fallbacks (factory, MAD, NDEF, null) |

After a successful write, the **NFC Tag** sensor updates with the new UID.

**Random UID:**

```yaml
action: proxmark3.write_magic_uid
response_variable: tag
```

**Fixed UID:**

```yaml
action: proxmark3.write_magic_uid
data:
  uid: "76343115"
response_variable: tag
```

**Example — notify with the new UID:**

```yaml
alias: NFC rewrite Magic UID
triggers:
  - trigger: event
    event_type: rewrite_nfc_uid
actions:
  - action: proxmark3.write_magic_uid
    response_variable: tag
  - action: notify.persistent_notification
    data:
      title: NFC UID written
      message: "New UID: {{ tag.uid }}, type: {{ tag.tag_type }}"
```

**Errors** (shown as action failure): no tag, not MIFARE Classic, block 0 read/write failed (check keys / tag is Magic), verify failed.

Place the tag on the reader **before** calling the action. Polling pauses briefly while the write runs (serial lock).

## Example automations

Adjust `entity_id` / `notify.*` to match your setup.

### UID and MIFARE Classic block 1

Notify when a tag is placed on the reader (UID + block 1). Requires **Read raw blocks on MIFARE Classic** and block 1 enabled.

```yaml
alias: NFC Tag (UID + block)
description: ""
triggers:
  - trigger: state
    entity_id:
      - sensor.proxmark3_nfc_tag
conditions:
  - condition: not
    conditions:
      - condition: state
        entity_id: sensor.proxmark3_nfc_tag
        state:
          - unknown
          - unavailable
actions:
  - action: notify.persistent_notification
    data:
      title: NFC Tag
      message: >-
        Detected NFC tag, ID={{ states('sensor.proxmark3_nfc_tag') }},
        block_1={{ state_attr('sensor.proxmark3_nfc_tag', 'block_1') }}
mode: single
```

To react only to a specific payload in block 1, add a template condition on `state_attr('sensor.proxmark3_nfc_tag', 'block_1')`.

### NDEF (URL or text from `ntag` JSON)

Requires **Read NDEF** and/or **Read NDEF on MIFARE Classic**. The `ntag` attribute is a JSON string; parse it with the `from_json` filter.

**Open a URL from the first NDEF record** (e.g. tag written with NFC Tools → URL record):

```yaml
alias: NFC Tag NDEF URL
description: ""
triggers:
  - trigger: state
    entity_id: sensor.proxmark3_nfc_tag
    attribute: ntag
conditions:
  - condition: template
    value_template: >-
      {% set raw = state_attr('sensor.proxmark3_nfc_tag', 'ntag') %}
      {{ raw not in [none, ''] }}
actions:
  - variables:
      ndef_uri: >-
        {% set records = state_attr('sensor.proxmark3_nfc_tag', 'ntag') | from_json %}
        {{ records[0].uri | default('') }}
  - condition: template
    value_template: "{{ ndef_uri != '' }}"
  - action: notify.persistent_notification
    data:
      title: NFC tag URL
      message: "NDEF link: {{ ndef_uri }}"
mode: single
```

**Run different actions by NDEF text** (e.g. tag with text `Kitchen` / `Bedroom` from NFC Tools):

```yaml
alias: NFC Tag NDEF text room
description: ""
triggers:
  - trigger: state
    entity_id: sensor.proxmark3_nfc_tag
    attribute: ntag
conditions:
  - condition: template
    value_template: >-
      {% set raw = state_attr('sensor.proxmark3_nfc_tag', 'ntag') %}
      {% if raw in [none, ''] %}
        false
      {% else %}
        {% set records = raw | from_json %}
        {{ records | selectattr('text', 'defined') | list | length > 0 }}
      {% endif %}
actions:
  - choose:
      - conditions:
          - condition: template
            value_template: >-
              {% set records = state_attr('sensor.proxmark3_nfc_tag', 'ntag') | from_json %}
              {{ records[0].text | default('') == 'Kitchen' }}
        sequence:
          - action: light.turn_on
            target:
              entity_id: light.kitchen
      - conditions:
          - condition: template
            value_template: >-
              {% set records = state_attr('sensor.proxmark3_nfc_tag', 'ntag') | from_json %}
              {{ records[0].text | default('') == 'Bedroom' }}
        sequence:
          - action: light.turn_on
            target:
              entity_id: light.bedroom
mode: single
```

**Reusable template** — first URL or text from any record:

```jinja2
{% set raw = state_attr('sensor.proxmark3_nfc_tag', 'ntag') %}
{% if raw in [none, ''] %}
  none
{% else %}
  {% set records = raw | from_json %}
  {{ records[0].uri | default(records[0].text | default(none)) }}
{% endif %}
```


## License

GPLv3 License

## Support the Developer and the Project

* [GitHub Sponsors](https://github.com/sponsors/ClusterM)

* [Patreon](https://www.patreon.com/c/ClusterMeerkat)

* [Buy Me A Coffee](https://www.buymeacoffee.com/cluster)

* [Sber](https://messenger.online.sberbank.ru/sl/Lnb2OLE4JsyiEhQgC)

* [Donation Alerts](https://www.donationalerts.com/r/clustermeerkat)

* [Boosty](https://boosty.to/cluster)
