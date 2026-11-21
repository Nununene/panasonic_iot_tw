"""Experimental read-only NA-LX128B appliance state from live observations.

The codes below are *not* documented by Panasonic. In particular, the
account-level overview caches each code independently, so a numeric 0x54 in
the cache is not necessarily the current appliance phase.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

# Slightly longer than the usual 3-minute overview poll, but not the entire
# 15-minute numeric-cache lifetime. A stage is reported only if BOTH codes
# came from the same most recent successful overview response.
PHASE_FRESHNESS = timedelta(minutes=5)


def _code_int(value: Any) -> int | None:
    """Parse exact numeric status codes, excluding API error sentinels."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if raw.isascii() and raw.isdecimal():
            return int(raw)
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _latest_overview_operation(device: dict[str, Any], now: datetime) -> tuple[str | None, int | None]:
    """Return the latest *fresh response* classification and verified 0x50.

    Cached overview_status 0x50 can still say 7 after powering off. Treat
    it as the present pause only when its observation timestamp is exactly
    the latest matched cloud response's timestamp. A blank is different
    from an old cached number; an error/absent response is not 'off'.
    """
    summary = device.get("overview_last_response_summary") or {}
    if summary.get("result") != "matched":
        return (None, None)
    response_time = _timestamp(device.get("overview_last_response_at"))
    if (
        response_time is None
        or not timedelta(0) <= now - response_time <= PHASE_FRESHNESS
    ):
        return (None, None)
    kind = (summary.get("commands") or {}).get("0x50")
    if kind != "numeric":
        return (kind, None)
    observed = _timestamp(
        (device.get("overview_observed_at") or {}).get("0x50")
    )
    if observed != response_time:
        return (None, None)
    value = _code_int((device.get("overview_status") or {}).get("0x50"))
    return ("numeric", value) if value is not None else (None, None)


def _confirmed_phase(device: dict[str, Any], now: datetime) -> int | None:
    """Use 0x54 only when it and overview 0x50 are from this same live read."""
    # Even a matching overview pair must never be treated as the current phase
    # if DeviceGetInfo now reports off, standby or paused.
    status = device.get("status") or {}
    if _code_int(status.get("0x01")) == 2 or _code_int(
        status.get("0x50")
    ) != 2:
        return None
    summary = device.get("overview_last_response_summary") or {}
    if summary.get("result") != "matched":
        return None
    commands = summary.get("commands") or {}
    if commands.get("0x50") != "numeric" or commands.get("0x54") != "numeric":
        return None

    latest = _timestamp(device.get("overview_last_response_at"))
    observed = device.get("overview_observed_at") or {}
    time_50 = _timestamp(observed.get("0x50"))
    time_54 = _timestamp(observed.get("0x54"))
    if (
        latest is None
        or time_50 != latest
        or time_54 != latest
        or not timedelta(0) <= now - latest <= PHASE_FRESHNESS
    ):
        return None

    values = device.get("overview_status") or {}
    if _code_int(values.get("0x50")) != 2:
        return None
    return _code_int(values.get("0x54"))


def laundry_operation_state(
    device: dict[str, Any], now: datetime,
) -> str | None:
    """Return a cautious, model-specific HA enum key or None (unknown).

    0x01=2 is observed in BOTH pause and powered-off states. When DeviceGetInfo
    0x50=7 too, a *fresh* numeric overview 0x50=7 means pause; an empty
    latest overview 0x50 is a tentative powered-off signal. Unknown/old
    cloud data must not be mistaken for a confirmed powered-off state.
    For running programmes, extra 0x54 stages are shown only with synchronized
    numeric overview readings; otherwise fall back to generic 'running'.
    """
    if str(device.get("model") or "").upper() != "NA-LX128B":
        return None

    status = device.get("status") or {}
    power = _code_int(status.get("0x01"))
    operation = _code_int(status.get("0x50"))
    if power == 2 and operation == 7:
        # Real pause sample: 0x01=2 AND DeviceGetInfo 0x50=7, while the
        # latest UserGetDeviceStatus GET returns a new numeric 0x50=7.
        # After power-off both DeviceGetInfo values persist but a new GET
        # returned an empty overview 0x50, leaving cached 7 behind.
        overview_kind, overview_value = _latest_overview_operation(device, now)
        if overview_kind == "numeric" and overview_value == 7:
            return "paused"
        if overview_kind == "empty_string":
            return "off"
        return None
    if power == 2:
        # Unobserved combination: avoid treating 0x01=2 alone as off.
        return None
    if operation == 1:
        return "standby"
    if operation == 7:
        return "paused"
    if operation != 2:
        return None

    phase = _confirmed_phase(device, now)
    if phase == 11:
        return "soft_keeping"
    if phase == 6:
        return "drying"
    if phase == 5:
        return "spinning"
    if phase == 3:
        # LX128B CommandList: 0x54=3 is 洗衣; App independently showed
        # 洗衣中 in a wash-only programme at 20:24 on 2026-11-21.
        return "washing"
    return "running"


def laundry_state_attributes(
    device: dict[str, Any], now: datetime,
) -> dict[str, Any]:
    """Expose evidence and its age without claiming cached 0x54 is live."""
    status = device.get("status") or {}
    values = device.get("overview_status") or {}
    observed = device.get("overview_observed_at") or {}
    return {
        "interpretation": "experimental_na_lx128b",
        "device_get_info_0x01": status.get("0x01"),
        "device_get_info_0x50": status.get("0x50"),
        "overview_0x50": values.get("0x50"),
        "overview_0x54": values.get("0x54"),
        "overview_0x50_observed_at": observed.get("0x50"),
        "overview_0x54_observed_at": observed.get("0x54"),
        "overview_last_response_at": device.get("overview_last_response_at"),
        "overview_0x50_live_classification": _latest_overview_operation(
            device, now
        )[0],
        "overview_phase_current": _confirmed_phase(device, now) is not None,
    }
