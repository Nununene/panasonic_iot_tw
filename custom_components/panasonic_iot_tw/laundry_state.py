"""Experimental read-only NA-LX128B appliance state from live observations.

The codes below are *not* documented by Panasonic. In particular, the
account-level overview caches each code independently, so a numeric 0x54 in
the cache is not necessarily the current appliance phase.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .laundry_overview import (
    fresh_device_info_command,
    fresh_overview_command,
    preferred_laundry_command,
)

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


def _current_operation(
    device: dict[str, Any], now: datetime,
) -> tuple[int | None, str | None]:
    """Use current direct status first, then fresh Overview 0x50.

    Older synthetic tests and pre-upgrade snapshots have no freshness
    marker. Only those legacy cases keep the historical raw-status path.
    Real coordinator polls always have status_observed_at/status_is_cached.
    """
    direct = fresh_device_info_command(device, "0x50", now)
    if direct is not None:
        return direct, "DeviceGetInfo"
    if (
        "status_observed_at" not in device
        and "status_is_cached" not in device
    ):
        legacy = _code_int((device.get("status") or {}).get("0x50"))
        if legacy is not None:
            return legacy, "DeviceGetInfo (legacy)"
    return preferred_laundry_command(device, "0x50", now)


def _confirmed_phase(device: dict[str, Any], now: datetime) -> int | None:
    """Select live 0x54: DeviceGetInfo first, Overview second."""
    # Power 0x01=2 can mean paused or off; never claim an active phase.
    status = device.get("status") or {}
    if _code_int(status.get("0x01")) == 2:
        return None
    operation, _ = _current_operation(device, now)
    if operation != 2:
        return None
    phase, source = preferred_laundry_command(device, "0x54", now)
    if source == "UserGetDeviceStatus":
        # A phase taken from Overview cannot be trusted if this same
        # fresh Overview explicitly reports a non-running operation.
        overview_operation = fresh_overview_command(device, "0x50", now)
        if overview_operation is not None and overview_operation != 2:
            return None
    return phase


def laundry_operation_state(
    device: dict[str, Any], now: datetime,
) -> str | None:
    """Return a cautious, model-specific HA enum key or None (unknown).

    0x01=2 is observed in BOTH pause and powered-off states. When DeviceGetInfo
    0x50=7 too, a *fresh* numeric overview 0x50=7 means pause; an empty
    latest overview 0x50 is a tentative powered-off signal. Unknown/old
    cloud data must not be mistaken for a confirmed powered-off state.
    For running programmes, prefer fresh DeviceGetInfo 0x54; if it is
    missing/invalid/stale, use a fresh numeric UserGetDeviceStatus 0x54.
    Stale cached values from either endpoint are not active stages.
    """
    if str(device.get("model") or "").upper() != "NA-LX128B":
        return None

    status = device.get("status") or {}
    power = _code_int(status.get("0x01"))
    raw_operation = _code_int(status.get("0x50"))
    operation, _ = _current_operation(device, now)
    if power == 2 and raw_operation == 7:
        # Real pause sample: 0x01=2 AND DeviceGetInfo 0x50=7, while the
        # latest UserGetDeviceStatus GET returns a new numeric 0x50=7.
        # After power-off both DeviceGetInfo values persist but a new GET
        # returned an empty overview 0x50, leaving cached 7 behind.
        # A bridged or expired DGI snapshot does not prove present
        # power state. Keep pause/off distinction conservative.
        if device.get("status_is_cached") is True:
            return None
        if (
            "status_observed_at" in device
            and fresh_device_info_command(device, "0x50", now) != 7
        ):
            return None
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
    if phase == 4:
        # LX128B CommandList: 0x54=4 is 洗清; App independently showed
        # 洗清中 in wash-only after phase 3 at 20:30 on 2026-11-21.
        return "rinsing"
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
        "device_get_info_0x54": status.get("0x54"),
        "device_get_info_observed_at": device.get("status_observed_at"),
        "device_get_info_is_cached": device.get("status_is_cached"),
        "effective_0x50_source": _current_operation(device, now)[1],
        "effective_0x54_source": (
            preferred_laundry_command(device, "0x54", now)[1]
            if _confirmed_phase(device, now) is not None else None
        ),
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
