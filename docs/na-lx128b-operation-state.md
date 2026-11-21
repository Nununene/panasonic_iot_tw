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
| Drying after spin in same wash+dry cycle / 乾衣中 | `1` | `2` | `2` | `6` | 15:23:47 overview, App at 15:24 said 乾衣中 with finish estimate 16:57 (unchanged since spinning at 14:59); same-poll `0x50` and `0x54` numeric; `0x41/0x4F` empty |
| Anti-crease holding / 鬆柔保持中 | not recorded | not recorded | `2` | `11` | 13:24:25 after drying completed |
| Paused / 一時停止中 | `2` confirmed on later sample | `7` | `7` **fresh** | `49601` in first pause sample only | At 13:30:33, 0x41/0x4F were 0; at **16:28:13** after pause from anti-crease, App read 一時停止中, DeviceGetInfo 0x01=2, 0x50=7 and latest overview 0x50=7 numeric, but 0x54=11 was cached from 16:25:09 |
| Powered off (app showed offline) | `2` | `7` (unchanged after pause) | previous `7` **cached** | previous `49601` **cached** | 13:36:40 new overview GET returned *all empty strings* |
| On, standby / 待機中 | `3` | `1` | `1` **fresh** | previous `49601` **cached** | 13:39:45; overview `0x54` was empty again |
| On, standby with remote control permission | `3` | `1` | `1` **fresh** | no valid value | 13:48:57; DeviceGetInfo `0x74` changed from `0` in ordinary standby to `1` after closing the door and enabling remote control; official App recognized permission |

The apparent discrepancy in the powered-off and first-standby rows is **intentional**.
`overview_status` holds per-code numeric values for up to 15 minutes; check
`overview_observed_at` plus `overview_last_response_summary` rather than
trusting its presence as a fresh reading.

**Unknown / not established:** `0x54=49601` could be a composite word
(0xC1C1), not a unique pause code. Do not use it to detect pause.
`0x50=7` appears in DeviceGetInfo both **during pause and after power-off**.
`0x01=2` ALSO occurs in both states; contrary to the first interpretation,
it is **not a unique power-off signal**. The difference in these two observed
samples is whether the **latest** account overview independently returned a
fresh numeric `0x50=7` (pause) or an `empty_string` (after power-off).

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

1. If DeviceGetInfo `0x01=2` **and** `0x50=7`, disambiguate using the
   latest matched overview GET, only if it is within five minutes:
   - New numeric `overview 0x50=7`, observed at precisely the same response
     timestamp → `paused`.
   - `overview 0x50` is `empty_string` → tentative `off`.
   - No recent matched response, stale cache, errors, unrecognized codes
     → unknown, **not automatically `off`**.
2. For other combinations with `0x01=2`, return unknown until separately
   observed.
3. Otherwise DeviceGetInfo `0x50=1` → `standby`.
4. Otherwise DeviceGetInfo `0x50=7` → `paused`.
5. Otherwise DeviceGetInfo `0x50=2` → `running`, with conditional
   subdivision only if **both** account-level overview codes `0x50=2`
   and `0x54` were numeric on the **same latest successful response**:
   - `0x54=5` → `spinning` (one matching wash+dry sample)
   - `0x54=6` → `drying`
   - `0x54=11` → `soft_keeping`
6. Any unrecognized/missing DeviceGetInfo operation code → unknown
   (`None`), **not a guessed state**.

For step 4, the overview response must have been observed within 5 minutes.
If the codes are blank, have differing observation times, or only old cached
values remain, report **generic `running`**, not an obsolete drying stage.
HA entity attributes include the source raw codes, their timestamps and
`overview_phase_current` so users can check the basis of the interpretation.

This is a **display-only interpretation**, not proof of actual power draw,
network connectivity, or a vendor-supported machine state. The off/paused
transitions may lag or briefly show unknown between polling. Because the
cloud can return empty codes transiently even during operation, interpreting
a blank latest overview as `off` is still a **provisional heuristic** and
needs more live validation. The current HA
`available` flag can remain true when the official app calls a powered-off
washer 'offline'; do not equate the two concepts.

## Same-cycle spin → drying transition (2026-11-21)

The user observed the **same wash+dry programme** in the official Smart App
at 14:59 (spinning) and 15:24 (drying). In the corresponding account overview
samples, freshly read `0x54` changed from **5 → 6**, with `0x50=2` in
both. The later sample was read at **15:23:47 Asia/Taipei**. Both 0x50 and
0x54 carried numeric `Status` strings from the same API GET in each reading.
This provides within-cycle support for the tentative stage mapping, without
relying on a lingering cache.

