# NA-LX128B: experimental read-only operation state

> **Status:** draft/experimental, based on one user's 2026-11-21
> (Asia/Taipei) live observation of a Panasonic NA-LX128B washer-dryer.
> Panasonic has **not** documented these interpretations. This feature
> deliberately supports **only exact model NA-LX128B**; do not generalize to
> other washer models or the stand-alone Type 6 dryer.

The new HA enum entity is **Washer-Dryer Operation State (Experimental)**,
unique-ID suffix `laundry_operation_state`. It **does not control the machine**,
change polling rates, or interfere with drying ETA logic.

## Evidence observed together with official Smart App

All times below are local Asia/Taipei times on **2026-11-21**.
`DeviceGetInfo` and account-level `UserGetDeviceStatus` are distinct APIs.

| App state | DeviceGetInfo `0x01` | DeviceGetInfo `0x50` | Overview `0x50` | Overview `0x54` | Evidence |
| --- | --- | --- | --- | --- | --- |
| Drying | `1` | `2` | `2` | `6` | Running sample, 12:34 and 12:40; `0x41/0x4F` held 111 and 105 minutes respectively |
| Spin during wash+dry / 脫水中 | `1` | `2` | `2` | `5` | 14:56:11 numeric same-poll overview, user checked App at 14:59 showing 脫水中 and estimated 16:57 finish; `0x41/0x4F` were empty strings; remote permission `0x74=0` |
| Anti-crease holding / 鬆柔保持中 | not recorded | not recorded | `2` | `11` | 13:24:25 after drying completed |
| Paused / 一時停止中 | not recorded | `7` in next status | `7` | `49601` | 13:30:33, after user pressed pause to take laundry out; `0x41/0x4F` were `0` |
| Powered off (app showed offline) | `2` | `7` (unchanged after pause) | previous `7` **cached** | previous `49601` **cached** | 13:36:40 new overview GET returned *all empty strings* |
| On, standby / 待機中 | `3` | `1` | `1` **fresh** | previous `49601` **cached** | 13:39:45; overview `0x54` was empty again |
| On, standby with remote control permission | `3` | `1` | `1` **fresh** | no valid value | 13:48:57; DeviceGetInfo `0x74` changed from `0` in ordinary standby to `1` after closing the door and enabling remote control; official App recognized permission |

The apparent discrepancy in the powered-off and first-standby rows is **intentional**.
`overview_status` holds per-code numeric values for up to 15 minutes; check
`overview_observed_at` plus `overview_last_response_summary` rather than
trusting its presence as a fresh reading.

**Unknown / not established:** `0x54=49601` could be a composite word
(0xC1C1), not a unique pause code. Do not use it to detect pause.
`0x50=7` was observed during pause, but DeviceGetInfo continued reporting it
after power-off. Consequently the **power code takes precedence**.

## First Home Assistant on-device check (2026-11-21 13:54:46 Asia/Taipei)

User deployed the experimental sensor and confirmed that Home Assistant displayed
**「洗衣機運轉狀態（實驗性）: 待機中」** while the official app/device was in
standby. Contemporaneous diagnostics: DeviceGetInfo `0x01=3`,
`0x50=1`, and `0x74=1` (remote-control permission remains enabled).
The latest overview `0x50=1` was numeric; `0x41`, `0x4F`,
`0x54`, and `0x20` were all empty strings. This validates the **standby
display path on the actual NA-LX128B** and confirms that the presence of
remote-control permission does not override standby in this implementation.
It does **not** yet validate live transitions into drying, anti-crease,
pause, or power-off with this new HA entity.

## Remote control permission is separate from operation state

Live observation at **13:48:57**: user closed the washer door and pressed the
machine's remote-control permission button. The official Smart App detected
remote control enabled. DeviceGetInfo reported `0x01=3`, `0x50=1`,
and **`0x74=1`**. The earlier ordinary-standby sample had
`0x01=3`, `0x50=1`, and `0x74=0`.

This confirms **`0x74` as a useful independent permission flag** in this
single transition, without changing the overall standby state. The existing
washer binary sensor `remote_control_allowed` already uses `0x74` and
interprets `0` as disabled and `1` as enabled. Do not duplicate it in
the new operation-state enum.

