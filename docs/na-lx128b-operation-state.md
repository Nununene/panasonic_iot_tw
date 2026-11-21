# NA-LX128B: experimental read-only operation state

> **Status:** draft/experimental, based on one user's 2026-11-21
> (Asia/Taipei) live observation of a Panasonic NA-LX128B washer-dryer.
> Panasonic has **not** documented these interpretations. This feature
> deliberately supports **only exact model NA-LX128B**; do not generalize to
> other washer models or the stand-alone Type 6 dryer.

The new HA enum entity is **Washer-Dryer Operation State (Experimental)**,
unique-ID suffix `laundry_operation_state`. It **does not control the machine**,
change polling rates, or interfere with drying ETA logic.

## Model command metadata acquired after live tests

The user performed a separate **read-only** command-schema lookup from
`GET /api/UserGetRegisteredGwList2`. The resulting filtered export
matched **NA-LX128B / LX128B**, device type **3**, with twelve commands
and command-set timestamp **202204180700**.

Cloud-provided descriptions for `0x54` (**工程訊息**) include:

| Code | Metadata text | Real-device App comparison |
| --- | --- | --- |
| 1 | 預洗 | Not yet observed |
| 2 | 浸泡 | Not yet observed |
| 3 | 洗衣 | 14:00:57 HA history code observed; App not contemporaneously checked |
| 4 | 洗清 | 14:28:34 HA history code observed; App not contemporaneously checked |
| 5 | 脫水 | Live App and cloud agree |
| 6 | 冷風乾燥 | Live App said 乾衣中; UI wording differs |
| 9 | 冷卻 | Not yet observed |
| 11 | 延後晾衣 | Live App and HA said 鬆柔保持中; UI wording differs |

The same metadata labels `0x50` **運轉情報**, enum
`1=待機中`, `2=動作中`, `3/4=預約中`, `5=終了`.
**It omits `0x50=7`**, which was observed during real pause. Therefore
these 2022 definitions are helpful semantic evidence, but incomplete
for 2026 runtime behaviour. Preserve the live App-tested labels and
experimental off/pause distinction.

It also defines **`0x41=洗衣殘時間`** (range 0–1440 minutes)
and **`0x4F=nanoe殘時間`** (range 0–1440 minutes).
This contradicts the earlier guess that 0x4F was the current spin
phase's countdown. The ETA policy was corrected on this branch to
use **0x41 only**; never synthesize programme completion from 0x4F.

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

### Earlier 0x4F phase-minute hypothesis: **superseded by metadata**

At 15:02:20 the cloud reported `0x41=116`, `0x4F=11`
during spinning (`0x54=5`). The HA 0x41-derived finish near
16:58 matched the App's approximately 16:57 estimate. The apparent
match between 11 minutes and the later 15:14 drying transition initially
suggested that 0x4F was a current-stage countdown.

**That hypothesis is no longer supported**: the model's CommandList
explicitly labels `0x4F` as **nanoe殘時間**. No interpretation of
the observed 11 minutes as "time to drying" should be made.
The now-corrected implementation uses only `0x41` for ETA;
`0x4F` remains a read-only diagnostic. Equality of 0x41 and 0x4F
in earlier drying snapshots is not sufficient evidence to override this.

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

## Wash-only programme: App confirms 0x54=3 = washing (2026-11-21)

At approximately **20:24 Asia/Taipei** the user's family was running
a **wash-only** cycle (no drying); the official Panasonic App showed
**洗衣中** and estimated programme completion at **20:53**. The installed
experimental HA sensor still displayed generic **運轉中**, because it had
no specific classifier for phase 3.

The latest successful account overview GET at **20:24:24 Asia/Taipei**
returned `overview 0x50=2` and `0x54=3`, **both fresh numeric**
with identical observation timestamps. DeviceGetInfo had `0x01=1`,
`0x50=2`, `0x55=0`, `0x72=8192`, `0x73=1`, and `0x74=0`.
The most recent cloud time fields `0x41` and `0x4F` were again
`empty_string`; the absence of an ETA in HA is expected.

This is independent live App corroboration of the **LX128B CommandList
enum `0x54=3` = 洗衣**, on a *wash-only* programme in addition to the
earlier wash+dry history where phase 3 was logged at 14:00:57.
Draft PR #3 now maps fresh, synchronized `0x54=3` to HA
`washing` / **洗衣中**, with an explicit enum option and translations.
Missing or stale overview data still falls back to `running`.

**Not yet validated on deployed HA:** the user-observed UI was still
`running` on the older deployed version. Deploy the updated branch
after this cycle ends if an end-to-end HA UI confirmation is desired.
The data also include `0x72=8192`, `0x73=1` during wash-only,
versus `0x72=512`, `0x73=3` in an earlier wash+dry sequence; treat
these as correlations only, not a safe programme discriminator.