The App continued to predict a **16:57** finish, while `0x41` and `0x4F`
were `empty_string` in the 15:24 overview read. The alternate App ETA source
remains unknown. The existing operation-state classifier already maps fresh
`0x54=6` to `drying`; no additional code change is needed for that phase.

## First Home Assistant live drying-phase validation (2026-11-21)

During the ongoing wash+dry cycle, the user confirmed that the **deployed
experimental HA entity displayed `乾衣中`** while the official Panasonic
App also displayed drying. The accompanying 15:23:47 Asia/Taipei overview
sample had `0x50=2` and `0x54=6`, both numeric in the same successful
response. This validates the full chain **official App → cloud response →
experimental HA state** for the `drying` label on this NA-LX128B.

This is separate from the earlier first live validation of `standby`.
The `spinning` label (for `0x54=5`) was added to the draft branch
after the user observed it, but its HA display has not yet been checked
on-device. Likewise `soft_keeping`, `paused`, and `off` still need
live validation with the new entity. The cloud-based drying remaining
time is independently unresolved.

## Drying ETA revision while cloud timing fields remain blank (2026-11-21)

The same wash+dry programme remained **乾衣中** in the official Panasonic
App at **15:56 Asia/Taipei**, but its predicted completion moved from
**16:57** (still shown at 15:24) to **16:21**, a **36-minute earlier**
estimate. At 15:56 this represents approximately 25 minutes remaining.

Diagnostics from the **15:54:27** account-level GET reported fresh
`overview 0x50=2`, `0x54=6` (both numeric and observed at the exact same
timestamp). `0x41`, `0x4F`, and `0x20` were each
`empty_string` in the most recent response. `overview_expected_finish_at`
and `overview_eta_observed_at` were null. DeviceGetInfo retained
`0x01=1`, `0x50=2`, and remote permission `0x74=0`.

This is a second recent sample of a **stable running dryer phase `0x54=6`**
and direct evidence that the two currently probed time codes cannot account
for the App's changing ETA during this cycle. **Do not conclude** that the
App necessarily uses another endpoint: other fields, separate requests,
server state, or local calculations remain possible. Do not synthesize a HA
ETA from the App's display without a reliable machine-readable source.

## Continued drying sample near estimated finish (2026-11-21 16:07)

At **16:07 Asia/Taipei** the official Panasonic App still showed
**乾衣中**, with expected finish **16:21** unchanged from 15:56. At
**16:06:46** the latest successful `UserGetDeviceStatus` reported both
`overview 0x50=2` and `overview 0x54=6` as numeric values from that
same response. `0x41`, `0x4F`, and `0x20` again returned empty
strings. DeviceGetInfo simultaneously showed `0x01=1`, `0x50=2`
and `0x74=0`.

This extends observed persistence of `0x54=6` during drying across
samples at **15:23:47 → 15:54:27 → 16:06:46** (about 43 minutes between
the first and latest). No distinct cooldown/airflow code has been
observed *yet*, but there may be shorter transitions between samples.
The App was forecasting roughly **14 minutes** until finish; this
cannot be inferred from `0x41/0x4F` in the received overview.

## Home Assistant state-history CSV: sequence of a full wash+dry programme

The user exported Home Assistant **history events**, which record entity
**state changes**, not every coordinator poll. The timestamps were UTC
(`Z`) and are converted to **Asia/Taipei (+08:00)** below. These times
indicate when HA recorded the transition, not necessarily its exact physical
onset. In this sample `0x50` stayed at `2` while **`0x54`** stepped
through `3 → 4 → 5 → 6`:

| HA history time, Taipei | Sensor / observed value | Interpretation |
| --- | --- | --- |
| 14:00:57 | overview `0x50=2`, `0x54=3` | Programme running; phase 3 **not yet identified in App** |
| 14:28:34 | overview `0x54=4` | Next phase; **not yet identified in App** |
| 14:50:02 | overview `0x54=5` | Supports spinning; App showed 脫水中 at 14:59 |
| 15:02:20 | `0x41=116`, `0x4F=11`, `0x20=66` | **Different time scopes likely** (see below) |
| 15:14:36 | overview `0x54=6`; experimental HA state `drying` | Supports drying; App confirmed at 15:24 |
| 15:17:40 | raw `0x41/0x4F/0x20` and finish-time sensor became `unknown` | About 15 minutes after last numerical time sample; **cache expiry**, not proof that the machine reset |

During initial running at **14:00:57**, the remote-permission binary sensor
changed to off; this is an independent flag, not a phase code.

### A strong new candidate interpretation of 0x41 versus 0x4F

At **15:02:20**, while the programme was still in the spinning segment
(`0x54=5` until 15:14:36), the API returned **`0x41=116` and
`0x4F=11`**. The HA computed a finish at **16:58:19** from
`0x41=116`, closely matching the official App's **16:57** forecast
observed at 14:59.

