from __future__ import annotations

import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from obi_energy_tracker import (
    Bridge,
    Device,
    DeviceKind,
    FirmwareUpdate,
    Measure,
    MeasureRecord,
    ObiEnergyTrackerApi,
    OtaStatus,
    OutletState,
)
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    DeviceData,
    ObiEnergyTrackerData,
)

TEST_ACCESS_TOKEN = "test-token-abcdefghijklmnop"


FAKE_OAUTH_TOKEN = {
    "access_token": TEST_ACCESS_TOKEN,
    "refresh_token": "fake-refresh-token",
    "token_type": "Bearer",
    "expires_in": 900,
    "expires_at": time.time() + 900,
    "scope": "openid email profile offline_access",
}


def make_oauth_config_data(token: dict | None = None) -> dict:
    return {
        "auth_implementation": DOMAIN,
        "token": token if token is not None else {**FAKE_OAUTH_TOKEN},
    }


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    yield


def make_sensor(
    id: str = "sensor-001",
    bridge_id: str = "bridge-001",
    display_name: str = "Test Sensor",
    firmware_version: str = "1.0.0",
    hardware_version: str = "2.0.0",
    battery_level: int | None = 85,
    is_online: bool = True,
) -> Device:
    return Device(
        id=id,
        bridge_id=bridge_id,
        kind=DeviceKind.SENSOR,
        display_name=display_name,
        firmware_version=firmware_version,
        hardware_version=hardware_version,
        is_online=is_online,
        battery_level=battery_level,
    )


def make_outlet(
    id: str = "outlet-001",
    bridge_id: str = "bridge-001",
    display_name: str = "Test Outlet",
    firmware_version: str = "12.0.0",
    hardware_version: str = "0.1.0",
    is_online: bool = True,
    state: OutletState | None = OutletState.OFF,
) -> Device:
    return Device(
        id=id,
        bridge_id=bridge_id,
        kind=DeviceKind.OUTLET,
        display_name=display_name,
        firmware_version=firmware_version,
        hardware_version=hardware_version,
        is_online=is_online,
        state=state,
    )


def make_bridge(
    id: str = "bridge-001",
    label: str | None = "Test Bridge",
    firmware_version: str = "1.0.0",
    hardware_version: str = "2.0.0",
    ota_status: OtaStatus | None = OtaStatus.IDLE,
    ota_progress: int | None = None,
    sensors: list[Device] | None = None,
    outlets: list[Device] | None = None,
) -> Bridge:
    return Bridge(
        id=id,
        display_name=f"OBI Bridge {label or id[:8]}",
        firmware_version=firmware_version,
        hardware_version=hardware_version,
        ota_status=ota_status,
        ota_progress=ota_progress,
        sensors=sensors if sensors is not None else [],
        outlets=outlets if outlets is not None else [],
    )


def make_firmware_update(
    id: str = "firmware-001",
    version: str = "1.1.0",
    change_log: str | None = "- Improved WiFi stability",
) -> FirmwareUpdate:
    return FirmwareUpdate(id=id, version=version, change_log=change_log)


def make_measure_record(
    time: datetime | None = None,
    value: float = 1234.5,
    measure: Measure = Measure.ENERGY,
) -> MeasureRecord:
    return MeasureRecord(
        time=time if time is not None else datetime.now(UTC),
        value=value,
        measure=measure,
    )


def make_device_data(
    device: Device | None = None,
    bridge: Bridge | None = None,
    energy: float | None = 1234.5,
    negative_energy: float = 0.0,
    rssi: float | None = -70.0,
    battery: float | None = None,
) -> DeviceData:
    device = device if device is not None else make_sensor()
    bridge = bridge if bridge is not None else make_bridge(sensors=[device])
    return DeviceData(
        device=device,
        bridge=bridge,
        energy=energy,
        negative_energy=negative_energy,
        rssi=rssi,
        battery=battery,
    )


def make_coordinator_data(
    devices: list[DeviceData] | None = None,
    bridges: list[Bridge] | None = None,
    firmware_updates: dict[str, FirmwareUpdate] | None = None,
) -> ObiEnergyTrackerData:
    if devices is None:
        devices = [make_device_data()]
    if bridges is None:
        seen: dict[str, Bridge] = {}
        for data in devices:
            seen.setdefault(data.bridge.id, data.bridge)
        bridges = list(seen.values())
    return ObiEnergyTrackerData(
        bridges=bridges,
        devices={data.device.id: data for data in devices},
        firmware_updates=firmware_updates if firmware_updates is not None else {},
    )


def make_bridge_api_response(
    id: str = "bridge-001",
    label: str | None = "Test Bridge",
    firmware_version: str = "1.0.0",
    hardware_version: str = "2.0.0",
    ota_status: str | None = "NOT_UPDATING",
    ota_progress: int | None = None,
    sensors: list[dict] | None = None,
    outlets: list[dict] | None = None,
) -> dict:
    return {
        "id": id,
        "label": label,
        "firmwareVersion": firmware_version,
        "hardwareVersion": hardware_version,
        "otaStatus": ota_status,
        "otaProgress": ota_progress,
        "sensors": sensors if sensors is not None else [],
        "outlets": outlets if outlets is not None else [],
    }


def make_mock_oauth_session(token: dict | None = None) -> MagicMock:
    session = MagicMock()
    session.token = token if token is not None else {**FAKE_OAUTH_TOKEN}
    session.async_ensure_token_valid = AsyncMock()
    session.valid_token = True
    return session


def make_mock_oauth_implementation() -> MagicMock:
    impl = MagicMock()
    impl.name = "OBI ENERGY TRACKER"
    impl.domain = DOMAIN
    impl.async_generate_authorize_url = AsyncMock(
        return_value="https://auth.test.com/authorize"
    )
    impl.async_resolve_external_data = AsyncMock(return_value={**FAKE_OAUTH_TOKEN})
    impl._async_refresh_token = AsyncMock(return_value={**FAKE_OAUTH_TOKEN})
    return impl


@pytest.fixture
def mock_responses(aioclient_mock):
    return aioclient_mock


@pytest.fixture
def mock_api() -> AsyncMock:
    sensor = make_sensor()
    bridge = make_bridge(sensors=[sensor])
    mock = AsyncMock(spec=ObiEnergyTrackerApi)
    mock.async_get_bridges.return_value = [bridge]
    mock.async_get_bridge_measures.return_value = {
        sensor.id: {
            Measure.ENERGY: [make_measure_record(value=1234.5)],
            Measure.RSSI: [make_measure_record(value=-70.0, measure=Measure.RSSI)],
            Measure.BATTERY: [make_measure_record(value=85.0, measure=Measure.BATTERY)],
        }
    }
    mock.async_get_meter_readings.return_value = {
        Measure.ENERGY: [make_measure_record(value=1234.5)]
    }
    mock.async_get_bridge_firmware_update.return_value = None
    return mock


@pytest.fixture
def mock_config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_oauth_config_data(),
        version=1,
    )
    entry.add_to_hass(hass)
    return entry
