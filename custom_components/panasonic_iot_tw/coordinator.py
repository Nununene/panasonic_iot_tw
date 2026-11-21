"""Data update coordinator for the Panasonic IoT TW integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_UPDATE_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_UPDATE_INTERVAL,
    DEVICE_STATUS_CODES,
    TRANSIENT_FAILURE_TOLERANCE,
)
from .exceptions import (
    PanasonicLoginFailed,
    PanasonicRefreshTokenNotFound,
    PanasonicTokenExpired,
)
from .laundry_overview import (
    OVERVIEW_POLL_INTERVAL,
    OVERVIEW_MISSING_ETA_RETRY_INTERVAL,
    attach_laundry_overview,
    current_expected_finish,
    summarize_overview_response,
)
from .services import SmartApp

_LOGGER = logging.getLogger(__name__)

PanasonicConfigEntry = ConfigEntry["PanasonicCoordinator"]

# The Panasonic cloud (including its auth endpoints) is unreliable between
# 00:00 and 08:00 local time — the backend's day runs 8 hours ahead. A single
# failed login at night must not be treated as bad credentials: HA never
# retries after ConfigEntryAuthFailed, so a transient blip would disable the
# integration until the user manually reauthenticates. Only escalate to
# reauth when auth keeps failing persistently outside that window.
AUTH_MAINTENANCE_END_HOUR = 8
AUTH_FAIL_MIN_COUNT = 3
AUTH_FAIL_MIN_DURATION = timedelta(minutes=30)


class PanasonicCoordinator(DataUpdateCoordinator[Dict[str, Dict[str, Any]]]):
    """Coordinator that owns the SmartApp client and polls device state."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: PanasonicConfigEntry,
        smart_app: SmartApp,
    ) -> None:
        """Initialize the coordinator."""
        self.smart_app = smart_app
        self._auth_failure_count = 0
        self._first_auth_failure: Optional[datetime] = None
        self._consecutive_failures = 0
        self._last_overview_attempt: Optional[datetime] = None
        update_interval = entry.options.get(
            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DEFAULT_NAME,
            update_interval=timedelta(seconds=update_interval),
        )

    def _classify_auth_failure(self, err: Exception) -> Exception:
        """Decide whether an auth error warrants reauth or a plain retry."""
        now = datetime.now()
        self._auth_failure_count += 1
        if self._first_auth_failure is None:
            self._first_auth_failure = now

        persistent = (
            self._auth_failure_count >= AUTH_FAIL_MIN_COUNT
            and now - self._first_auth_failure >= AUTH_FAIL_MIN_DURATION
        )
        in_maintenance_window = now.hour < AUTH_MAINTENANCE_END_HOUR

        if persistent and not in_maintenance_window:
            return ConfigEntryAuthFailed(
                f"Authentication with Panasonic service failed: {err}"
            )

        _LOGGER.warning(
            "Authentication error (attempt %d%s), will retry: %s",
            self._auth_failure_count,
            " during nightly maintenance window" if in_maintenance_window else "",
            err,
        )
        return UpdateFailed(f"Authentication error, retrying: {err}")

    async def _async_update_data(self) -> Dict[str, Dict[str, Any]]:
        """Fetch the latest device state from the Panasonic service."""
        _LOGGER.debug("Updating device info...")
        try:
            data = await self.smart_app.get_device_with_info(DEVICE_STATUS_CODES)
        except (
            PanasonicLoginFailed,
            PanasonicTokenExpired,
            PanasonicRefreshTokenNotFound,
        ) as err:
            failure = self._classify_auth_failure(err)
            if isinstance(failure, ConfigEntryAuthFailed):
                raise failure from err
            return self._last_good_or_raise(failure, err)
        except Exception as err:
            return self._last_good_or_raise(
                UpdateFailed(f"Error communicating with Panasonic service: {err}"),
                err,
            )

        # The account-level overview is the only endpoint observed to carry
        # valid NA-LX128B drying minutes. Read only for Type 3 devices, no more
        # often than every 3 minutes, and do not fail normal polling if this
        # supplementary endpoint is empty or temporarily inaccessible.
        now = datetime.now(timezone.utc)
        if any(device.get("device_type") == 3 for device in data.values()):
            overview_payload = None
            # First reading after HA restart often has only 0x50 and 0x54.
            # Temporarily try the next *normal coordinator cycle* once at
            # least 1 minute has passed, but use the normal 3-minute cadence
            # whenever there is already a fresh ETA. No sleep or retry loop.
            running_without_eta = any(
                device.get("device_type") == 3
                and str(device.get("status", {}).get("0x50")) == "2"
                and current_expected_finish(
                    (self.data or {}).get(device_key, {}), now
                ) is None
                for device_key, device in data.items()
            )
            overview_interval = (
                OVERVIEW_MISSING_ETA_RETRY_INTERVAL
                if running_without_eta
                else OVERVIEW_POLL_INTERVAL
            )
            due = (
                self._last_overview_attempt is None
                or now - self._last_overview_attempt >= overview_interval
            )
            overview_fetch_result = None
            if due:
                self._last_overview_attempt = now
                try:
                    overview_payload = await self.smart_app.get_laundry_overview()
                    overview_fetch_result = "success"
                except Exception as exc:
                    overview_fetch_result = "error"
                    # Avoid logging exception text: cloud responses may include
                    # auth tokens or private gateway IDs.
                    _LOGGER.warning(
                        "Laundry overview read failed (%s); keeping recent values",
                        type(exc).__name__,
                    )
            attach_laundry_overview(data, self.data, overview_payload, now)
            last_request = (
                self._last_overview_attempt.isoformat()
                if self._last_overview_attempt else None
            )
            for device_key, device in data.items():
                if device.get("device_type") != 3:
                    continue
                before = (self.data or {}).get(device_key, {})
                device["overview_last_request_at"] = last_request
                device["overview_last_fetch_result"] = (
                    overview_fetch_result
                    if overview_fetch_result is not None
                    else before.get("overview_last_fetch_result")
                )
                # Only a successful request replaces the diagnostic summary.
                # A skipped poll or API error must not make the previous
                # response appear to have reported missing command codes.
                if overview_fetch_result == "success":
                    device["overview_last_response_at"] = now.isoformat()
                    device["overview_last_response_summary"] = (
                        summarize_overview_response(
                            overview_payload, device.get("gwid")
                        )
                    )
                else:
                    device["overview_last_response_at"] = before.get(
                        "overview_last_response_at"
                    )
                    device["overview_last_response_summary"] = before.get(
                        "overview_last_response_summary"
                    )

        self._consecutive_failures = 0
        self._auth_failure_count = 0
        self._first_auth_failure = None
        _LOGGER.debug("Coordinator received data with %d devices", len(data))
        return data

    def _last_good_or_raise(
        self, failure: UpdateFailed, err: Exception
    ) -> Dict[str, Dict[str, Any]]:
        """Bridge a short outage with the last good data, else raise."""
        self._consecutive_failures += 1
        if self.data and self._consecutive_failures <= TRANSIENT_FAILURE_TOLERANCE:
            _LOGGER.warning(
                "Update failed (%d in a row), keeping last known state: %s",
                self._consecutive_failures,
                err,
            )
            return self.data
        raise failure from err
