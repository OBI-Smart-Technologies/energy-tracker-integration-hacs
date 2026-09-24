from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.core import HomeAssistant
from obi_energy_tracker import OtaStatus
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    ObiEnergyTrackerCoordinator,
)
from custom_components.obi_energy_tracker.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import (
    make_bridge,
    make_coordinator_data,
    make_device_data,
    make_firmware_update,
    make_oauth_config_data,
    make_outlet,
    make_sensor,
)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=make_oauth_config_data(), version=1)
    entry.add_to_hass(hass)
    return entry


def _coordinator(data) -> MagicMock:
    coordinator = MagicMock(spec=ObiEnergyTrackerCoordinator)
    coordinator.data = data
    coordinator.last_update_success = True
    coordinator.update_interval = None
    return coordinator


def _full_data():
    sensor = make_sensor(id="sensor-001")
    outlet = make_outlet(id="outlet-001")
    bridge = make_bridge(
        id="bridge-001",
        ota_status=OtaStatus.INSTALLING,
        ota_progress=72,
        sensors=[sensor],
        outlets=[outlet],
    )
    return make_coordinator_data(
        devices=[
            make_device_data(device=sensor, bridge=bridge, energy=1000.0),
            make_device_data(device=outlet, bridge=bridge, energy=50.0),
        ],
        bridges=[bridge],
        firmware_updates={"bridge-001": make_firmware_update(id="fw-1")},
    )


class TestRedaction:
    async def test_token_is_redacted(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["entry"]["data"]["token"] == "**REDACTED**"

    async def test_auth_implementation_is_redacted(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["entry"]["data"]["auth_implementation"] == "**REDACTED**"

    async def test_no_access_token_leaks(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert "test-token-abcdefghijklmnop" not in str(result)

    async def test_display_names_are_redacted(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["bridges"][0]["display_name"] == "**REDACTED**"
        assert result["devices"]["sensor-001"]["device"]["display_name"] == (
            "**REDACTED**"
        )


class TestContent:
    async def test_bridges_listed_with_children(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["bridges"][0]["id"] == "bridge-001"
        assert result["bridges"][0]["sensor_ids"] == ["sensor-001"]
        assert result["bridges"][0]["outlet_ids"] == ["outlet-001"]

    async def test_device_measurements_included(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        sensor = result["devices"]["sensor-001"]
        assert sensor["energy"] == 1000.0
        assert sensor["bridge_id"] == "bridge-001"
        assert sensor["device"]["kind"] == "sensor"

    async def test_coordinator_state_reported(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["last_update_success"] is True

    async def test_ota_state_included(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["bridges"][0]["ota_status"] is OtaStatus.INSTALLING
        assert result["bridges"][0]["ota_progress"] == 72

    async def test_pending_firmware_update_included(self, hass: HomeAssistant) -> None:
        entry = _entry(hass)
        entry.runtime_data = _coordinator(_full_data())

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["bridges"][0]["firmware_update"]["id"] == "fw-1"

    async def test_no_firmware_update_is_none(self, hass: HomeAssistant) -> None:
        bridge = make_bridge(id="bridge-001")
        entry = _entry(hass)
        entry.runtime_data = _coordinator(
            make_coordinator_data(devices=[], bridges=[bridge])
        )

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["bridges"][0]["firmware_update"] is None