In contrast, 11 minutes after 15:02 is **about 15:13**, close to the
next observed transition from spin (`0x54=5`) to drying
(`0x54=6`) at **15:14:36**. This suggests:

- `0x41`: **total programme remaining minutes**, even before drying;
- `0x4F`: potentially **current phase remaining minutes**.

These are **hypotheses**, not established vendor meanings. The previous
drying-only observations saw both `0x41` and `0x4F` equal 111, then
105, compatible with a phase that occupied the rest of the programme.
Still needed: repeated paired readings in wash/rinse/spin/cooling and
confirmation of both meaning and App ETA after programme adjustments.

### Implication for the experimental HA ETA

The initial ETA implementation treated `0x4F` as a general fallback
whenever `0x41` was blank. That could incorrectly show *11 minutes to
programme completion* if the overview returned `0x4F=11` alone during
spinning. The draft now allows `0x4F` to provide an ETA **only when
fresh overview `0x50=2` and `0x54=6` confirm drying together**;
`0x41` remains the preferred overall-programme source.

The existing entity named "drying remaining time" may actually represent
**programme remaining time** from `0x41` in a combined wash+dry mode.
Do not silently change the established entity ID/name or claim the exact
semantics before further validation. Its displayed value should be
understood as an experimental whole-programme estimate.

## Drying remains in phase 6 near App-predicted finish (2026-11-21 16:19)

At **16:19 Asia/Taipei**, the official App was still showing **乾衣中**
with estimated finish **16:21**, unchanged from its adjusted estimate
at 15:56. The same-minute successful account overview GET at **16:19:01**
reported synchronized numeric `0x50=2` and `0x54=6`, with
`0x41`, `0x4F` and `0x20` all `empty_string`. DeviceGetInfo
reported `0x01=1`, `0x50=2`, and `0x74=0`; ETA remained null in HA.

Thus `0x54=6` was repeatedly seen across approximately 55 minutes of
drying, including ~2 minutes before the App's expected finish, even while
the App changed its prediction from 16:57 to 16:21. This does not prove
the actual physical transition occurs exactly at 16:21. A subsequent
sample after the App changes stage is needed to identify the transition,
particularly any intermediate phase preceding anti-crease holding.

## End of wash+dry: drying → anti-crease holding (2026-11-21 16:22)

A second, independently observed wash+dry cycle confirms the candidate
anti-crease holding code.

- At **16:19:01 Asia/Taipei**, the most recent synchronized numeric overview
  reported `0x50=2`, `0x54=6`; the official App still showed
  **乾衣中**, with estimated completion **16:21**.
- At **16:21**, the App remained **乾衣中**, but revised its estimate
  to **16:22**. The user's diagnostics were still the previous sample,
  so no new cloud state was established at that moment.
- By **16:22**, the App showed drying complete and **鬆柔保持中**.
  The new successful overview sample from **16:22:05** showed
  **`0x50=2`, `0x54=11`**, both numeric from the same response.
  `0x41`, `0x4F` and `0x20` remained empty; HA ETA stayed null.
  DeviceGetInfo `0x01=1` and `0x50=2` did not change, and
  remote-operation permission `0x74=0`.

This confirms the **`0x54=6 → 11` transition alongside App drying →
anti-crease** at the granularity of these samples. A previous independent
cycle also showed `0x54=11` during 鬆柔保持中 at **13:24:25**.
Therefore `0x54=11` has multiple supporting observations, but the
manufacturer's official register semantics remain undocumented.

The unchanged `0x50=2` is particularly important: it should mean a
broad running/active state rather than be treated as proof of *ongoing*
drying. The draft classifier already maps synchronized `0x54=11`
to `soft_keeping`. **No new code change is required.** The user subsequently confirmed that the deployed **HA experimental
sensor also displayed 鬆柔保持中**, matching both the official App and
the fresh synchronized cloud readings (`0x50=2`, `0x54=11`).
This completes the end-to-end validation for the anti-crease holding state
on this NA-LX128B. **No new code change is required.**

Note that the last earlier sample was 16:19:01; no intermediate stage
was **observed**, which does not rule out a brief transient stage
between samples. The App's 16:22 estimate matched its transition
minute on this occasion.

## Corrected 16:28 pause interpretation: power code alone is ambiguous

At **16:28:13 Asia/Taipei** on 2026-11-21, the user pressed pause during
anti-crease holding. The official App showed **一時停止中**, but the deployed
experimental HA sensor incorrectly displayed **關機**. DeviceGetInfo reported
`0x01=2` **and** `0x50=7`. These values were previously taken as proof
of power-off based on a single after-off observation; this was wrong.

