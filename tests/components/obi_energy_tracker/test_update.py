from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON, EntityCategory
from homeassistant.exceptions import HomeAssistantError
from obi_energy_tracker import (
    UNKNOWN_VERSION,
    FirmwareUpdate,
    ObiEnergyTrackerError,
    OtaStatus,
)

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    ObiEnergyTrackerCoordinator,
    ObiEnergyTrackerData,
)
from custom_components.obi_energy_tracker.update import (
    FIRMWARE_DESCRIPTION,
    ObiBridgeUpdate,
    async_setup_entry,
)

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_firmware_update,
    make_sensor,
)

BRIDGE_ID = "bridge-1"


def _make_data(
    installed: str = "1.0.0",
    firmware_update: FirmwareUpdate | None = None,
    ota_status: OtaStatus | None = OtaStatus.IDLE,
    ota_progress: int | None = None,
    bridges: int = 1,
):
    all_bridges = []
    devices = []
    for index in range(bridges):
        bridge_id = BRIDGE_ID if index == 0 else f"{BRIDGE_ID}-{index}"
        sensor = make_sensor(id=f"s-{index}", bridge_id=bridge_id)
        bridge = make_bridge(
            id=bridge_id,
            firmware_version=installed,
            ota_status=ota_status,
            ota_progress=ota_progress,
            sensors=[sensor],
        )
        all_bridges.append(bridge)
        devices.append(make_device_data(device=sensor, bridge=bridge))
    updates = {} if firmware_update is None else {BRIDGE_ID: firmware_update}
    return make_coordinator_data(
        devices=devices, bridges=all_bridges, firmware_updates=updates
    )


def _make_coordinator_mock(data: ObiEnergyTrackerData | None = None) -> MagicMock:
    mock = MagicMock(spec=ObiEnergyTrackerCoordinator)
    mock.async_add_listener = MagicMock(return_value=lambda: None)
    mock.last_update_success = True
    mock.data = data
    mock.async_install_bridge_firmware = AsyncMock()
    return mock


def _make_update(coord: MagicMock) -> ObiBridgeUpdate:
    bridge = coord.data.bridges[0]
    entity = ObiBridgeUpdate(coord, bridge, FIRMWARE_DESCRIPTION)
    entity.async_write_ha_state = MagicMock()
    return entity


class TestDescription:
    def test_firmware_device_class(self):
        assert FIRMWARE_DESCRIPTION.device_class == UpdateDeviceClass.FIRMWARE

    def test_translation_key(self):
        assert FIRMWARE_DESCRIPTION.translation_key == "firmware"

    def test_config_category(self):
        assert FIRMWARE_DESCRIPTION.entity_category == EntityCategory.CONFIG

    def test_supports_install_and_progress(self):
        coord = _make_coordinator_mock(_make_data())

        features = _make_update(coord).supported_features

        assert UpdateEntityFeature.INSTALL in features
        assert UpdateEntityFeature.PROGRESS in features

    def test_does_not_support_specific_version_or_backup(self):
        coord = _make_coordinator_mock(_make_data())

        features = _make_update(coord).supported_features

        assert UpdateEntityFeature.SPECIFIC_VERSION not in features
        assert UpdateEntityFeature.BACKUP not in features


