from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    valid_statistic_id,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
from obi_energy_tracker import (
    ConnectionStrength,
    Device,
    ObiEnergyTrackerAuthError,
    ObiEnergyTrackerError,
    OutletState,
)
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
)
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.obi_energy_tracker.const import DOMAIN
from custom_components.obi_energy_tracker.coordinator import (
    DEVICE_MEASURES,
    MEASURES_WINDOW,
    STATISTIC_MEASURES,
    DeviceData,
    ObiEnergyTrackerCoordinator,
    ObiEnergyTrackerData,
)

from .conftest import (
    make_bridge,
    make_firmware_update,
    make_measure_record,
    make_oauth_config_data,
    make_outlet,
    make_sensor,
)

_CREATED: list[ObiEnergyTrackerCoordinator] = []


def _make_coordinator(hass, mock_api, entry=None):
    if entry is None:
        entry = MockConfigEntry(domain=DOMAIN, data=make_oauth_config_data(), version=1)
        entry.add_to_hass(hass)
    coordinator = ObiEnergyTrackerCoordinator(hass, entry, mock_api)
    entry.runtime_data = coordinator
    _CREATED.append(coordinator)
    return coordinator


@pytest.fixture(autouse=True)
async def _shutdown_coordinators():
    yield
    while _CREATED:
        await _CREATED.pop().async_shutdown()


def _measures(**per_device: dict[str, list]) -> dict:
    return dict(per_device)


def _hours(mock_api) -> int:
    duration = mock_api.async_get_bridge_measures.await_args.kwargs["duration"]
    return int(duration.removeprefix("PT").removesuffix("H"))


