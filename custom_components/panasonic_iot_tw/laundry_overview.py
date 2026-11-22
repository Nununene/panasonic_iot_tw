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
# When a washer is running but the cloud returned only empty minute fields,
# allow a bounded extra attempt on the next coordinator update. Never spin.
OVERVIEW_MISSING_ETA_RETRY_INTERVAL = timedelta(minutes=1)
OVERVIEW_FRESHNESS = timedelta(minutes=15)
# NA-LX128B CommandList: 0x41 洗衣殘時間, allowed range 0..1440 min.
MAX_REMAINING_MINUTES = 1440
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


def summarize_overview_response(payload: Any, gwid: str | None) -> dict[str, Any]:
    """Report response *shape*, never raw cloud fields or gateway identifiers.

    The value cache intentionally omits blank fields. This separate safe
    summary distinguishes a blank Status from a missing CommandType, a missing
    gateway, malformed payloads, and valid readings without leaking secrets.
    """
    states = {command: "not_returned" for command in OVERVIEW_CODES}
    if not isinstance(payload, dict):
        return {"result": "invalid_response", "commands": states}
    gateways = payload.get("GwList")
    if not isinstance(gateways, list):
        return {"result": "invalid_gateway_list", "commands": states}

    matches = [
        gateway for gateway in gateways
        if isinstance(gateway, dict)
        and isinstance(gwid, str) and gwid
        and gateway.get("GWID") == gwid
    ]
    if not matches:
        return {"result": "gateway_not_returned", "commands": states}

    has_valid_list = False
    for gateway in matches:
        items = gateway.get("List")
        if not isinstance(items, list):
            continue
        has_valid_list = True
        for item in items:
            if not isinstance(item, dict):
                continue
            command = item.get("CommandType")
            if not isinstance(command, str) or command not in states:
                continue
            if "Status" not in item:
                state = "status_field_missing"
            elif item["Status"] == "":
                state = "empty_string"
            elif _parse_integer(item["Status"]) is not None:
                state = "numeric"
            else:
                state = "invalid_value"
            # If a gateway repeats a command, the latest valid sample wins.
            if states[command] != "numeric" or state == "numeric":
                states[command] = state

    return {
        "result": "matched" if has_valid_list else "invalid_command_list",
        "commands": states,
    }


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


# A source is only eligible when its own latest reading is recent.
# This is deliberately shorter than the separately bounded ETA cache.
STATUS_SOURCE_FRESHNESS = timedelta(minutes=5)


def _command_value(raw: Any, command: str) -> int | None:
    """Discard blanks, API sentinels and values invalid for this code."""
    value = _parse_integer(raw)
    if value is None or value < 0:
        return None
    if command == "0x50" and value not in (0, 1, 2, 3, 4, 5, 7, 8):
        return None
    if command == "0x54" and value not in (1, 2, 3, 4, 5, 6, 9, 11):
        return None
    if command in ("0x41", "0x4F") and value > MAX_REMAINING_MINUTES:
        return None
    return value


def fresh_device_info_command(
    device: dict[str, Any], command: str, now: datetime,
) -> int | None:
    """Return a fresh DeviceGetInfo value, never a bridged/cache value."""
    stamp = _read_iso_time(device.get("status_observed_at"))
    if (
        device.get("available") is not True
        or device.get("status_is_cached") is True
        or stamp is None
        or not timedelta(0) <= now - stamp <= STATUS_SOURCE_FRESHNESS
    ):
        return None
    return _command_value((device.get("status") or {}).get(command), command)


def fresh_overview_command(
    device: dict[str, Any], command: str, now: datetime,
) -> int | None:
    """Return only a verified numeric value from the latest overview GET."""
    summary = device.get("overview_last_response_summary") or {}
    if (
        summary.get("result") != "matched"
        or (summary.get("commands") or {}).get(command) != "numeric"
    ):
        return None
    latest = _read_iso_time(device.get("overview_last_response_at"))
    observed = _read_iso_time(
        (device.get("overview_observed_at") or {}).get(command)
    )
    if (
        latest is None
        or observed != latest
        or not timedelta(0) <= now - latest <= STATUS_SOURCE_FRESHNESS
    ):
        return None
    return _command_value(
        (device.get("overview_status") or {}).get(command), command
    )