**User-confirmed physical behavior:** The remote-control button has no effect
while the washer door is open. Closing the door is a prerequisite to enabling
remote control; pressing the button after closing the door **locks the door**.
Consequently, `0x74=1` was observed during the locked-for-remote-control
condition. It is *not* proof that `0x74` directly exposes an independent
physical door-closed or lock sensor: a closed door may have remote permission
disabled, and other phases might lock the door for unrelated reasons. Keep
door-open, door-locked, and remote-control-permitted as distinct concepts
until separately observed.

The overview response still showed `0x50=1` with `0x54`, `0x41`,
`0x4F`, and `0x20` empty. This is consistent with standby and reinforces
that old cached `0x54=49601` must never become a live phase label.

## Conservative mapping for the new sensor

All model checks use exactly `NA-LX128B`.

1. If DeviceGetInfo `0x01=2`, report `off`, even if DeviceGetInfo
   `0x50` still says `7` (previous pause).
2. Otherwise DeviceGetInfo `0x50=1` → `standby`.
3. Otherwise DeviceGetInfo `0x50=7` → `paused`.
4. Otherwise DeviceGetInfo `0x50=2` → `running`, with conditional
   subdivision only if **both** account-level overview codes `0x50=2`
   and `0x54` were numeric on the **same latest successful response**:
   - `0x54=5` → `spinning` (one matching wash+dry sample)
   - `0x54=6` → `drying`
   - `0x54=11` → `soft_keeping`
5. Any unrecognized/missing DeviceGetInfo operation code → unknown
   (`None`), **not a guessed state**.

For step 4, the overview response must have been observed within 5 minutes.
If the codes are blank, have differing observation times, or only old cached
values remain, report **generic `running`**, not an obsolete drying stage.
HA entity attributes include the source raw codes, their timestamps and
`overview_phase_current` so users can check the basis of the interpretation.

This is a **display-only interpretation**, not proof of actual power draw,
network connectivity, or a vendor-supported machine state. The off/paused
transitions may still lag until DeviceGetInfo changes. The current HA
`available` flag can remain true when the official app calls a powered-off
washer 'offline'; do not equate the two concepts.

## Unresolved observations and suggested future tests

Tests to do during **normal usage**, not by interrupting a cycle solely for
reverse engineering:

- [ ] During ordinary use, disable remote-control permission **without
  starting a programme**, and verify that `0x74` returns to `0` while
  `0x01=3`, `0x50=1` remain in standby. If the app changes its display,
  note that as an independent confirmation.
- [ ] When convenient during normal use, confirm the separate
  states **door closed but remote control disabled** versus **remote enabled
  (door locked)**. A separate door-lock sensor requires independent evidence;
  do not use `0x74` as a door-contact signal.
- [ ] Capture `0x01` and both variants of `0x50` **at the moment of pause**
  during washing, not only after drying/anti-crease; confirm pause `7` and
  whether `0x01` changes.
- [ ] Repeat `0x54=5` during spin in a wash-only programme as well as
  wash+dry; check if rinse/spin transitions use a different code.
- [ ] Repeat `0x54=6` in a drying programme, and verify that a *wash-only*
  programme never uses it for an unrelated phase.
- [ ] Repeat `0x54=11` in anti-crease holding, ideally with `0x01`
  alongside it; determine whether this also appears in cooling or airflow.
- [ ] Verify `0x01=2` after power-off from a running or standby state,
  not only after a pause; verify `0x01=3` on a second power-on.
- [ ] Observe the meaning of DeviceGetInfo `0x50=3,4,5,8` for
  reservations, ordinary completion and errors **on this model**, before
  assigning textual labels in this new entity.
- [ ] Check what happens on cloudy/API errors, mismatched timing, and
  repeated empty responses when real appliance phases change between polls.
- [ ] Investigate the official App's remaining-time source separately:
  on the same observed cycle it showed a finish time while
  `UserGetDeviceStatus 0x41` and `0x4F` were repeatedly `""`.
  No change to the ETA sensor is implied by this PR.

The raw overview diagnostics and the independent `DeviceGetInfo` status
are retained unchanged for subsequent validation.
