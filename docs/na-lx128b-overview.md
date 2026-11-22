# NA-LX128B: preliminary laundry overview support (read-only)

This branch adds `UserGetDeviceStatus` as a **second, read-only** data source for
Type 3 washer-dryer combinations. It does not replace the existing
`DeviceGetInfo` calls for other entities, or send any appliance control command.

## Observed values (Asia/Taipei, 2026-11-21)

| Time | Official Panasonic app ETA | 0x41 | 0x4F | 0x50 | 0x54 | 0x20 |
|------|---------------------------|------|------|------|------|------|
| 12:34 | 14:25 | 111 | 111 | 2 | 6 | 66 |
| 12:40 | 14:25 | 105 | 105 | 2 | 6 | 66 |

The App ETA difference and both 0x41/0x4F values happened to match
**exactly** in two consecutive drying samples. Later metadata specifically
names **0x41 = 洗衣殘時間** and **0x4F = nanoe殘時間**. That equality
must **not** be taken to mean 0x4F is a whole-programme ETA.

### 15:02:20 wash+dry spin sample: two time fields differ

The user later exported a full Home Assistant history CSV (UTC timestamps).
On 2026-11-21 at **15:02:20 Asia/Taipei**, during a wash+dry programme
with `0x54=5` (spin), the API reported `0x41=116`,
`0x4F=11`, `0x50=2`, `0x20=66`. The resulting HA
finish time was **16:58:19 Asia/Taipei**, close to the official App's
16:57 estimate checked at 14:59. HA next observed `0x54=6` (drying)
at **15:14:36**, about 12 minutes later.

### Discovery: cloud-provided LX128B command metadata

The user separately performed a **single read-only**
`GET /api/UserGetRegisteredGwList2` and supplied a filtered JSON export.
It matched **ModelType LX128B**, **DeviceType 3**, and contained a
12-item command definition with timestamp **202204180700**. This is
model-targeted cloud metadata, not a necessarily exhaustive or updated
description of every live code.

| Code | CommandName | Type / unit | Metadata |
| --- | --- | --- | --- |
| `0x41` | 洗衣殘時間 | range, 分 | 0–1440 |
| `0x4F` | nanoe殘時間 | range, 分 | 0–1440 |
| `0x50` | 運轉情報 | enum | 1 待機中, 2 動作中, 3/4 預約中, 5 終了 |
| `0x54` | 工程訊息 | enum | 1 預洗, 2 浸泡, 3 洗衣, 4 洗清, 5 脫水, 6 冷風乾燥, 9 冷卻, 11 延後晾衣 |
| `0x55` | 行程別訊息 | enum | 0 標準行程 and other programme values |
| `0x20` | 異常狀態 | range, 分 | 0–1440 (unit appears unusual; do not interpret as elapsed minutes without validation) |

This **overrules the previous speculation** that `0x4F=11`
represented 11 minutes remaining in the spin *phase*. Its similarity to
the later observed spin→dry transition was coincidental or otherwise
unexplained, not proof of phase timing. In contrast, `0x41=116` at
15:02 predicted ~16:58 and was close to the official App's ~16:57.
This supports interpreting `0x41` as programme remaining minutes,
while still requiring real-device confirmation for diverse cycles.

**Corrected ETA policy:** only `0x41` is eligible to produce a
whole-programme ETA. `0x4F` remains a diagnostic raw value but is
never a fallback, **including during drying**. A cached ETA derived
from `0x4F` is no longer carried forward. The schema lists a maximum
of 1440 minutes for `0x41`, replacing the previous arbitrary 599
minutes. No new API calls are required.

The 2022 metadata is not a perfect description of live 2026 operation:
for example, current diagnostics include **`0x50=7` during pause**,
which is absent from its enum. Furthermore the App shows **乾衣中**
for `0x54=6` despite the internal metadata label **冷風乾燥**,
and **鬆柔保持中** for `0x54=11` despite the internal label
**延後晾衣**. Keep user-facing labels grounded in live App observations
and don't infer active heating or physical airflow solely from names.

The cloud often returns **all blank** `Status` values or **partly blank**
responses. A real example had 0x41/0x4F/0x20 empty but 0x50=2 and 0x54=6.

## 2026-11-22 dual-API direct-read discovery and preferred source

A separate **read-only** experiment called both endpoints sequentially
for one NA-LX128B. At **08:17 Asia/Taipei**:

| Command | UserGetDeviceStatus | DeviceGetInfo |
| --- | --- | --- |
| `0x41` 洗衣殘時間 | `""` | **`"150"`** |
| `0x4F` nanoe殘時間 | `""` | **`"14"`** |
| `0x50` 運轉情報 | `""` | **`"2"`** |
| `0x54` 工程訊息 | `""` | **`"3"`** |
| `0x20` 異常狀態 | `""` | **`"66"`** |

