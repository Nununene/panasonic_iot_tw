# NA-LX128B: preliminary laundry overview support (read-only)

This branch adds `UserGetDeviceStatus` as a **second, read-only** data source for
Type 3 washer-dryer combinations. It does not replace the existing
`DeviceGetInfo` calls for other entities, or send any appliance control command.

## Observed values (Asia/Taipei, 2026-11-21)

| Time | Official Panasonic app ETA | 0x41 | 0x4F | 0x50 | 0x54 | 0x20 |
|------|---------------------------|------|------|------|------|------|
| 12:34 | 14:25 | 111 | 111 | 2 | 6 | 66 |
| 12:40 | 14:25 | 105 | 105 | 2 | 6 | 66 |

The app ETA difference and both 0x41/0x4F values match **exactly** in two
consecutive samples during drying. This is evidence, not vendor documentation;
the roles of 0x41 vs 0x4F outside drying remain unknown.

### 15:02:20 wash+dry spin sample: two time fields differ

The user later exported a full Home Assistant history CSV (UTC timestamps).
On 2026-11-21 at **15:02:20 Asia/Taipei**, during a wash+dry programme
with `0x54=5` (spin), the API reported `0x41=116`,
`0x4F=11`, `0x50=2`, `0x20=66`. The resulting HA
finish time was **16:58:19 Asia/Taipei**, close to the official App's
16:57 estimate checked at 14:59. HA next observed `0x54=6` (drying)
at **15:14:36**, about 12 minutes later.

This suggests, but does **not yet prove**, that **0x41 tracks
whole-programme remaining time** and **0x4F may track current-phase
remaining time**. The two were equal in the earlier drying samples
because the active dryer phase may have lasted until programme completion.

**Safe fallback change on the NA-LX128B operation-state draft branch:**
when `0x41` is blank, allow `0x4F` to become a programme ETA only
when the same new overview response contains `0x50=2` and
`0x54=6` (confirmed drying). Otherwise leave ETA unknown rather than
misinterpret the spin-phase 11 minutes as 11 minutes until wash+dry
completion. The regular `0x41` path remains unchanged.

The cloud often returns **all blank** `Status` values or **partly blank**
responses. A real example had 0x41/0x4F/0x20 empty but 0x50=2 and 0x54=6.

## HA entities

- Existing `drying_remaining_time` unique-ID suffix now reads from the
  overview-derived ETA rather than DeviceGetInfo 0x05 (which returned
  `-1683.3` in real tests). No new duplicate for this existing entity.
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
(`DeviceGetInfo 0x50=2`) with **no fresh overview ETA**, the next normal HA
coordinator poll may retry after a minimum of 1 minute. It does not issue
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

- Validate the apparent difference between total-programme `0x41`
  and possibly current-phase `0x4F` in wash/rinse/spin/drying; do not
  assume identical meanings across all washer programmes.
- Validate `0x54=3` and `0x54=4` against the official App in the
  same session; the HA history CSV shows these phases before spin `5`,
  but lacks contemporaneous App labels.
- Verify long-term behaviour of intermittent blank statuses and stale data.
- Verify end-of-cycle/idle and revised ETA behaviour on a real NA-LX128B.
- The `debug/na-lx128b-overview-probe` branch separately retains the
  temporary diagnostic probes; they are not required for this feature.
