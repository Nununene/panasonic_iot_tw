"""Washing machine device logic for Panasonic IoT TW integration."""
import logging
from typing import List, Optional

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.helpers.entity import EntityCategory
from homeassistant.components.sensor import SensorDeviceClass
from homeassistant.const import UnitOfTime

from ..base import BaseDevice, value_processors
from ..const import (
    DISPENSER_AMOUNT_LEVELS,
    LX128B_AVAILABLE_CYCLES,
    WASHING_MACHINE_AVAILABLE_STATUS,
    WASHING_MACHINE_AVAILABLE_CYCLES,
    WASHING_MACHINE_AVAILABLE_PHASES,
)

_LOGGER = logging.getLogger(__name__)


class WashingMachineDevice(BaseDevice):
    """Washing machine device logic."""

    # Device registers, ordered by command type. const.py's
    # DEVICE_STATUS_CODES decides which of them are polled.
    WASHING_REMAINING_TIME_COMMAND = "0x13"   # Washing remaining time (0-599 min)   Sensor
    DRYING_REMAINING_TIME_COMMAND = "0x05"    # Drying remaining time on combo models
    DRYING_ERROR_CODE_COMMAND = "0x0A"         # Dryer panel error code on combo models
    SCHEDULE_REMAINING_TIME_COMMAND = "0x15"  # Reservation remaining time (0-24 h)  Sensor
    ERROR_CODE_COMMAND = "0x19"               # Panel error code, 0 = no fault       Sensor
    ENGINEERING_INFO_COMMAND = "0x34"         # Stage bits 128/64/32/16              4x BinarySensor

    # Also gates the remaining-time sensors: they report nothing while idle,
    # because the register keeps the last programme's duration.
    OPERATION_STATUS_COMMAND = "0x50"         # Operation status, 8 = fault          Sensor

    CYCLE_MESSAGE_COMMAND = "0x55"            # Selected programme                   Sensor

    # Auto-dispenser state. Absent from the cloud's CommandList; the two flag
    # bytes track the tanks behind the vendor app's "please refill detergent"
    # and "please refill softener" notifications. The app splits the word into
    # two hex bytes and warns when a byte reads 1 (high byte = detergent,
    # low byte = softener), so 0x0101 means both tanks are low.
    # Evidence for the detergent byte: 0x0100 stayed set across three separate
    # low-detergent events (idle and running alike) and cleared the moment the
    # tank was refilled, including at an identical cycle stage (0x34=16),
    # which rules out the value merely following the programme. The softener
    # byte has not been observed low yet.
    DISPENSER_COMMAND = "0x71"                # Dispenser flags                      BinarySensor
    DETERGENT_LOW_BIT = 0x0100
    SOFTENER_LOW_BIT = 0x0001

    # Dispenser amounts. The two warnings above only mean something while the
    # matching slot is in use: the vendor app hides a tank's warning unless its
    # amount here is non-zero, so a machine that never uses a slot keeps that
    # warning byte at 0 forever. Gating on this keeps the sensors from claiming
    # a tank is fine when the tank is simply not in use.
    DISPENSER_SETTING_COMMAND = "0x72"        # Dispenser amounts                    Sensor

    REMOTE_CONTROL_COMMAND = "0x74"           # Remote control allowed               BinarySensor

    # Operation status (0x50) values for which a remaining time is meaningful
    RUNNING_STATUS = 2
    REMAINING_TIME_STATUSES = {1, 2, 3, 4}     # standby / running / reserved
    RESERVED_STATUSES = {3, 4}                 # reservation pending
    FINISHED_STATUS = 5                        # programme complete

    @property
    def washing_remaining_time(self) -> Optional[int]:
        """Return washing remaining time in minutes."""
        return self.get_int_status(self.WASHING_REMAINING_TIME_COMMAND)

    @property
    def schedule_remaining_time(self) -> Optional[int]:
        """Return schedule remaining time in hours."""
        return self.get_int_status(self.SCHEDULE_REMAINING_TIME_COMMAND)

    @property
    def operation_status(self) -> Optional[int]:
        """Return current operation status."""
        return self.get_int_status(self.OPERATION_STATUS_COMMAND)

    @property
    def cycle_message(self) -> Optional[int]:
        """Return current cycle message."""
        return self.get_int_status(self.CYCLE_MESSAGE_COMMAND)

    @property
    def is_remote_control_allowed(self) -> bool:
        """Return if remote control is allowed."""
        return self.get_boolean_status(self.REMOTE_CONTROL_COMMAND)
    
    def get_sensor_entities(self, coordinator) -> List:
        """Return model-specific sensors without altering other washers."""
        if str(self.model).upper() == "NA-LX128B":
            return self._get_lx128b_sensor_entities(coordinator)
        sensors = [
            # Time sensors with formatting
            self._create_sensor(
                coordinator,
                command_type=self.WASHING_REMAINING_TIME_COMMAND,
                name="洗衣殘時間",
                sensor_key="washing_remaining_time",
                device_class=SensorDeviceClass.DURATION,
                unit=UnitOfTime.MINUTES,
                value_processor=value_processors.safe_int,
                extra_state_processor=value_processors.create_time_formatter("minutes"),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=self.REMAINING_TIME_STATUSES,
                translation_key="washing_machine_washing_remaining_time"
            ),
            self._create_sensor(
                coordinator,
                command_type=self.SCHEDULE_REMAINING_TIME_COMMAND,
                name="預約殘時間",
                sensor_key="schedule_remaining_time",
                device_class=SensorDeviceClass.DURATION,
                unit=UnitOfTime.HOURS,
                value_processor=value_processors.safe_int,
                extra_state_processor=value_processors.create_time_formatter("hours"),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=self.RESERVED_STATUSES,
                translation_key="washing_machine_schedule_remaining_time"
            ),
            # Status sensors with mapping
            self._create_sensor(
                coordinator,
                command_type=self.OPERATION_STATUS_COMMAND,
                name="運轉情報",
                sensor_key="operation_status",
                device_class=SensorDeviceClass.ENUM,
                options=list(dict.fromkeys(WASHING_MACHINE_AVAILABLE_STATUS.values())),
                value_processor=value_processors.create_status_mapping_processor(WASHING_MACHINE_AVAILABLE_STATUS),
                translation_key="washing_machine_operation_status"
            ),
            self._create_sensor(
                coordinator,
                command_type=self.CYCLE_MESSAGE_COMMAND,
                name="行程別訊息",
                sensor_key="cycle_message",
                device_class=SensorDeviceClass.ENUM,
                options=list(dict.fromkeys(WASHING_MACHINE_AVAILABLE_CYCLES.values())),
                value_processor=value_processors.create_status_mapping_processor(WASHING_MACHINE_AVAILABLE_CYCLES),
                translation_key="washing_machine_cycle_message"
            ),
            # Which phase is running right now. Gated on the operation status:
            # while the machine sits idle the register still holds the whole
            # programme's bits, and reading a phase out of it would be wrong.
            self._create_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="目前階段",
                sensor_key="current_stage",
                device_class=SensorDeviceClass.ENUM,
                options=list(WASHING_MACHINE_AVAILABLE_PHASES.values()) + ["finished"],
                value_processor=value_processors.create_pending_phase_processor(
                    WASHING_MACHINE_AVAILABLE_PHASES
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_current_stage"
            ),
            # How much each dispenser slot is set to dispense. The word is the
            # sum of the app's two amounts, so the detergent side sits in the
            # high byte and the softener side in the low nibble; both are levels
            # (無/較少/標準/較多) rather than volumes.
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="洗劑投入量",
                sensor_key="detergent_amount",
                device_class=SensorDeviceClass.ENUM,
                options=list(DISPENSER_AMOUNT_LEVELS.values()),
                value_processor=value_processors.create_level_processor(
                    value_processors.dispenser_detergent_level, DISPENSER_AMOUNT_LEVELS
                ),
                translation_key="washing_machine_detergent_amount"
            ),
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="柔軟劑投入量",
                sensor_key="softener_amount",
                device_class=SensorDeviceClass.ENUM,
                options=list(DISPENSER_AMOUNT_LEVELS.values()),
                value_processor=value_processors.create_level_processor(
                    value_processors.dispenser_softener_level, DISPENSER_AMOUNT_LEVELS
                ),
                translation_key="washing_machine_softener_amount"
            ),
            # Undecoded dispenser-area registers, recorded so a future
            # "softener low" or maintenance alert can be traced to a bit.
            # 0x71 next to them is the confirmed detergent flag.
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="暫存器 0x72",
                sensor_key="register_72",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_72"
            ),
            self._create_sensor(
                coordinator,
                command_type="0x73",
                name="暫存器 0x73",
                sensor_key="register_73",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_73"
            ),
            self._create_sensor(
                coordinator,
                command_type="0x75",
                name="暫存器 0x75",
                sensor_key="register_75",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_75"
            ),
            self._create_sensor(
                coordinator,
                command_type=self.ERROR_CODE_COMMAND,
                name="錯誤代碼",
                sensor_key="error_code",
                value_processor=value_processors.process_error_code,
                extra_state_processor=value_processors.process_error_code_attributes,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_error_code"
            ),
            # Washer-dryer combo models expose dryer data through the same
            # Type 3 device entry. Keep these keys distinct from the Type 6
            # dryer entities so the existing washer entities remain unchanged.
            self._create_sensor(
                coordinator,
                command_type="0x41",
                name="乾衣殘時間",
                sensor_key="drying_remaining_time",
                device_class=SensorDeviceClass.DURATION,
                unit=UnitOfTime.MINUTES,
                data_source="overview_remaining",
                translation_key="washing_machine_drying_remaining_time"
            ),
            self._create_sensor(
                coordinator,
                command_type=self.DRYING_ERROR_CODE_COMMAND,
                name="乾衣錯誤代碼",
                sensor_key="drying_error_code",
                value_processor=value_processors.process_error_code,
                extra_state_processor=value_processors.process_error_code_attributes,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_drying_error_code"
            ),
            # Estimated finish is a real HA timestamp (not a local-time
            # formatted string); observed during NA-LX128B drying.
            self._create_sensor(
                coordinator,
                command_type="0x41",
                name="乾衣預計完成時間",
                sensor_key="drying_expected_finish",
                device_class=SensorDeviceClass.TIMESTAMP,
                data_source="overview_eta",
                translation_key="washing_machine_drying_expected_finish",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x41",
                name="雲端原始狀態 0x41",
                sensor_key="overview_41",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_overview_41",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x4F",
                name="雲端原始狀態 0x4F",
                sensor_key="overview_4f",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_overview_4f",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x50",
                name="雲端原始狀態 0x50",
                sensor_key="overview_50",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_overview_50",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x54",
                name="雲端原始狀態 0x54",
                sensor_key="overview_54",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_overview_54",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x20",
                name="雲端原始狀態 0x20",
                sensor_key="overview_20",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_overview_20",
            ),
        ]
        return sensors

    def _get_lx128b_sensor_entities(self, coordinator) -> List:
        """NA-LX128B: only verified, useful model-specific sensor entities.

        The Japanese LX128B uses 0x41 for programme minutes and 0x54
        for phase, not the legacy washer 0x13/0x34. Do not register
        duplicates or known-invalid legacy sensors for this model.
        Other Type 3 models continue through get_sensor_entities().
        """
        sensors = [
            self._create_sensor(
                coordinator,
                command_type="0x41",
                name="行程剩餘時間",
                sensor_key="programme_remaining_time",
                device_class=SensorDeviceClass.DURATION,
                unit=UnitOfTime.MINUTES,
                data_source="overview_remaining",
                translation_key="washing_machine_lx128b_programme_remaining_time",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x41",
                name="行程預計完成時間",
                sensor_key="programme_expected_finish",
                device_class=SensorDeviceClass.TIMESTAMP,
                data_source="overview_eta",
                translation_key="washing_machine_lx128b_programme_expected_finish",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x50",
                name="洗衣機運轉狀態",
                sensor_key="laundry_operation_state",
                data_source="laundry_operation_state",
                device_class=SensorDeviceClass.ENUM,
                options=[
                    "off", "standby", "running", "washing", "rinsing",
                    "spinning", "drying", "soft_keeping", "paused",
                ],
                icon="mdi:washing-machine",
                translation_key="washing_machine_laundry_operation_state",
            ),
            self._create_sensor(
                coordinator,
                command_type=self.CYCLE_MESSAGE_COMMAND,
                name="行程別訊息",
                sensor_key="cycle_message",
                device_class=SensorDeviceClass.ENUM,
                options=list(LX128B_AVAILABLE_CYCLES.values()),
                value_processor=value_processors.create_status_mapping_processor(
                    LX128B_AVAILABLE_CYCLES
                ),
                translation_key="washing_machine_lx128b_cycle_message",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x4F",
                name="nanoe殘時間",
                sensor_key="nanoe_remaining_time",
                data_source="laundry_nanoe_remaining",
                device_class=SensorDeviceClass.DURATION,
                unit=UnitOfTime.MINUTES,
                translation_key="washing_machine_lx128b_nanoe_remaining_time",
            ),
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="洗劑投入量",
                sensor_key="detergent_amount",
                device_class=SensorDeviceClass.ENUM,
                options=list(DISPENSER_AMOUNT_LEVELS.values()),
                value_processor=value_processors.create_level_processor(
                    value_processors.dispenser_detergent_level, DISPENSER_AMOUNT_LEVELS
                ),
                translation_key="washing_machine_detergent_amount",
            ),
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="柔軟劑投入量",
                sensor_key="softener_amount",
                device_class=SensorDeviceClass.ENUM,
                options=list(DISPENSER_AMOUNT_LEVELS.values()),
                value_processor=value_processors.create_level_processor(
                    value_processors.dispenser_softener_level, DISPENSER_AMOUNT_LEVELS
                ),
                translation_key="washing_machine_softener_amount",
            ),
            self._create_sensor(
                coordinator,
                command_type=self.DISPENSER_SETTING_COMMAND,
                name="暫存器 0x72",
                sensor_key="register_72",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_72",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x73",
                name="暫存器 0x73",
                sensor_key="register_73",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_73",
            ),
            self._create_sensor(
                coordinator,
                command_type="0x75",
                name="暫存器 0x75",
                sensor_key="register_75",
                value_processor=value_processors.raw_register_word,
                extra_state_processor=value_processors.process_status_flag_bits,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_register_75",
            ),
            self._create_sensor(
                coordinator,
                command_type=self.ERROR_CODE_COMMAND,
                name="錯誤代碼",
                sensor_key="error_code",
                value_processor=value_processors.process_error_code,
                extra_state_processor=value_processors.process_error_code_attributes,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_error_code",
            ),
        ]
        # Keep both raw cloud sources inspectable independently. These
        # diagnostics intentionally show Overview only, not merged values.
        for code in ("0x41", "0x4F", "0x50", "0x54", "0x20"):
            sensors.append(self._create_sensor(
                coordinator,
                command_type=code,
                name=f"雲端原始狀態 {code}",
                sensor_key=f"overview_{code[2:].lower()}",
                data_source="overview",
                value_processor=value_processors.safe_int,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key=f"washing_machine_overview_{code[2:].lower()}",
            ))
        return sensors

    def get_binary_sensor_entities(self, coordinator) -> List:
        """Select washer phase binaries only on models using 0x34.

        This retains the original entity creation order for legacy
        models, while NA-LX128B exposes only meaningful common alerts.
        """
        is_lx128b = str(self.model).upper() == "NA-LX128B"
        washer_phases = [] if is_lx128b else [
            # Engineering info 0x34 marks the phases that have NOT finished
            # yet, so these report the phase being run right now instead of
            # testing their own bit (which stayed set for the whole programme
            # and made every phase look active at once). Gated on the
            # operation status for the same reason as the stage sensor.
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 預洗",
                sensor_key="prewash_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "prewash"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_prewash_status",
                entity_registry_enabled_default=False
            ),
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 洗衣",
                sensor_key="washing_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "washing"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_washing_status",
                entity_registry_enabled_default=False
            ),
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 洗清",
                sensor_key="rinsing_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "rinsing"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_rinsing_status",
                entity_registry_enabled_default=False
            ),
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 脫水",
                sensor_key="spinning_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "spinning"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_spinning_status",
                entity_registry_enabled_default=False
            ),
        ]
        common = [
            # Detergent tank low (high byte of the dispenser register). Silent
            # while the detergent slot is not in use.
            self._create_binary_sensor(
                coordinator,
                command_type=self.DISPENSER_COMMAND,
                name="洗衣劑不足",
                sensor_key="detergent_low",
                device_class=BinarySensorDeviceClass.PROBLEM,
                bit_mask=self.DETERGENT_LOW_BIT,
                gate_command=self.DISPENSER_SETTING_COMMAND,
                gate_when=value_processors.dispenser_has_detergent,
                translation_key="washing_machine_detergent_low"
            ),
            # Softener tank low (low byte of the same register, which the app
            # decodes as a second warning flag; 0x0101 = both tanks low).
            # Silent while the softener slot is not in use, which is how the
            # vendor app behaves too.
            self._create_binary_sensor(
                coordinator,
                command_type=self.DISPENSER_COMMAND,
                name="柔軟劑不足",
                sensor_key="softener_low",
                device_class=BinarySensorDeviceClass.PROBLEM,
                bit_mask=self.SOFTENER_LOW_BIT,
                gate_command=self.DISPENSER_SETTING_COMMAND,
                gate_when=value_processors.dispenser_has_softener,
                translation_key="washing_machine_softener_low"
            ),
            # Remote control allowance
            self._create_binary_sensor(
                coordinator,
                command_type=self.REMOTE_CONTROL_COMMAND,
                name="遠端控制允許",
                sensor_key="remote_control_allowed",
                value_processor=value_processors.process_remote_control,
                entity_category=EntityCategory.DIAGNOSTIC,
                translation_key="washing_machine_remote_control_allowed"
            ),
        ]
        if is_lx128b:
            return common
        dryer_phases = [
            # Dryer phases share engineering register 0x34 with washer phases.
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 乾衣",
                sensor_key="drying_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "drying"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_drying_status",
                entity_registry_enabled_default=False
            ),
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 送風",
                sensor_key="air_flow_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "air_flow"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_air_flow_status",
                entity_registry_enabled_default=False
            ),
            self._create_binary_sensor(
                coordinator,
                command_type=self.ENGINEERING_INFO_COMMAND,
                name="進行中 - 鬆柔冷卻",
                sensor_key="soft_cooling_status",
                device_class=BinarySensorDeviceClass.RUNNING,
                value_processor=value_processors.create_current_phase_matcher(
                    WASHING_MACHINE_AVAILABLE_PHASES, "soft_cooling"
                ),
                gate_command=self.OPERATION_STATUS_COMMAND,
                gate_allowed=(self.RUNNING_STATUS, self.FINISHED_STATUS),
                translation_key="washing_machine_soft_cooling_status",
                entity_registry_enabled_default=False
            )
        ]
        return washer_phases + common + dryer_phases