Each DeviceGetInfo batch returned HTTP 200 and API `status=success`;
Overview also returned HTTP 200 but all five fields were empty.
The endpoint captures were seconds apart, not simultaneous. This
proves these codes can be read **directly** from DeviceGetInfo and
that Overview being empty must not erase a valid direct reading.
The `150`-minute value has **not** been compared with the App's ETA
in this exact capture; its interpretation as the full programme ETA
remains experimental despite the model's command metadata.

**Preferred-source policy for NA-LX128B, code by code:**

- Request `0x20`, `0x41`, `0x4F` and `0x54` from DeviceGetInfo
  in addition to its preexisting `0x50`.
- If a **fresh, successful, parseable** DeviceGetInfo value is present,
  use it, even if Overview disagrees.
- If that **particular code** is missing, empty, invalid, or from a
  SmartApp transient-failure cache bridge, fall back to a **fresh,
  numeric, latest-response** UserGetDeviceStatus value. Never treat
  an older cached Overview value as current.
- `0x41` alone may create the programme ETA. `0x4F` remains
  nanoe-related diagnostic data and is not interchangeable.
- A fresh, confirmed non-running `0x50` clears a previous ETA. If
  fresh direct `0x50` disagrees with Overview, direct takes priority.
- The experimental wash/rinse/spin/dry/anti-crease state uses fresh
  direct `0x54` first. Existing **pause versus powered-off**
  disambiguation still requires the independently validated Overview
  clue, since DeviceGetInfo `0x01=2`, `0x50=7` was observed
  in *both* situations.
- Only the experimental operation and ETA entities can remain
  available from a live Overview reading if direct per-device status
  is unavailable. Other entities retain their previous availability
  behavior. Raw `overview_*` diagnostic sensors still show their
  **own** endpoint's values, not the merged preferred state.
- DeviceGetInfo samples now carry a read timestamp and a
  `status_is_cached` marker, so the two-poll failure bridge cannot
  reset an ETA from stale minutes. No added timer or control command.

## HA entities

- Existing `drying_remaining_time` unique-ID suffix now reads
  an ETA derived **first from fresh DeviceGetInfo 0x41**, falling back to
  fresh overview 0x41. The legacy DeviceGetInfo 0x05 returned
  `-1683.3`. The existing entity ID remains unchanged.
- New `drying_expected_finish` timestamp entity.
- Five **diagnostic** raw numeric entities for 0x41, 0x4F, 0x50, 0x54, 0x20,
  separately identifying their `UserGetDeviceStatus` origin.

Each raw register retains its last valid value and observed timestamp for up to
15 minutes. Empty strings and invalid sentinels do not mean zero. The ETA
is preserved through short gaps, recalculated as time passes, and discarded
after 15 minutes without a valid minute reading. When a fresh 0x50 is not 2,
the ETA is cleared as a conservative first approximation.

The account-level GET normally runs no more than once every 3 minutes, only
while a Type 3 device is registered. During an actively running Type 3 cycle
(`DeviceGetInfo 0x50=2`) with **no fresh valid ETA from either source**,
the next normal HA coordinator poll may retry after a minimum of 1 minute. It does not issue
back-to-back requests, create a separate background loop, or override the
configured HA update interval. Once a fresh ETA is available, requests revert
to the 3-minute cadence. Failures do not interrupt ordinary HA entities.

## Troubleshooting missing ETA without extra cloud requests

The HA diagnostics download includes a sanitized
`overview_last_response_summary` for each Type 3 device. It is produced
during the **normal coordinator's overview fetch**, not by downloading
diagnostics itself. No private GWID or raw cloud response is retained.

- `overview_last_fetch_result`: `success` or `error` for the most recent
  attempted GET.
- `overview_last_response_at`: timestamp of the most recent successful GET.
- `overview_last_response_summary.result`: `matched`,
  `gateway_not_returned`, `invalid_command_list`, etc.
- `overview_last_response_summary.commands`: separate classifications for
  each of `0x41`, `0x4F`, `0x50`, `0x54`, `0x20`:
  `numeric`, `empty_string`, `not_returned`,
  `status_field_missing`, `invalid_value`.

Values in `overview_status` may be **cached from a previous valid response**;
the summary only classifies the **latest successfully fetched payload**.
Consult `overview_observed_at` to tell cached values apart from fresh values.
A failed or skipped request preserves the previous response summary and
timestamp, rather than claiming that its commands were absent.

## Remaining investigation

- Verify `0x41` remains the right **programme** ETA across wash/dry
  scenarios; cloud metadata calls it 洗衣殘時間.
- Investigate `0x4F` **nanoe殘時間** independently, without using it
  as a whole-programme ETA.
- Cross-check cloud-defined `0x54=3` (洗衣), `4` (洗清), and `9`
  (冷卻) against the official App during normal future runs.
- Verify long-term behaviour of intermittent blank statuses and stale data.
- Verify end-of-cycle/idle and revised ETA behaviour on a real NA-LX128B.
- The `debug/na-lx128b-overview-probe` branch separately retains the
  temporary diagnostic probes; they are not required for this feature.