class TestAsyncSetup:
    async def test_fetches_bridges(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)

        await coordinator.async_setup()

        mock_api.async_get_bridges.assert_awaited_once()
        assert len(coordinator._bridges) == 1

    async def test_bridge_api_error_propagates(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerError("boom")
        coordinator = _make_coordinator(hass, mock_api)

        with pytest.raises(ObiEnergyTrackerError, match="boom"):
            await coordinator.async_setup()


class TestUpdateRequestShape:
    async def test_one_measures_call_per_bridge(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensors = [make_sensor(id=f"s-{i}", bridge_id="br-1") for i in range(5)]
        outlet = make_outlet(id="o-1", bridge_id="br-1")
        bridge = make_bridge(id="br-1", sensors=sensors, outlets=[outlet])
        mock_api.async_get_bridges.return_value = [bridge]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator._async_update_data()

        assert mock_api.async_get_bridge_measures.await_count == 1

    async def test_measures_called_with_rolling_window(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator._async_update_data()

        _, kwargs = mock_api.async_get_bridge_measures.await_args
        assert kwargs["duration"] == MEASURES_WINDOW
        assert set(kwargs["measures"]) == {
            "energy",
            "negative_energy",
            "rssi",
            "battery",
        }

    async def test_no_measures_call_for_bridge_without_devices(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [make_bridge(id="br-empty")]
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        mock_api.async_get_bridge_measures.assert_not_awaited()
        assert data.devices == {}
        assert [bridge.id for bridge in data.bridges] == ["br-empty"]

    async def test_multiple_bridges_fetched(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        bridge_a = make_bridge(id="br-a", sensors=[make_sensor(id="s-a")])
        bridge_b = make_bridge(id="br-b", sensors=[make_sensor(id="s-b")])
        mock_api.async_get_bridges.return_value = [bridge_a, bridge_b]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert mock_api.async_get_bridge_measures.await_count == 2
        assert set(data.devices) == {"s-a", "s-b"}
        assert {bridge.id for bridge in data.bridges} == {"br-a", "br-b"}


class TestMeasureMapping:
    async def test_latest_values_per_measure(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1", battery_level=None)
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        old = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
        new = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "s-1": {
                    "energy": [
                        make_measure_record(time=old, value=100.0),
                        make_measure_record(time=new, value=200.0),
                    ],
                    "negative_energy": [
                        make_measure_record(
                            time=new, value=50.0, measure="negative_energy"
                        )
                    ],
                    "rssi": [
                        make_measure_record(time=new, value=-93.0, measure="rssi")
                    ],
                    "battery": [
                        make_measure_record(time=new, value=92.0, measure="battery")
                    ],
                }
            }
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        device_data = data.devices["s-1"]
        assert device_data.energy == 200.0
        assert device_data.negative_energy == 50.0
        assert device_data.rssi == -93.0
        assert device_data.battery == 92.0

        assert device_data.battery_level == 92

    async def test_battery_metadata_preferred(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1", battery_level=77)
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"s-1": {"battery": [make_measure_record(value=10.0, measure="battery")]}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.devices["s-1"].battery_level == 77

    async def test_all_devices_of_a_bridge_share_one_measures_request(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [
            make_bridge(
                id="br-1",
                sensors=[make_sensor(id="s-1", bridge_id="br-1")],
                outlets=[
                    make_outlet(id="o-1", bridge_id="br-1"),
                    make_outlet(id="o-2", bridge_id="br-1"),
                ],
            )
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "s-1": {"energy": [make_measure_record(value=100.0)]},
                "o-1": {"energy": [make_measure_record(value=200.0)]},
                "o-2": {"energy": [make_measure_record(value=300.0)]},
            }
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        mock_api.async_get_bridge_measures.assert_awaited_once_with(
            "br-1", measures=DEVICE_MEASURES, duration=MEASURES_WINDOW
        )
        assert [data.devices[key].energy for key in ("s-1", "o-1", "o-2")] == [
            100.0,
            200.0,
            300.0,
        ]

    @pytest.mark.parametrize(
        ("make_device", "collection", "rssi", "expected"),
        [
            (make_sensor, "sensors", -20.0, ConnectionStrength.EXCELLENT),
            (make_sensor, "sensors", -85.0, ConnectionStrength.FAIR),
            (make_outlet, "outlets", -36.0, ConnectionStrength.EXCELLENT),
            (make_outlet, "outlets", -99.0, ConnectionStrength.BAD),
        ],
    )
    async def test_connection_strength_derived_from_rssi(
        self,
        hass: HomeAssistant,
        mock_api: AsyncMock,
        make_device: Callable[..., Device],
        collection: str,
        rssi: float,
        expected: ConnectionStrength,
    ) -> None:
        device = make_device(id="d-1", bridge_id="br-1")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", **{collection: [device]})
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"d-1": {"rssi": [make_measure_record(value=rssi, measure="rssi")]}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.devices["d-1"].connection_strength is expected

    async def test_connection_strength_without_rssi(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[make_sensor(id="s-1", bridge_id="br-1")])
        ]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.devices["s-1"].connection_strength is None

    async def test_missing_measures_are_none(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1", battery_level=None)
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        device_data = data.devices["s-1"]
        assert device_data.energy is None
        assert device_data.negative_energy == 0.0
        assert device_data.rssi is None
        assert device_data.battery_level is None

    async def test_feed_in_keeps_last_value_when_window_is_empty(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        mock_api.async_get_bridge_measures.return_value = {
            "s-1": {
                "negative_energy": [
                    make_measure_record(value=900.0, measure="negative_energy")
                ]
            }
        }
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        coordinator.data = await coordinator._async_update_data()
        assert coordinator.data.devices["s-1"].negative_energy == 900.0

        mock_api.async_get_bridge_measures.return_value = {}
        data = await coordinator._async_update_data()

        assert data.devices["s-1"].negative_energy == 900.0

    async def test_outlet_state_available(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        outlet = make_outlet(id="o-1", bridge_id="br-1", state="on")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", outlets=[outlet])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"o-1": {"energy": [make_measure_record(value=1137.0)]}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.devices["o-1"].device.state == "on"
        assert data.devices["o-1"].energy == 1137.0

    async def test_measures_for_unknown_device_ignored(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "s-1": {"energy": [make_measure_record(value=1.0)]},
                "gone-1": {"energy": [make_measure_record(value=2.0)]},
            }
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert set(data.devices) == {"s-1"}


class TestDynamicDiscovery:
    async def test_metadata_refreshed_every_cycle(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        mock_api.async_get_bridges.reset_mock()

        await coordinator._async_update_data()
        await coordinator._async_update_data()

        assert mock_api.async_get_bridges.await_count == 2

    async def test_new_device_appears_in_data(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        sensor = make_sensor(id="s-1", bridge_id="br-1")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[sensor])
        ]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        first = await coordinator._async_update_data()
        assert set(first.devices) == {"s-1"}

        mock_api.async_get_bridges.return_value = [
            make_bridge(
                id="br-1",
                sensors=[sensor],
                outlets=[make_outlet(id="o-new", bridge_id="br-1")],
            )
        ]

        second = await coordinator._async_update_data()
        assert set(second.devices) == {"s-1", "o-new"}

    async def test_removed_device_disappears(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        await coordinator._async_update_data()

        mock_api.async_get_bridges.return_value = [make_bridge(id="bridge-001")]

        data = await coordinator._async_update_data()
        assert data.devices == {}


class TestStaleDevices:
    async def test_vanished_device_is_removed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        entry = MockConfigEntry(domain=DOMAIN, data=make_oauth_config_data(), version=1)
        entry.add_to_hass(hass)
        coordinator = _make_coordinator(hass, mock_api, entry=entry)
        await coordinator.async_setup()

        device_reg = dr.async_get(hass)
        for obi_id in ("bridge-001", "sensor-001", "sensor-gone"):
            device_reg.async_get_or_create(
                config_entry_id=entry.entry_id, identifiers={(DOMAIN, obi_id)}
            )

        await coordinator._async_update_data()

        assert device_reg.async_get_device(identifiers={(DOMAIN, "sensor-001")})
        assert device_reg.async_get_device(identifiers={(DOMAIN, "bridge-001")})
        assert (
            device_reg.async_get_device(identifiers={(DOMAIN, "sensor-gone")}) is None
        )

    async def test_foreign_identifiers_are_kept(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        entry = MockConfigEntry(domain=DOMAIN, data=make_oauth_config_data(), version=1)
        entry.add_to_hass(hass)
        coordinator = _make_coordinator(hass, mock_api, entry=entry)
        await coordinator.async_setup()

        device_reg = dr.async_get(hass)
        device_reg.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={("other", "x-1")}
        )

        await coordinator._async_update_data()

        assert device_reg.async_get_device(identifiers={("other", "x-1")})


class TestErrorHandling:
    async def test_metadata_failure_uses_cached_topology(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        cached = coordinator._bridges

        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerError("net down")

        with caplog.at_level(logging.WARNING):
            data = await coordinator._async_update_data()

        assert "using cached topology" in caplog.text
        assert coordinator._bridges is cached
        assert set(data.devices) == {"sensor-001"}

    async def test_metadata_failure_without_cache_raises(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerError("net down")

        with pytest.raises(UpdateFailed) as err:
            await coordinator._async_update_data()

        assert err.value.translation_key == "update_failed"
        assert err.value.translation_placeholders == {"error": "net down"}

    async def test_measures_failure_keeps_devices(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_measures.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with caplog.at_level(logging.WARNING):
            data = await coordinator._async_update_data()

        assert "Failed to get measures for bridge" in caplog.text
        assert data.devices["sensor-001"].energy is None
        assert data.devices["sensor-001"].device.is_online is True

    async def test_measures_failure_logs_once(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_measures.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with caplog.at_level(logging.WARNING):
            await coordinator._async_update_data()
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("Failed to get measures for bridge") == 1

    async def test_measures_recovery_logged_once(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_measures.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        await coordinator._async_update_data()

        mock_api.async_get_bridge_measures.side_effect = None
        mock_api.async_get_bridge_measures.return_value = {
            "sensor-001": {"energy": [make_measure_record(value=5.0)]}
        }

        with caplog.at_level(
            logging.INFO, logger="custom_components.obi_energy_tracker.coordinator"
        ):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("are available again") == 1

    async def test_topology_failure_logs_once(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerError("net down")

        with caplog.at_level(logging.WARNING):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("using cached topology") == 1

    async def test_topology_recovery_logged(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerError("net down")
        await coordinator._async_update_data()

        mock_api.async_get_bridges.side_effect = None

        with caplog.at_level(
            logging.INFO, logger="custom_components.obi_energy_tracker.coordinator"
        ):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("Device metadata is available again") == 1

    async def test_one_bridge_failure_does_not_affect_other(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        bridge_a = make_bridge(id="br-a", sensors=[make_sensor(id="s-a")])
        bridge_b = make_bridge(id="br-b", sensors=[make_sensor(id="s-b")])
        mock_api.async_get_bridges.return_value = [bridge_a, bridge_b]

        async def measures(bridge_id, **kwargs):
            if bridge_id == "br-a":
                raise ObiEnergyTrackerError("bridge a down")
            return {"s-b": {"energy": [make_measure_record(value=7.0)]}}

        mock_api.async_get_bridge_measures.side_effect = measures
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.devices["s-a"].energy is None
        assert data.devices["s-b"].energy == 7.0

    async def test_auth_error_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridge_measures.side_effect = ObiEnergyTrackerAuthError(
            "token expired"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ConfigEntryAuthFailed) as err:
            await coordinator._async_update_data()

        assert err.value.translation_key == "auth_failed"

    async def test_metadata_auth_error_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        mock_api.async_get_bridges.side_effect = ObiEnergyTrackerAuthError("expired")

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_update_interval(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)

        assert coordinator.update_interval == timedelta(minutes=5)


class TestOutletControl:
    async def test_sets_state_and_refreshes(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        await coordinator.async_refresh()

        await coordinator.async_set_outlet_state("o-1", OutletState.ON)
        await hass.async_block_till_done()

        mock_api.async_set_outlet_state.assert_awaited_once_with("o-1", "on")

    async def test_auth_error_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_set_outlet_state.side_effect = ObiEnergyTrackerAuthError("nope")
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_set_outlet_state("o-1", OutletState.ON)

    async def test_api_error_propagates(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_set_outlet_state.side_effect = ObiEnergyTrackerError("boom")
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ObiEnergyTrackerError):
            await coordinator.async_set_outlet_state("o-1", OutletState.ON)


class TestFirmwareUpdates:
    async def test_one_firmware_call_per_bridge(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1", sensors=[make_sensor(id="s-1", bridge_id="br-1")]),
            make_bridge(id="br-2", sensors=[make_sensor(id="s-2", bridge_id="br-2")]),
        ]
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator._async_update_data()

        assert [
            call.args[0]
            for call in mock_api.async_get_bridge_firmware_update.await_args_list
        ] == ["br-1", "br-2"]

    async def test_available_update_lands_in_data(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        update = make_firmware_update(id="fw-1", version="1.1.0")
        mock_api.async_get_bridge_firmware_update.return_value = update
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.firmware_updates == {"bridge-001": update}

    async def test_no_update_leaves_the_map_empty(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.firmware_updates == {}

    async def test_firmware_call_happens_without_devices(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [make_bridge(id="br-1")]
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator._async_update_data()

        mock_api.async_get_bridge_firmware_update.assert_awaited_once_with("br-1")
        mock_api.async_get_bridge_measures.assert_not_awaited()

    async def test_failure_keeps_devices(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_firmware_update.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with caplog.at_level(logging.WARNING):
            data = await coordinator._async_update_data()

        assert "Failed to check for firmware updates of bridge" in caplog.text
        assert data.firmware_updates == {}
        assert data.devices["sensor-001"].energy == 1234.5

    async def test_failure_logs_once(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_firmware_update.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with caplog.at_level(logging.WARNING):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("Failed to check for firmware updates") == 1

    async def test_recovery_logged_once(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_firmware_update.side_effect = ObiEnergyTrackerError(
            "timeout"
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        await coordinator._async_update_data()

        mock_api.async_get_bridge_firmware_update.side_effect = None
        mock_api.async_get_bridge_firmware_update.return_value = None

        with caplog.at_level(
            logging.INFO, logger="custom_components.obi_energy_tracker.coordinator"
        ):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert caplog.text.count("Firmware update information for bridge") == 1

    async def test_auth_error_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridge_firmware_update.side_effect = (
            ObiEnergyTrackerAuthError("nope")
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_one_bridge_failure_does_not_affect_other(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        update = make_firmware_update(id="fw-2")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br-1"),
            make_bridge(id="br-2"),
        ]

        async def _firmware(bridge_id: str):
            if bridge_id == "br-1":
                raise ObiEnergyTrackerError("timeout")
            return update

        mock_api.async_get_bridge_firmware_update.side_effect = _firmware
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert data.firmware_updates == {"br-2": update}


class TestFirmwareInstall:
    async def test_triggers_and_refreshes(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        await coordinator.async_refresh()

        await coordinator.async_install_bridge_firmware("br-1", "fw-1")
        await hass.async_block_till_done()

        mock_api.async_trigger_bridge_firmware_update.assert_awaited_once_with(
            "br-1", "fw-1"
        )

    async def test_auth_error_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_trigger_bridge_firmware_update.side_effect = (
            ObiEnergyTrackerAuthError("nope")
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_install_bridge_firmware("br-1", "fw-1")

    async def test_api_error_propagates(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_trigger_bridge_firmware_update.side_effect = (
            ObiEnergyTrackerError("boom")
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with pytest.raises(ObiEnergyTrackerError):
            await coordinator.async_install_bridge_firmware("br-1", "fw-1")


class TestDataStructure:
    async def test_returned_data_shape(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert isinstance(data, ObiEnergyTrackerData)
        assert [bridge.id for bridge in data.bridges] == ["bridge-001"]
        assert isinstance(data.devices["sensor-001"], DeviceData)
        assert data.devices["sensor-001"].bridge is data.bridges[0]
        assert data.devices["sensor-001"].energy == 1234.5

    async def test_devices_contain_sensors_and_outlets(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridges.return_value = [
            make_bridge(
                id="br-1",
                sensors=[make_sensor(id="s-1", bridge_id="br-1")],
                outlets=[make_outlet(id="o-1", bridge_id="br-1")],
            )
        ]
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        data = await coordinator._async_update_data()

        assert sorted(data.devices) == ["o-1", "s-1"]
        assert data.devices["o-1"].device.is_outlet is True
        assert data.devices["s-1"].device.is_outlet is False


class TestStatisticsImport:
    @staticmethod
    def _series(
        start: datetime = datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        count: int = 37,
        per_step: float = 12.0,
        base: float = 1000.0,
        measure: str = "energy",
    ) -> list:
        return [
            make_measure_record(
                time=start + timedelta(minutes=5 * index),
                value=base + per_step * index,
                measure=measure,
            )
            for index in range(count)
        ]

    async def test_imports_for_sensors_and_outlets(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridges.return_value = [
            make_bridge(
                id="br-1",
                sensors=[make_sensor(id="s-1", bridge_id="br-1")],
                outlets=[make_outlet(id="o-1", bridge_id="br-1")],
            )
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "s-1": {"energy": self._series()},
                "o-1": {"energy": self._series()},
            }
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        mock_api.async_get_bridge_measures.assert_awaited_once_with(
            "br-1", measures=tuple(STATISTIC_MEASURES), duration=None
        )
        assert [meta["statistic_id"] for meta, _ in imported] == [
            "obi_energy_tracker:s_1_consumption",
            "obi_energy_tracker:s_1_feed_in",
            "obi_energy_tracker:o_1_consumption",
            "obi_energy_tracker:o_1_feed_in",
        ]
        assert all(valid_statistic_id(meta["statistic_id"]) for meta, _ in imported)
        assert all(meta["source"] == DOMAIN for meta, _ in imported)
        assert all(meta["unit_of_measurement"] == "Wh" for meta, _ in imported)

    async def test_buckets_follow_meter_increments(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        _, stats = imported[0]

        assert [entry["start"].hour for entry in stats] == [10, 11, 12, 13]

        assert [entry["sum"] for entry in stats] == [132.0, 276.0, 420.0, 432.0]

        assert [entry["state"] for entry in stats] == [1132.0, 1276.0, 1420.0, 1432.0]

    async def test_total_matches_the_meter_difference(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        series = self._series(start=datetime(2026, 1, 1, 0, 0, tzinfo=UTC), count=288)
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": series}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        _, stats = imported[0]
        assert stats[-1]["sum"] == series[-1].value - series[0].value

    async def test_negative_energy_imported_separately(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "sensor-001": {
                    "energy": self._series(),
                    "negative_energy": self._series(
                        per_step=4.0, base=50.0, measure="negative_energy"
                    ),
                }
            }
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        ids = [meta["statistic_id"] for meta, _ in imported]
        assert ids == [
            "obi_energy_tracker:sensor_001_consumption",
            "obi_energy_tracker:sensor_001_feed_in",
        ]
        _, feed_in = imported[1]
        assert [entry["sum"] for entry in feed_in] == [44.0, 92.0, 140.0, 144.0]

    async def test_feed_in_statistic_exists_without_cloud_records(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        feed_in = [
            stats
            for meta, stats in imported
            if meta["statistic_id"] == "obi_energy_tracker:sensor_001_feed_in"
        ]

        assert len(feed_in) == 1
        assert [entry["start"].hour for entry in feed_in[0]] == [10, 11, 12, 13]
        assert all(entry["sum"] == 0.0 for entry in feed_in[0])

    @pytest.mark.parametrize(
        ("language", "expected"),
        [
            ("de", ["Test Sensor Verbrauch", "Test Sensor Einspeisung"]),
            ("en", ["Test Sensor Consumption", "Test Sensor Feed-in"]),
        ],
    )
    async def test_statistic_names_follow_the_instance_language(
        self,
        hass: HomeAssistant,
        mock_api: AsyncMock,
        language: str,
        expected: list[str],
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        hass.config.language = language
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        assert [meta["name"] for meta, _ in imported] == expected

    async def test_statistic_ids_stay_language_independent(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        hass.config.language = "de"
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        assert [meta["statistic_id"] for meta, _ in imported] == [
            "obi_energy_tracker:sensor_001_consumption",
            "obi_energy_tracker:sensor_001_feed_in",
        ]

    async def test_single_reading_not_imported(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series(count=1)}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        assert imported == []

    async def test_no_readings_no_import(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        assert imported == []

    async def test_second_import_continues_from_existing_sum(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br1", sensors=[make_sensor(id="s1", bridge_id="br1")])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"s1": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator.async_import_historical_statistics()
        await async_wait_recording_done(hass)

        mock_api.async_get_bridge_measures.return_value = _measures(
            **{
                "s1": {
                    "energy": self._series(
                        start=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
                        count=25,
                        base=0.0,
                    )
                }
            }
        )

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        _, stats = imported[0]
        assert [entry["start"].hour for entry in stats] == [13, 14]
        assert [entry["sum"] for entry in stats] == [432.0, 444.0]

    async def test_import_rebases_onto_own_previous_sum(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br1", sensors=[make_sensor(id="s1", bridge_id="br1")])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"s1": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await self._seed_id(
            hass,
            f"{DOMAIN}:s1_consumption",
            datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
            5000.0,
        )

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        _, stats = imported[0]
        assert [entry["sum"] for entry in stats] == [5000.0, 5144.0, 5288.0, 5300.0]

    async def test_recent_hours_are_not_pinned_to_a_stale_sum(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridges.return_value = [
            make_bridge(id="br1", sensors=[make_sensor(id="s1", bridge_id="br1")])
        ]
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"s1": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await self._seed_id(
            hass,
            f"{DOMAIN}:s1_consumption",
            datetime(2026, 1, 1, 13, 0, tzinfo=UTC),
            1.0,
        )

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        _, stats = imported[0]
        assert stats[-1]["start"].hour == 13
        assert stats[-1]["sum"] == 432.0

    async def test_hyphenated_device_id_is_accepted(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator.async_import_historical_statistics()
        await async_wait_recording_done(hass)

        statistic_id = f"{DOMAIN}:sensor_001_consumption"
        rows = await get_instance(hass).async_add_executor_job(
            get_last_statistics, hass, 1, statistic_id, True, {"sum"}
        )
        assert rows[statistic_id][0]["sum"] == 432.0

    async def test_first_setup_pulls_the_full_history(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator.async_import_historical_statistics()

        assert mock_api.async_get_bridge_measures.await_args.kwargs["duration"] is None

    async def test_later_setup_catches_up_instead_of_pulling_everything(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        gap = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) - timedelta(
            hours=9
        )
        for key in ("consumption", "feed_in"):
            await self._seed_id(hass, f"{DOMAIN}:sensor_001_{key}", gap, 10.0)

        mock_api.async_get_bridge_measures.reset_mock()

        await coordinator.async_import_historical_statistics()

        assert _hours(mock_api) in (11, 12)

    async def test_missing_statistic_forces_the_full_history(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = {}
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        top = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
        await self._seed_id(hass, f"{DOMAIN}:sensor_001_consumption", top, 10.0)

        mock_api.async_get_bridge_measures.reset_mock()

        await coordinator.async_import_historical_statistics()

        assert mock_api.async_get_bridge_measures.await_args.kwargs["duration"] is None

    async def test_cycle_makes_no_extra_request(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()
        mock_api.async_get_bridge_measures.reset_mock()

        await coordinator._async_update_data()

        assert mock_api.async_get_bridge_measures.await_count == 1
        kwargs = mock_api.async_get_bridge_measures.await_args.kwargs
        assert kwargs["duration"] == MEASURES_WINDOW

    async def test_cycle_needs_no_window_only_the_new_readings(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        series = self._series()
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": series}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator.async_import_historical_statistics()
        await async_wait_recording_done(hass)

        cursor = coordinator._cursors[("sensor-001", "consumption")]
        assert cursor.time == series[-1].time
        assert cursor.value == series[-1].value
        assert cursor.total == 432.0

        nxt = self._series(
            start=series[-1].time + timedelta(minutes=5), count=2, base=1444.0
        )
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": nxt}}
        )

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator._async_update_data()

        _, stats = imported[0]
        assert [entry["start"].hour for entry in stats] == [13]
        assert [entry["sum"] for entry in stats] == [456.0]
        assert coordinator._cursors[("sensor-001", "consumption")].time == nxt[-1].time

    async def test_cycle_ignores_readings_it_already_counted(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        series = self._series()
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": series}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        await coordinator.async_import_historical_statistics()
        await async_wait_recording_done(hass)

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator._async_update_data()
            await coordinator._async_update_data()

        assert imported == []
        assert coordinator._cursors[("sensor-001", "consumption")].total == 432.0

    async def test_cycle_without_a_cursor_writes_nothing(
        self, hass: HomeAssistant, mock_api: AsyncMock
    ) -> None:
        await hass.config.async_set_time_zone("UTC")
        mock_api.async_get_bridge_measures.return_value = _measures(
            **{"sensor-001": {"energy": self._series()}}
        )
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with _patch_import_statistics(imported):
            await coordinator._async_update_data()

        assert imported == []

    async def test_statistics_failure_keeps_entities_alive(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        with (
            caplog.at_level(logging.ERROR),
            patch.object(
                coordinator,
                "_async_advance_statistics",
                side_effect=RuntimeError("recorder down"),
            ),
        ):
            data = await coordinator._async_update_data()

        assert data.devices
        assert "Failed to update statistics" in caplog.text

    @staticmethod
    async def _seed_id(
        hass: HomeAssistant, statistic_id: str, start: datetime, total: float
    ) -> None:
        async_add_external_statistics(
            hass,
            StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name="Test Sensor Consumption",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_of_measurement="Wh",
                unit_class="energy",
            ),
            [StatisticData(start=start, state=1400.0, sum=total)],
        )
        await async_wait_recording_done(hass)

    async def test_api_failure_skips_bridge(
        self, hass: HomeAssistant, mock_api: AsyncMock, caplog
    ) -> None:
        mock_api.async_get_bridge_measures.side_effect = ObiEnergyTrackerError("down")
        coordinator = _make_coordinator(hass, mock_api)
        await coordinator.async_setup()

        imported: list = []
        with caplog.at_level(logging.WARNING), _patch_import_statistics(imported):
            await coordinator.async_import_historical_statistics()

        assert imported == []
        assert "Failed to fetch historical data" in caplog.text


def _patch_import_statistics(sink: list):
    from unittest.mock import patch

    def _record(hass, metadata, statistics):
        sink.append((metadata, statistics))

    return patch(
        "custom_components.obi_energy_tracker.coordinator.async_add_external_statistics",
        side_effect=_record,
    )