Crucially, the latest successful `UserGetDeviceStatus` GET at 16:28:13
returned a **new numeric** `overview 0x50=7`, observed at exactly the same
timestamp. `overview 0x54=11` had an older observation timestamp at
**16:25:09** (the preceding anti-crease phase) and was
`empty_string` in the new response, so `0x54=11` must not
override the pause. `0x41`, `0x4F`, and `0x20` were also empty.

In contrast, after **physical power-off** at 13:36:40, DeviceGetInfo also
showed `0x01=2` and `0x50=7`, but the **new** overview GET
returned `0x50=empty_string`, retaining the previously seen `7`
in the 15-minute numeric cache. Thus the overview response *freshness and
classification* are essential for distinguishing these two states.

**Fix in Draft PR #3:** prefer `paused` for verified fresh numeric
overview `0x50=7`, use `off` tentatively when the latest
response is blank, and return unknown for missing/stale/error data.
The new attributes include `overview_0x50_live_classification` so
real-device testing can verify why a label was selected. No increased API
polling or writes. The previous deployed version must be updated
explicitly to pick up this change.

## Corrected classifier: real HA pause → powered-off validation (2026-11-21)

After deploying the fix from commit `b8b49c9`, the user confirmed the new
experimental HA entity displayed **一時停止中** while the washer was paused.
They then switched off the washer normally; after a successful refresh the
same entity displayed **關機**. Both transitions matched the user's
physical actions (and earlier paused state was also confirmed in the official
App).

Final *powered-off* diagnostics showed DeviceGetInfo `0x01=2`,
`0x50=7`, and `available=true`. The **last numeric** overview
`0x50=7` was observed at **16:35:23.963 Asia/Taipei**; the
**latest successful** `UserGetDeviceStatus` request at
**16:38:39.873 Asia/Taipei** returned **empty strings for all five
overview commands**, so the cached `7` is not a current reading.

The corrected classifier accordingly produced the expected two different
states even though DeviceGetInfo `0x01=2, 0x50=7` stayed unchanged:
**fresh numeric overview 0x50=7 → paused**, then
**fresh empty overview 0x50 → tentative off**. This is the first
successful end-to-end HA test of the pause/power-off distinction.

The cloud can occasionally return blank codes while a programme is running,
so **one empty response is not authoritative proof of physical power-off**
in every circumstance. Keep the classification experimental and verify
future unusual state changes against the app and sensor timestamps.
`available=true` is the integration's availability flag; it does not
prove the washer is powered on.

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
- [x] Capture `0x01=2`, DeviceGetInfo `0x50=7` and **fresh**
  overview `0x50=7` while the App says 一時停止中 after anti-crease
  (16:28:13). Confirmed old `off` priority was incorrect.
- [ ] Repeat pause during washing, not only after drying/anti-crease; test
  whether the same overview freshness rule distinguishes a pause.
- [x] Confirm **powered-off** after pause on the corrected HA classifier:
  16:38:39 fresh blank overview response with DeviceGetInfo `0x01=2`,
  `0x50=7` correctly displayed 關機.
- [ ] Check whether a blank overview response can occur **while still paused**
  (which would cause an experimental false `off` label); do not treat
  this single successful transition as proof the heuristic is infallible.
- [x] Observe `0x54=5` → `0x54=6` during the same wash+dry cycle as the official App changes from spinning (14:59) to drying (15:24) on 2026-11-21.
- [ ] During a later normal wash+dry session, record official App phase
  alongside fresh `0x54=3` and `0x54=4` to test possible wash and rinse
  mappings; the CSV alone does not confirm their meanings.
- [ ] Capture `0x41` and `0x4F` **at the same read** in multiple
  different phases and compare to both App's total completion estimate
  and subsequent phase transitions. Especially test whether `0x4F` is
  the current phase countdown and whether `0x41` is total programme time.
- [ ] Repeat `0x54=5` during spin in a wash-only programme as well as
  wash+dry; check if rinse/spin transitions use a different code.
- [ ] Repeat `0x54=6` in a drying programme, and verify that a *wash-only*
  programme never uses it for an unrelated phase.
- [x] Repeat `0x54=11` in anti-crease holding with DeviceGetInfo `0x01=1` (16:22:05, second wash+dry cycle on 2026-11-21).
- [ ] Determine whether short cooling/airflow phases exist between `0x54=6`
  and `0x54=11`, or whether `0x54=11` also appears elsewhere. The three-minute
  observation gap cannot rule out brief transitions.
- [x] Verify the **HA experimental sensor shows 鬆柔保持中**
  while the App does; the user confirmed this for the 16:22 transition
  from `0x54=6` to `0x54=11`.
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