class TestEntityMetadata:
    def test_unique_id(self):
        coord = _make_coordinator_mock(_make_data())

        assert _make_update(coord).unique_id == f"{BRIDGE_ID}_firmware"

    def test_belongs_to_the_bridge_device(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _make_update(coord)

        assert entity.device_info == {"identifiers": {(DOMAIN, BRIDGE_ID)}}


class TestVersions:
    def test_installed_version_from_bridge(self):
        coord = _make_coordinator_mock(_make_data(installed="1.2.3"))

        assert _make_update(coord).installed_version == "1.2.3"

    def test_latest_version_falls_back_to_installed(self):
        coord = _make_coordinator_mock(_make_data(installed="1.2.3"))
        entity = _make_update(coord)

        assert entity.latest_version == "1.2.3"
        assert entity.state == STATE_OFF

    def test_latest_version_from_firmware_update(self):
        coord = _make_coordinator_mock(
            _make_data(
                installed="1.0.0", firmware_update=make_firmware_update(version="1.1.0")
            )
        )
        entity = _make_update(coord)

        assert entity.latest_version == "1.1.0"
        assert entity.state == STATE_ON

    def test_unknown_installed_version_with_update_available(self):
        coord = _make_coordinator_mock(
            _make_data(
                installed=UNKNOWN_VERSION,
                firmware_update=make_firmware_update(version="1.1.0"),
            )
        )
        entity = _make_update(coord)

        assert entity.installed_version == UNKNOWN_VERSION
        assert entity.state == STATE_ON

    def test_release_summary_is_the_change_log(self):
        coord = _make_coordinator_mock(
            _make_data(firmware_update=make_firmware_update(change_log="- Fixed it"))
        )

        assert _make_update(coord).release_summary == "- Fixed it"

    def test_no_release_summary_without_update(self):
        coord = _make_coordinator_mock(_make_data())

        assert _make_update(coord).release_summary is None

    def test_no_release_summary_when_change_log_is_missing(self):
        coord = _make_coordinator_mock(
            _make_data(firmware_update=make_firmware_update(change_log=None))
        )

        assert _make_update(coord).release_summary is None

    def test_none_when_bridge_gone(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        entity = _make_update(coord)

        coord.data = ObiEnergyTrackerData(bridges=[], devices={})

        assert entity.installed_version is None
        assert entity.latest_version is None
        assert entity.available is False


class TestProgress:
    @pytest.mark.parametrize(
        "status", [OtaStatus.STARTED, OtaStatus.DOWNLOADING, OtaStatus.INSTALLING]
    )
    def test_in_progress_while_updating(self, status: OtaStatus):
        coord = _make_coordinator_mock(_make_data(ota_status=status))

        assert _make_update(coord).in_progress is True

    @pytest.mark.parametrize(
        "status",
        [OtaStatus.IDLE, OtaStatus.COMPLETE, OtaStatus.FAILED, None],
    )
    def test_not_in_progress_otherwise(self, status: OtaStatus | None):
        coord = _make_coordinator_mock(_make_data(ota_status=status))

        assert _make_update(coord).in_progress is False

    def test_percentage_from_bridge(self):
        coord = _make_coordinator_mock(
            _make_data(ota_status=OtaStatus.INSTALLING, ota_progress=72)
        )

        assert _make_update(coord).update_percentage == 72

    def test_percentage_reported_in_state_attributes(self):
        coord = _make_coordinator_mock(
            _make_data(
                firmware_update=make_firmware_update(),
                ota_status=OtaStatus.INSTALLING,
                ota_progress=72,
            )
        )

        attributes = _make_update(coord).state_attributes

        assert attributes["in_progress"] is True
        assert attributes["update_percentage"] == 72

    def test_percentage_hidden_when_not_updating(self):
        coord = _make_coordinator_mock(
            _make_data(ota_status=OtaStatus.IDLE, ota_progress=72)
        )

        attributes = _make_update(coord).state_attributes

        assert attributes["in_progress"] is False
        assert attributes["update_percentage"] is None

    def test_no_percentage_when_bridge_gone(self):
        data = _make_data(ota_progress=72)
        coord = _make_coordinator_mock(data)
        entity = _make_update(coord)

        coord.data = ObiEnergyTrackerData(bridges=[], devices={})

        assert entity.update_percentage is None
        assert entity.in_progress is False


class TestInstall:
    async def test_install_triggers_the_available_firmware(self):
        coord = _make_coordinator_mock(
            _make_data(firmware_update=make_firmware_update(id="fw-42"))
        )
        entity = _make_update(coord)

        await entity.async_install(version=None, backup=False)

        coord.async_install_bridge_firmware.assert_awaited_once_with(BRIDGE_ID, "fw-42")

    async def test_install_without_update_raises(self):
        coord = _make_coordinator_mock(_make_data())
        entity = _make_update(coord)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_install(version=None, backup=False)

        assert err.value.translation_domain == DOMAIN
        assert err.value.translation_key == "firmware_update_unavailable"
        coord.async_install_bridge_firmware.assert_not_awaited()

    async def test_api_error_becomes_home_assistant_error(self):
        coord = _make_coordinator_mock(
            _make_data(firmware_update=make_firmware_update())
        )
        coord.async_install_bridge_firmware.side_effect = ObiEnergyTrackerError("boom")
        entity = _make_update(coord)

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_install(version=None, backup=False)

        assert err.value.translation_key == "firmware_update_failed"
        assert err.value.translation_placeholders["error"] == "boom"

    async def test_error_names_the_bridge(self):
        data = _make_data(firmware_update=make_firmware_update())
        coord = _make_coordinator_mock(data)
        entity = _make_update(coord)
        coord.async_install_bridge_firmware.side_effect = ObiEnergyTrackerError("boom")

        with pytest.raises(HomeAssistantError) as err:
            await entity.async_install(version=None, backup=False)

        assert (
            err.value.translation_placeholders["name"] == data.bridges[0].display_name
        )


class TestPlatformSetup:
    async def _setup(self, coord: MagicMock) -> MagicMock:
        hass_mock = MagicMock()
        entry_mock = MagicMock()
        entry_mock.entry_id = "entry-1"
        entry_mock.runtime_data = coord
        add_entities = MagicMock()
        await async_setup_entry(hass_mock, entry_mock, add_entities)
        return add_entities

    async def test_one_entity_per_bridge(self):
        coord = _make_coordinator_mock(_make_data(bridges=2))

        add_entities = await self._setup(coord)

        entities = add_entities.call_args[0][0]
        assert {e.unique_id for e in entities} == {
            f"{BRIDGE_ID}_firmware",
            f"{BRIDGE_ID}-1_firmware",
        }

    async def test_entity_exists_without_an_update_available(self):
        coord = _make_coordinator_mock(_make_data())

        add_entities = await self._setup(coord)

        entities = add_entities.call_args[0][0]
        assert [e.unique_id for e in entities] == [f"{BRIDGE_ID}_firmware"]

    async def test_no_bridges_no_entities(self):
        coord = _make_coordinator_mock(ObiEnergyTrackerData())

        add_entities = await self._setup(coord)

        add_entities.assert_not_called()

    async def test_new_bridge_added_dynamically(self):
        data = _make_data()
        coord = _make_coordinator_mock(data)
        add_entities = await self._setup(coord)
        listener = coord.async_add_listener.call_args[0][0]

        new_bridge = make_bridge(id="bridge-new")
        coord.data = make_coordinator_data(
            devices=list(data.devices.values()),
            bridges=[*data.bridges, new_bridge],
        )

        listener()

        entities = add_entities.call_args[0][0]
        assert [e.unique_id for e in entities] == ["bridge-new_firmware"]
