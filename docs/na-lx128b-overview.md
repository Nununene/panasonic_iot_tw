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

The account-level GET is attempted no more than once per 3 minutes, only while
a Type 3 device is registered. Failures do not interrupt ordinary HA entities.

## Remaining investigation

- Validate what 0x41, 0x4F, 0x54 and 0x20 mean in non-drying phases; do not
  assume they have identical semantics for all washer programs.
- Verify long-term behaviour of intermittent blank statuses and stale data.
- Verify end-of-cycle/idle and revised ETA behaviour on a real NA-LX128B.
- The `debug/na-lx128b-overview-probe` branch separately retains the
  temporary diagnostic probes; they are not required for this feature.