The same command metadata declares `0x54=4` as **洗清**.
Its transition appeared in the previous wash+dry HA CSV, but a
contemporaneous official App comparison in this wash-only cycle is
still needed before adding the new translated HA `rinsing` enum.

## Wash-only transition to rinsing: App confirms 0x54=4 (2026-11-21)

During the **same wash-only programme** described above, the official
Panasonic App switched to **洗清中**. At **20:30:32 Asia/Taipei**,
the successful overview GET returned synchronized numeric values
`0x50=2` and **`0x54=4`**, observed at the exact same time.
DeviceGetInfo reported `0x01=1`, `0x50=2`, `0x55=0`,
`0x72=8192`, `0x73=1`, and `0x74=0`; `0x41`,
`0x4F`, and `0x20` were `empty_string`. The previous
same-programme sample at **20:24:24** had `0x54=3` while the
App said **洗衣中**.

This is a *live wash-only* transition from `0x54=3` (洗衣)
to `0x54=4` (洗清), confirming the LX128B cloud CommandList
mapping and the App's wording. The classifier now supports
`rinsing` / **洗清中** for a fresh synchronized `0x54=4`
and adds the HA enum option and zh-Hant/en translations. Stale or
blank 0x54 continues to fall back to generic `running`.

**Deployed HA end-to-end check is still pending**: the washer is running
the household's actual cycle and the user was advised not to restart HA
until it finishes. Do not infer the physical transition's precise
start time from these snapshots.

## Wash-only cycle: App confirms 0x54=5 spinning (2026-11-21)

During the **same wash-only / no-drying** programme, the official
Panasonic App showed **脫水中**. The successful account overview
GET at **20:45:52 Asia/Taipei** returned **new numeric**
`0x50=2` and **`0x54=5`**, both observed at the identical
timestamp (2026-11-21T12:45:52.286225+00:00).
DeviceGetInfo reported `0x01=1`, `0x50=2`, `0x55=0`,
`0x72=8192`, `0x73=1`, `0x74=0`, and the same
sentinel values as during washing/rinsing. The latest account
overview `0x41`, `0x4F`, and `0x20` were empty;
HA's overview-derived ETA remained null.

This gives a **three-stage same-cycle wash-only observation**:
`0x54=3` 洗衣 at **20:24:24** →
`0x54=4` 洗清 at **20:30:32** →
`0x54=5` 脫水 at **20:45:52**. Each is corroborated by
the official App and the model's CommandList.
This independently confirms that code `5` means **spinning**
in both wash-only and wash+dry programmes. These are API
observation times, not exact mechanical phase-transition instants.

The Draft PR classifier **already supports** fresh synchronized
`0x54=5` → `spinning`, so **no code change is required**.
The user subsequently confirmed the **deployed HA experimental sensor
also displayed 脫水中** in this wash-only cycle, matching the official
App and the fresh 20:45:52 cloud sample. This completes end-to-end
wash-only spinning state validation without any HA restart or code changes.

## Wash-only end: reported nanoe anti-mold wait, then power-off (2026-11-21)

Following the same wash-only cycle, the user reported that **after
spinning finished**, the official Panasonic App briefly showed
**「NANOE除霉等待中」** and the washer was switched off shortly
afterwards. At the time of the report, the HA experimental operation
sensor showed the generic **運轉中**, not a specialized nanoe wait or
off state.

The attached diagnostic snapshot's **most recent successful overview**
was at **20:52:00.237 Asia/Taipei** (UTC 12:52:00.237).
Both `0x50=2` and `0x54=5` were fresh, synchronized numeric
values **at that specific timestamp**. DeviceGetInfo in the snapshot
also reported `0x01=1` and `0x50=2`; the rest of the status
included `0x55=0`, `0x72=8192`, `0x73=1`,
`0x74=0`. `0x41`, `0x4F`, `0x20` again
returned empty strings and there was no HA ETA.

**Do not assign a nanoe or finished-phase code from this sample.**
Although the app reportedly moved beyond spin, the sampled overview
still said `0x54=5`, and there is **no fresh cloud or
DeviceGetInfo evidence from after that reported transition**.
The actual transition times and manner of power-off were not
captured. The app's brief nanoe message is a useful observation,
but is not yet tied to a register value.

The HA classifier deliberately returns generic `running` if
DeviceGetInfo still reports `0x50=2` while a detailed-phase sample
is older than its five-minute freshness window. The reported
`運轉中` is therefore *consistent with* stale phase handling;
it does **not** prove that polling stopped, nor that nanoe wait
means generic running. A **new diagnostic capture after power-off**
should compare DeviceGetInfo `0x01`/`0x50`,
`overview_last_response_at`, latest `0x50` response
classification, and observed timestamps. The earlier
paused→powered-off heuristic may not cover a wash-only cycle
that was switched off directly after completion.

