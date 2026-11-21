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

    DeviceGetInfo 0x01=2 takes priority over a lingering 0x50=7 on power-off.
    For running programmes, extra 0x54 stages are shown only with synchronized
    numeric overview readings; otherwise fall back to generic 'running'.
    """
    if str(device.get("model") or "").upper() != "NA-LX128B":
        return None

    status = device.get("status") or {}
    power = _code_int(status.get("0x01"))
    operation = _code_int(status.get("0x50"))
    if power == 2:
        return "off"
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
        "overview_phase_current": _confirmed_phase(device, now) is not None,
    }
