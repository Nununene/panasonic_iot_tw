"""Unit tests for the PanasonicCoordinator."""
from datetime import datetime, timedelta

import pytest
from unittest.mock import AsyncMock

from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant.common import MockConfigEntry

from custom_components.panasonic_iot_tw.const import (
    CONF_UPDATE_INTERVAL,
    DOMAIN,
    TRANSIENT_FAILURE_TOLERANCE,
)
from custom_components.panasonic_iot_tw.coordinator import PanasonicCoordinator
from custom_components.panasonic_iot_tw.exceptions import (
    PanasonicLoginFailed,
    PanasonicTokenExpired,
)


def _make_entry(hass, options=None):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"username": "user@example.com", "password": "secret"},
        options=options or {CONF_UPDATE_INTERVAL: 120},
        version=2,
    )
    entry.add_to_hass(hass)
    return entry


async def test_update_interval_from_options(hass):
    """Coordinator reads its update interval from entry options."""
    entry = _make_entry(hass, options={CONF_UPDATE_INTERVAL: 300})
    smart_app = AsyncMock()
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    assert coordinator.update_interval.total_seconds() == 300
    assert coordinator.smart_app is smart_app


async def test_update_success(hass):
    """Coordinator returns device data keyed by device_id on success."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.return_value = {
        "dev-1": {"device_id": "dev-1", "available": True, "status": {}},
    }
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    data = await coordinator._async_update_data()

    assert "dev-1" in data


def _freeze(monkeypatch, when):
    import custom_components.panasonic_iot_tw.coordinator as coord_mod

    class _FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr(coord_mod, "datetime", _FakeDatetime)


@pytest.mark.parametrize("exc", [PanasonicLoginFailed, PanasonicTokenExpired])
async def test_single_auth_error_retries_as_update_failed(hass, exc, monkeypatch):
    """A first auth failure is retried, never instantly escalated to reauth."""
    _freeze(monkeypatch, datetime(2026, 7, 17, 12, 0, 0))
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.side_effect = exc("boom")
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_persistent_auth_error_raises_auth_failed(hass, monkeypatch):
    """Auth failing 3+ times over 30+ minutes in the daytime triggers reauth."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.side_effect = PanasonicLoginFailed("boom")
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    for minute in (0, 15):
        _freeze(monkeypatch, datetime(2026, 7, 17, 12, minute, 0))
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    _freeze(monkeypatch, datetime(2026, 7, 17, 12, 31, 0))
    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_auth_error_never_escalates_in_maintenance_window(hass, monkeypatch):
    """Persistent auth failures during 00:00-08:00 keep retrying."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.side_effect = PanasonicLoginFailed("boom")
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    for minute in (0, 15, 31, 45):
        _freeze(monkeypatch, datetime(2026, 7, 17, 0, minute, 0))
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()


async def test_success_resets_auth_failure_tracking(hass, monkeypatch):
    """A successful update clears the auth failure counters."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    for minute in (0, 15):
        _freeze(monkeypatch, datetime(2026, 7, 17, 12, minute, 0))
        smart_app.get_device_with_info.side_effect = PanasonicLoginFailed("boom")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    smart_app.get_device_with_info.side_effect = None
    smart_app.get_device_with_info.return_value = {"dev-1": {"available": True}}
    _freeze(monkeypatch, datetime(2026, 7, 17, 12, 20, 0))
    await coordinator._async_update_data()

    _freeze(monkeypatch, datetime(2026, 7, 17, 12, 40, 0))
    smart_app.get_device_with_info.side_effect = PanasonicLoginFailed("boom")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_other_error_raises_update_failed(hass):
    """Generic communication failures map to UpdateFailed."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.side_effect = RuntimeError("network down")
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_short_outage_keeps_last_known_state(hass):
    """A couple of failed polls serve the last good data; the next one fails."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    good = {"dev-1": {"device_id": "dev-1", "available": True, "status": {}}}
    smart_app.get_device_with_info.return_value = good
    coordinator = PanasonicCoordinator(hass, entry, smart_app)
    coordinator.data = await coordinator._async_update_data()

    smart_app.get_device_with_info.side_effect = RuntimeError("SQL timeout")
    for _ in range(TRANSIENT_FAILURE_TOLERANCE):
        assert await coordinator._async_update_data() == good
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