def preferred_laundry_command(
    device: dict[str, Any], command: str, now: datetime,
) -> tuple[int | None, str | None]:
    """DeviceGetInfo first; fall back to fresh UserGetDeviceStatus only."""
    direct = fresh_device_info_command(device, command, now)
    if direct is not None:
        return direct, "DeviceGetInfo"
    overview = fresh_overview_command(device, command, now)
    if overview is not None:
        return overview, "UserGetDeviceStatus"
    return None, None


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

        # Per-command source precedence, including a stop signal:
        # a current DeviceGetInfo 0x50 overrides a contradictory Overview
        # 0x50. A bridged/expired DeviceGetInfo value never takes priority.
        direct_operation = fresh_device_info_command(device, "0x50", now)
        live_operation = (
            direct_operation if direct_operation is not None
            else _command_value(incoming.get("0x50"), "0x50")
        )
        stopped = live_operation is not None and live_operation != 2
        if stopped:
            device["overview_expected_finish_at"] = None
            device["overview_eta_observed_at"] = None
            device["overview_eta_command"] = None
            continue

        # LX128B schema calls 0x41 洗衣殘時間, 0x4F nanoe殘時間.
        # On 2026-11-22, direct 0x41=150 while overview 0x41="".
        # Prefer a fresh direct reading, then this response's fresh
        # overview 0x41. NEVER derive programme ETA from nanoe 0x4F.
        minutes = None
        source = None
        if live_operation == 2:
            if str(device.get("model") or "").upper() == "NA-LX128B":
                minutes = fresh_device_info_command(device, "0x41", now)
                if minutes is not None:
                    source = "device_get_info:0x41"
            if minutes is None:
                minutes = _command_value(incoming.get("0x41"), "0x41")
                if minutes is not None:
                    source = "0x41"
        if minutes is not None:
            device["overview_expected_finish_at"] = (
                now + timedelta(minutes=minutes)
            ).isoformat()
            device["overview_eta_observed_at"] = now.isoformat()
            device["overview_eta_command"] = source
            continue

        last_seen = _read_iso_time(before.get("overview_eta_observed_at"))
        last_eta = _read_iso_time(before.get("overview_expected_finish_at"))
        if (
            not stopped
            and _is_recent(last_seen, now)
            and last_eta is not None
            and before.get("overview_eta_command")
            in ("0x41", "device_get_info:0x41")
            and (
                live_operation == 2
                or (
                    live_operation is None
                    and device.get("available") is True
                    # A two-poll bridge may retain a previous ETA as
                    # a bounded countdown, but must never re-timestamp
                    # that value as a newly measured remaining time.
                    and (device.get("status") or {}).get("0x50") == "2"
                )
            )
        ):
            device["overview_expected_finish_at"] = last_eta.isoformat()
            device["overview_eta_observed_at"] = last_seen.isoformat()
            device["overview_eta_command"] = before.get("overview_eta_command")
        else:
            device["overview_expected_finish_at"] = None
            device["overview_eta_observed_at"] = None
            device["overview_eta_command"] = None
    return devices


def current_expected_finish(
    device: dict[str, Any], now: datetime
) -> datetime | None:
    """Return a time-zone-aware ETA only while its snapshot is recent."""
    # This minute estimate may remain usable when only Overview is live,
    # even if DeviceGetInfo is temporarily unavailable.
    operation, _ = preferred_laundry_command(device, "0x50", now)
    source = device.get("overview_eta_command")
    if operation is not None and operation != 2:
        return None
    if operation is None:
        # The 15-minute ETA cache is a bounded holdover, not a new read.
        # Never expose that holdover after the device is unavailable.
        if device.get("available") is False:
            return None
        if source not in ("0x41", "device_get_info:0x41"):
            return None
    observed = _read_iso_time(device.get("overview_eta_observed_at"))
    eta = _read_iso_time(device.get("overview_expected_finish_at"))
    if not _is_recent(observed, now) or eta is None:
        return None
    return eta


def current_remaining_minutes(
    device: dict[str, Any], now: datetime
) -> int | None:
    """Estimate minutes until completion without treating empty polls as zero."""
    eta = current_expected_finish(device, now)
    if eta is None:
        return None
    seconds = (eta - now).total_seconds()
    # Round up whole minutes; a seconds-level timestamp shouldn't imply the
    # appliance has finished a full minute early.
    return max(0, int((seconds + 59.999999) // 60))
