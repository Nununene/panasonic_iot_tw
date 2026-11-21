"""Read-only Panasonic laundry overview decoding and short-lived status cache.

The account-level UserGetDeviceStatus endpoint is distinct from DeviceGetInfo.
All overview codes in this module are OBSERVED, not vendor-documented meanings.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

OVERVIEW_CODES = ("0x41", "0x4F", "0x50", "0x54", "0x20")
OVERVIEW_POLL_INTERVAL = timedelta(minutes=3)
OVERVIEW_FRESHNESS = timedelta(minutes=15)
MAX_REMAINING_MINUTES = 599
_INTEGER = re.compile(r"-?\d{1,6}\Z")


def _parse_integer(value: Any) -> int | None:
    """Do not treat empty, invalid sentinels, bools or free text as values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if -100000 <= value <= 100000 else None
    if isinstance(value, str) and _INTEGER.fullmatch(value.strip()):
        value_int = int(value.strip())
        return value_int if -100000 <= value_int <= 100000 else None
    return None


def decode_overview(response: Any) -> dict[str, dict[str, int]]:
    """Map the cloud's GWID/List format to five observable numeric statuses."""
    result: dict[str, dict[str, int]] = {}
    if not isinstance(response, dict):
        return result
    gw_list = response.get("GwList")
    if not isinstance(gw_list, list):
        return result
    for gateway in gw_list:
        if not isinstance(gateway, dict):
            continue
        gwid = gateway.get("GWID")
        items = gateway.get("List")
        if not isinstance(gwid, str) or not gwid or not isinstance(items, list):
            continue
        readings: dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            command = item.get("CommandType")
            if command not in OVERVIEW_CODES:
                continue
            value = _parse_integer(item.get("Status"))
            if value is not None:
                readings[command] = value
        result[gwid] = readings
    return result


def _read_iso_time(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        stamp = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return stamp if stamp.tzinfo is not None else None


def _is_recent(when: datetime | None, now: datetime) -> bool:
    return when is not None and timedelta(0) <= now - when <= OVERVIEW_FRESHNESS


def attach_laundry_overview(
    devices: dict[str, dict[str, Any]],
    previous: dict[str, dict[str, Any]] | None,
    payload: Any,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    """Attach five observed codes and a bounded ETA to Type 3 devices.

    Blank status strings never reset an earlier *recent* ETA. A later confirmed
    non-running state clears it; all cached readings expire after 15 minutes.
    """
    if now.tzinfo is None:
        raise ValueError("now must have timezone information")
    previous = previous or {}
    overview_by_gwid = decode_overview(payload)
    for device_key, device in devices.items():
        if device.get("device_type") != 3:
            continue
        before = previous.get(device_key, {})
        gwid = device.get("gwid")
        incoming = overview_by_gwid.get(gwid, {}) if isinstance(gwid, str) else {}
        old_values = before.get("overview_status", {})
        old_times = before.get("overview_observed_at", {})
        if not isinstance(old_values, dict):
            old_values = {}
        if not isinstance(old_times, dict):
            old_times = {}

        values: dict[str, int] = {}
        times: dict[str, str] = {}
        for command in OVERVIEW_CODES:
            if command in incoming:
                values[command] = incoming[command]
                times[command] = now.isoformat()
                continue
            observed = _read_iso_time(old_times.get(command))
            old_value = _parse_integer(old_values.get(command))
            if _is_recent(observed, now) and old_value is not None:
                values[command] = old_value
                times[command] = observed.isoformat()

        device["overview_status"] = values
        device["overview_observed_at"] = times

        # A *fresh* stop/finish state overrides any cached estimate.
        # Two consecutive samples during drying showed 0x50=2 (running).
        stopped = "0x50" in incoming and incoming["0x50"] != 2
        if stopped:
            device["overview_expected_finish_at"] = None
            device["overview_eta_observed_at"] = None
            device["overview_eta_command"] = None
            continue

        # Prefer new 0x41; use 0x4F when 0x41 is temporarily absent.
        candidate_code = next((
            code for code in ("0x41", "0x4F")
            if code in incoming and 0 <= incoming[code] <= MAX_REMAINING_MINUTES
        ), None)
        if candidate_code is not None and values.get("0x50") == 2:
            minutes = incoming[candidate_code]
            device["overview_expected_finish_at"] = (
                now + timedelta(minutes=minutes)
            ).isoformat()
            device["overview_eta_observed_at"] = now.isoformat()
            device["overview_eta_command"] = candidate_code
            continue

        last_seen = _read_iso_time(before.get("overview_eta_observed_at"))
        last_eta = _read_iso_time(before.get("overview_expected_finish_at"))
        if (
            not stopped
            and _is_recent(last_seen, now)
            and last_eta is not None
            and values.get("0x50") == 2
        ):
            device["overview_expected_finish_at"] = last_eta.isoformat()
            device["overview_eta_observed_at"] = last_seen.isoformat()
            device["overview_eta_command"] = before.get("overview_eta_command")
        else:
            device["overview_expected_finish_at"] = None
            device["overview_eta_observed_at"] = None
            device["overview_eta_command"] = None
    return devices
