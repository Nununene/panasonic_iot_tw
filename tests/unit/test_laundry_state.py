"""Tests for tentative NA-LX128B programme-state interpretation.

Samples originate from the user's 2026-11-21 HA/API + official app observations.
The vendor has not documented the meanings of these registers.
"""
from datetime import datetime, timedelta, timezone

from custom_components.panasonic_iot_tw.laundry_state import (
    laundry_operation_state, laundry_state_attributes,
)

BASE = datetime(2026, 11, 21, 5, 24, 25, tzinfo=timezone.utc)


def _device(power, operation, stage=None, *, moment=BASE, model="NA-LX128B"):
    device = {
        "model": model,
        "status": {"0x01": str(power), "0x50": str(operation)},
        "overview_status": {},
        "overview_observed_at": {},
        "overview_last_response_summary": None,
        "overview_last_response_at": None,
    }
    if stage is not None:
        device["overview_status"] = {"0x50": 2, "0x54": stage}
        device["overview_observed_at"] = {
            "0x50": moment.isoformat(),
            "0x54": moment.isoformat(),
        }
        device["overview_last_response_at"] = moment.isoformat()
        device["overview_last_response_summary"] = {
            "result": "matched",
            "commands": {"0x50": "numeric", "0x54": "numeric"},
        }
    return device


def test_drying_inferred_only_from_synchronized_valid_stage_6():
    device = _device(1, 2, 6)
    assert laundry_operation_state(device, BASE) == "drying"
    assert laundry_state_attributes(device, BASE)["overview_phase_current"] is True


def test_spinning_phase_5_from_real_wash_and_dry_sample():
    """14:56:11 overview matched official app spinning at 14:59."""
    sample = _device(1, 2, 5)
    sample["status"]["0x74"] = "0"
    assert laundry_operation_state(sample, BASE) == "spinning"
    assert laundry_state_attributes(sample, BASE)["overview_phase_current"] is True

    # This should not claim spinning from an older cached 0x54=5
    sample["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(sample, BASE) == "running"


def test_anti_crease_holding_phase_11():
    device = _device(1, 2, 11)
    assert laundry_operation_state(device, BASE) == "soft_keeping"


def test_paused_register_7_precedes_stale_or_strange_stage_49601():
    device = _device(1, 7, 49601)
    assert laundry_operation_state(device, BASE) == "paused"


def test_power_off_register_01_2_overrides_stale_paused_operation():
    # Power-off read: DeviceGetInfo 0x01=2, 0x50=7 (still prior pause).
    device = _device(2, 7, 49601)
    assert laundry_operation_state(device, BASE) == "off"


def test_standby_after_power_on_ignores_cached_paused_stage():
    # Standby read: DeviceGetInfo 0x01=3, 0x50=1, while 0x54 still 49601.
    device = _device(3, 1, 49601)
    assert laundry_operation_state(device, BASE) == "standby"


def test_blank_latest_phase_response_ignores_cache_from_older_read():
    device = _device(1, 2, 11)
    next_poll = BASE + timedelta(minutes=3)
    device["overview_last_response_at"] = next_poll.isoformat()
    device["overview_last_response_summary"]["commands"]["0x54"] = "empty_string"
    assert laundry_operation_state(device, next_poll) == "running"
    assert not laundry_state_attributes(
        device, next_poll
    )["overview_phase_current"]


def test_phase_must_be_at_the_same_time_as_overview_operation():
    device = _device(1, 2, 11)
    device["overview_observed_at"]["0x54"] = (
        BASE - timedelta(minutes=1)
    ).isoformat()
    assert laundry_operation_state(device, BASE) == "running"


def test_unavailable_overview_summary_keeps_general_running():
    device = _device(1, 2, 6)
    device["overview_last_response_summary"] = None
    assert laundry_operation_state(device, BASE) == "running"


def test_old_cloud_sample_downgrades_to_general_running():
    device = _device(1, 2, 11)
    assert laundry_operation_state(
        device, BASE + timedelta(minutes=6)
    ) == "running"


def test_unknown_54_does_not_claim_drying():
    device = _device(1, 2, 49601)
    assert laundry_operation_state(device, BASE) == "running"


def test_unsupported_device_status_is_unknown_not_invented():
    device = _device(3, 8)
    assert laundry_operation_state(device, BASE) is None


def test_other_models_are_not_classified_using_na_lx128b_codes():
    device = _device(1, 2, 11, model="NA-OTHER")
    assert laundry_operation_state(device, BASE) is None


def test_fresh_phase_requires_numeric_overview_50():
    device = _device(1, 2, 11)
    device["overview_status"]["0x50"] = 7
    assert laundry_operation_state(device, BASE) == "running"


def test_power_off_from_observed_sample_even_when_cloud_all_blank():
    device = _device(2, 7)
    device["overview_last_response_summary"] = {
        "result": "matched",
        "commands": {"0x50": "empty_string", "0x54": "empty_string"},
    }
    assert laundry_operation_state(device, BASE) == "off"


def test_state_attributes_include_raw_age_with_no_private_gateway_id():
    device = _device(3, 1, 49601)
    attrs = laundry_state_attributes(device, BASE)
    assert attrs["device_get_info_0x01"] == "3"
    assert attrs["device_get_info_0x50"] == "1"
    assert attrs["overview_0x54"] == 49601
    assert attrs["overview_0x54_observed_at"] == BASE.isoformat()
    assert attrs["overview_phase_current"] is False
    assert "gwid" not in attrs