async def test_recovery_resets_outage_tolerance(hass):
    """A good poll in between restarts the count of tolerated failures."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    good = {"dev-1": {"device_id": "dev-1", "available": True, "status": {}}}
    smart_app.get_device_with_info.return_value = good
    coordinator = PanasonicCoordinator(hass, entry, smart_app)
    coordinator.data = await coordinator._async_update_data()

    for _ in range(3):
        smart_app.get_device_with_info.side_effect = RuntimeError("blip")
        for _ in range(TRANSIENT_FAILURE_TOLERANCE):
            assert await coordinator._async_update_data() == good
        smart_app.get_device_with_info.side_effect = None
        assert await coordinator._async_update_data() == good



async def test_laundry_overview_preserves_partial_status_and_throttles(hass, monkeypatch):
    """Only the missing overview fields stay cached when others update."""
    from datetime import timezone

    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.return_value = {
        "washer-1": {
            "device_id": "washer-1", "device_type": 3, "gwid": "gw-1",
            "status": {"0x50": "2"}, "available": True,
        },
        "ac-1": {
            "device_id": "ac-1", "device_type": 1, "gwid": "gw-2",
            "status": {"0x50": "2"}, "available": True,
        },
    }
    first = datetime(2026, 11, 21, 4, 34, tzinfo=timezone.utc)
    import custom_components.panasonic_iot_tw.coordinator as coord_mod

    class FakeDateTime(datetime):
        now_value = first

        @classmethod
        def now(cls, tz=None):
            return cls.now_value

    monkeypatch.setattr(coord_mod, "datetime", FakeDateTime)

    def payload(values):
        return {
            "GwList": [{
                "GWID": "gw-1",
                "List": [
                    {"CommandType": key, "Status": value}
                    for key, value in values.items()
                ],
            }]
        }

    smart_app.get_laundry_overview.side_effect = [
        payload({
            "0x41": "111", "0x4F": "111", "0x50": "2",
            "0x54": "6", "0x20": "66",
        }),
        payload({
            "0x41": "", "0x4F": "", "0x50": "2",
            "0x54": "6", "0x20": "",
        }),
    ]
    coordinator = PanasonicCoordinator(hass, entry, smart_app)
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data["washer-1"]["overview_status"]["0x41"] == 111
    assert coordinator.data["washer-1"]["overview_last_fetch_result"] == "success"
    assert coordinator.data["washer-1"]["overview_last_response_summary"][
        "commands"
    ]["0x41"] == "numeric"
    assert "overview_status" not in coordinator.data["ac-1"]

    # One additional normal coordinator update before 3 minutes must NOT
    # cause an extra cloud request to the overview endpoint.
    FakeDateTime.now_value = first + timedelta(minutes=1)
    coordinator.data = await coordinator._async_update_data()
    assert smart_app.get_laundry_overview.await_count == 1
    assert coordinator.data["washer-1"]["overview_last_response_summary"][
        "commands"
    ]["0x41"] == "numeric"

    FakeDateTime.now_value = first + timedelta(minutes=3)
    coordinator.data = await coordinator._async_update_data()
    current = coordinator.data["washer-1"]
    assert current["overview_status"]["0x41"] == 111
    assert current["overview_status"]["0x20"] == 66
    assert current["overview_status"]["0x50"] == 2
    assert current["overview_observed_at"]["0x41"] == first.isoformat()
    assert current["overview_observed_at"]["0x50"] == (
        first + timedelta(minutes=3)
    ).isoformat()
    assert smart_app.get_laundry_overview.await_count == 2
    assert current["overview_last_response_summary"]["commands"] == {
        "0x41": "empty_string",
        "0x4F": "empty_string",
        "0x50": "numeric",
        "0x54": "numeric",
        "0x20": "empty_string",
    }
    assert current["overview_last_response_at"] == (
        first + timedelta(minutes=3)
    ).isoformat()


async def test_overview_api_failure_does_not_break_other_devices(hass):
    """A cloud overview error cannot bring down other HA entities."""
    entry = _make_entry(hass)
    smart_app = AsyncMock()
    smart_app.get_device_with_info.return_value = {
        "washer": {
            "device_type": 3, "gwid": "gw-1",
            "status": {"0x50": 2}, "available": True,
        }
    }
    smart_app.get_laundry_overview.side_effect = RuntimeError(
        "gateway token info should never be logged verbatim"
    )
    coordinator = PanasonicCoordinator(hass, entry, smart_app)

    data = await coordinator._async_update_data()

    assert data["washer"]["available"] is True
    assert data["washer"]["overview_status"] == {}
    assert data["washer"]["overview_expected_finish_at"] is None
    assert data["washer"]["overview_last_fetch_result"] == "error"
    assert data["washer"]["overview_last_response_summary"] is None



async def test_missing_active_laundry_eta_retries_at_bounded_interval(
    hass, monkeypatch
):
    """Partial blank first response should not stay unknown for 3 full minutes."""
    from datetime import timezone

    entry = _make_entry(hass, options={CONF_UPDATE_INTERVAL: 60})
    smart_app = AsyncMock()
    smart_app.get_device_with_info.return_value = {
        "washer": {
            "device_type": 3,
            "gwid": "gw-1",
            "status": {"0x50": "2"},
            "available": True,
        }
    }

    import custom_components.panasonic_iot_tw.coordinator as coord_mod

    beginning = datetime(2026, 11, 21, 4, 52, tzinfo=timezone.utc)

    class FakeDatetime(datetime):
        now_value = beginning

        @classmethod
        def now(cls, tz=None):
            return cls.now_value

    monkeypatch.setattr(coord_mod, "datetime", FakeDatetime)
    smart_app.get_laundry_overview.side_effect = [
        {"GwList": [{"GWID": "gw-1", "List": [
            {"CommandType": "0x41", "Status": ""},
            {"CommandType": "0x4F", "Status": ""},
            {"CommandType": "0x50", "Status": "2"},
            {"CommandType": "0x54", "Status": "6"},
            {"CommandType": "0x20", "Status": ""},
        ]}]},
        {"GwList": [{"GWID": "gw-1", "List": [
            {"CommandType": "0x41", "Status": "105"},
            {"CommandType": "0x4F", "Status": "105"},
            {"CommandType": "0x50", "Status": "2"},
        ]}]},
        {"GwList": [{"GWID": "gw-1", "List": [
            {"CommandType": "0x50", "Status": "2"},
        ]}]},
    ]
    coordinator = PanasonicCoordinator(hass, entry, smart_app)
    coordinator.data = await coordinator._async_update_data()
    assert coordinator.data["washer"]["overview_expected_finish_at"] is None
    assert smart_app.get_laundry_overview.await_count == 1

    FakeDatetime.now_value = beginning + timedelta(seconds=30)
    coordinator.data = await coordinator._async_update_data()
    assert smart_app.get_laundry_overview.await_count == 1

    FakeDatetime.now_value = beginning + timedelta(minutes=1)
    coordinator.data = await coordinator._async_update_data()
    assert smart_app.get_laundry_overview.await_count == 2
    assert coordinator.data["washer"]["overview_eta_command"] == "0x41"
    assert coordinator.data["washer"]["overview_status"]["0x54"] == 6

    # A fresh ETA restores the gentler normal 3-minute cadence.
    FakeDatetime.now_value = beginning + timedelta(minutes=2)
    coordinator.data = await coordinator._async_update_data()
    assert smart_app.get_laundry_overview.await_count == 2

    FakeDatetime.now_value = beginning + timedelta(minutes=4)
    coordinator.data = await coordinator._async_update_data()
    assert smart_app.get_laundry_overview.await_count == 3