No classifier or ETA changes are warranted without a new synchronized
post-spin observation. Cloud metadata calls `0x4F`
`nanoe殘時間`, but it was empty here, so it does not establish
nanoe activity or an ETA.

### Subsequent 20:58:07 snapshot after the user confirmed power-off

The user confirmed that the machine had been **physically off for a while**,
but HA still displayed **運轉中**. A newly successful
`UserGetDeviceStatus` response at **20:58:07.992 Asia/Taipei**
(`2026-11-21T12:58:07.992902+00:00`) was `matched` and
returned **`empty_string` for all five commands**:
`0x41`, `0x4F`, `0x50`, `0x54`, `0x20`.
The stored `overview_status` still showed `0x50=2`,
`0x54=5` only because their `overview_observed_at` timestamps
were unchanged at **20:52:00.237**. Thus a newer request
succeeded, and the two numeric overview values were stale.

The processed DeviceGetInfo `status` remained **`0x01=1`,
`0x50=2`**, leading the current classifier to output generic
`running` once the valid phase timestamp exceeded five minutes.
The diagnostics do **not** expose a per-code DeviceGetInfo read
timestamp or whether a previous device response was bridged through
the integration's transient-failure tolerance. We therefore cannot
distinguish (a) a live cloud response returning out-of-date values,
(b) the integration retaining last-known-good DeviceGetInfo data, or
(c) another delay in the device/cloud pipeline using this snapshot
alone. In particular, `available=true` does not prove a new
successful DeviceGetInfo read.

This **differs from the earlier pause→power-off case**, whose
DeviceGetInfo status was `0x01=2,0x50=7` and could be
disambiguated with a fresh blank overview `0x50`. A blank
overview alone must **not automatically mean power-off** for
a device whose DeviceGetInfo still says `0x01=1,0x50=2`:
blank cloud responses have also occurred during legitimate running.
A follow-up diagnostic after additional ordinary polling, plus
a future **read-only DeviceGetInfo freshness/source marker**, would
help separate cache reuse from cloud lag. Consider representing
unconfirmed state as unknown only after gathering more evidence,
instead of inventing `off` from one blank overview.


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
- [x] Confirm `0x54=3` (洗衣) against the official App in a wash-only
  programme at 20:24:24 on 2026-11-21; newer HA enum support added, UI test pending.
- [x] Confirm `0x54=4` (洗清) against the official App in a
  wash-only programme at 20:30:32 on 2026-11-21; newer HA enum support
  added, deployed UI test pending.
- [ ] Check `0x41` (metadata: 洗衣殘時間) against App completion
  time across future cycles. Investigate `0x4F` (metadata: nanoe殘時間)
  separately, without treating it as current-stage or total ETA.
- [ ] Observe the official App if `0x54=9` (metadata: 冷卻) ever appears,
  and whether it occurs between `0x54=6` and `0x54=11`.
- [x] Confirm `0x54=5` during **wash-only** spin after
  `0x54=4` rinse at 20:45:52 on 2026-11-21; App and cloud match.
  Code `5` had also been confirmed during wash+dry spin.
- [x] Confirm deployed HA experimental sensor shows 脫水中 during
  the 20:45 wash-only spinning phase, matching App and API.
- [ ] Repeat `0x54=6` in a drying programme, and verify that a *wash-only*
  programme never uses it for an unrelated phase.
- [x] Repeat `0x54=11` in anti-crease holding with DeviceGetInfo `0x01=1` (16:22:05, second wash+dry cycle on 2026-11-21).
- [ ] Determine whether short cooling/airflow phases exist between `0x54=6`
  and `0x54=11`, or whether `0x54=11` also appears elsewhere. The three-minute
  observation gap cannot rule out brief transitions.
- [x] Verify the **HA experimental sensor shows 鬆柔保持中**
  while the App does; the user confirmed this for the 16:22 transition
  from `0x54=6` to `0x54=11`.
- [x] Capture **new post-power-off overview diagnostics** at 20:58:07:
  five latest `empty_string` commands with `0x50=2,0x54=5`
  retained only as 20:52:00 numeric cache. HA still displayed 運轉中,
  and processed DeviceGetInfo retained `0x01=1,0x50=2`.
- [ ] Determine whether post-power-off DeviceGetInfo `0x01=1,0x50=2`
  came from a fresh remote reply or integration fallback/cache; current
  diagnostics contain no per-request proof of that. Track later
  natural polls without forcing a washer restart.
- [ ] Observe the brief **NANOE除霉等待中** App stage alongside a
  *fresh synchronized* cloud overview before assigning a stage code.
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
