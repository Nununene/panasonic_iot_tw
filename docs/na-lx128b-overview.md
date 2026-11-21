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

- Validate what 0x41, 0x4F, 0x54 and 0x20 mean in non-drying phases; do not
  assume they have identical semantics for all washer programs.
- Verify long-term behaviour of intermittent blank statuses and stale data.
- Verify end-of-cycle/idle and revised ETA behaviour on a real NA-LX128B.
- The `debug/na-lx128b-overview-probe` branch separately retains the
  temporary diagnostic probes; they are not required for this feature.
