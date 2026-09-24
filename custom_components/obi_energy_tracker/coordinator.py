from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from math import ceil
from typing import override

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import translation
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from obi_energy_tracker import (
    Bridge,
    ConnectionStrength,
    Device,
    FirmwareUpdate,
    HourlyBucket,
    Measure,
    MeasureRecord,
    ObiEnergyTrackerApi,
    ObiEnergyTrackerAuthError,
    ObiEnergyTrackerError,
    OutletState,
    connection_strength_from_rssi,
    cumulative,
    hourly_buckets,
    to_series,
)

from .const import (
    DOMAIN,
    KEY_CONSUMPTION,
    KEY_FEED_IN,
    SCAN_INTERVAL_MINUTES,
)

_LOGGER = logging.getLogger(__name__)

MEASURES_WINDOW = "PT15M"

STATISTICS_WARMUP_BUCKETS = 1

CATCH_UP_MARGIN = timedelta(hours=2)

DEVICE_MEASURES: tuple[Measure, ...] = (
    Measure.ENERGY,
    Measure.NEGATIVE_ENERGY,
    Measure.RSSI,
    Measure.BATTERY,
)

STATISTIC_MEASURES: dict[Measure, str] = {
    Measure.ENERGY: KEY_CONSUMPTION,
    Measure.NEGATIVE_ENERGY: KEY_FEED_IN,
}

STATISTIC_LABEL_FALLBACKS: dict[str, str] = {
    KEY_CONSUMPTION: "Consumption",
    KEY_FEED_IN: "Feed-in",
}


@dataclass(frozen=True)
class MeterCursor:
    time: datetime
    value: float
    total: float


@dataclass
class DeviceData:
    device: Device
    bridge: Bridge
    energy: float | None = None
    negative_energy: float = 0.0
    rssi: float | None = None
    battery: float | None = None

    @property
    def battery_level(self) -> int | None:
        if self.device.battery_level is not None:
            return self.device.battery_level
        return None if self.battery is None else int(self.battery)

    @property
    def connection_strength(self) -> ConnectionStrength | None:
        return connection_strength_from_rssi(self.rssi)


@dataclass
class ObiEnergyTrackerData:
    bridges: list[Bridge] = field(default_factory=list)
    devices: dict[str, DeviceData] = field(default_factory=dict)
    firmware_updates: dict[str, FirmwareUpdate] = field(default_factory=dict)

    def bridge(self, bridge_id: str) -> Bridge | None:
        return next((bridge for bridge in self.bridges if bridge.id == bridge_id), None)


type ObiEnergyTrackerConfigEntry = ConfigEntry[ObiEnergyTrackerCoordinator]


def _latest_value(records: list[MeasureRecord] | None) -> float | None:
    return records[-1].value if records else None


def _statistic_id(device_id: str, key: str) -> str:
    return f"{DOMAIN}:{slugify(device_id)}_{key}"


def _statistic_readings(
    readings: dict[Measure, list[MeasureRecord]], measure: Measure
) -> list[MeasureRecord]:
    records = readings.get(measure) or []
    if records or measure is not Measure.NEGATIVE_ENERGY:
        return records
    return [
        MeasureRecord(time=record.time, value=0.0, measure=measure)
        for record in readings.get(Measure.ENERGY) or []
    ]


def _baseline_shift(
    buckets: list[tuple[HourlyBucket, float]],
    anchor_sum: float | None,
) -> float:
    if anchor_sum is None:
        return 0.0
    return anchor_sum - buckets[0][1]


class ObiEnergyTrackerCoordinator(DataUpdateCoordinator[ObiEnergyTrackerData]):
    config_entry: ObiEnergyTrackerConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ObiEnergyTrackerConfigEntry,
        client: ObiEnergyTrackerApi,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=SCAN_INTERVAL_MINUTES),
        )
        self.client = client
        self._bridges: list[Bridge] = []
        self._feed_in: dict[str, float] = {}
        self._cursors: dict[tuple[str, str], MeterCursor] = {}
        self._labels: dict[str, str] = dict(STATISTIC_LABEL_FALLBACKS)
        self._measures_failed: set[str] = set()
        self._firmware_failed: set[str] = set()
        self._topology_failed = False

    async def async_setup(self) -> None:
        self._bridges = await self.client.async_get_bridges()

    @override
    async def _async_update_data(self) -> ObiEnergyTrackerData:
        try:
            await self._async_refresh_topology()

            results = await asyncio.gather(
                *(self._async_fetch_bridge(bridge) for bridge in self._bridges)
            )

            devices: dict[str, DeviceData] = {}
            firmware_updates: dict[str, FirmwareUpdate] = {}
            for bridge_devices, bridge_firmware in results:
                devices.update(bridge_devices)
                firmware_updates.update(bridge_firmware)

            data = ObiEnergyTrackerData(
                bridges=self._bridges,
                devices=devices,
                firmware_updates=firmware_updates,
            )
            self._async_remove_stale_devices(data)
            return data

        except ObiEnergyTrackerAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        except ObiEnergyTrackerError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    @callback
    def _async_remove_stale_devices(self, data: ObiEnergyTrackerData) -> None:
        current = {bridge.id for bridge in data.bridges} | set(data.devices)
        device_reg = dr.async_get(self.hass)
        for entry in dr.async_entries_for_config_entry(
            device_reg, self.config_entry.entry_id
        ):
            obi_ids = {i[1] for i in entry.identifiers if i[0] == DOMAIN}
            if obi_ids and not obi_ids & current:
                _LOGGER.debug("Removing stale device %s", entry.name)
                device_reg.async_update_device(
                    entry.id, remove_config_entry_id=self.config_entry.entry_id
                )

    async def _async_update_statistics(
        self, bridge: Bridge, measures: dict[str, dict[Measure, list[MeasureRecord]]]
    ) -> None:
        try:
            await self._async_advance_statistics(bridge, measures)
        except Exception:
            _LOGGER.exception("Failed to update statistics for bridge %s", bridge.id)

    async def _async_refresh_topology(self) -> None:
        try:
            self._bridges = await self.client.async_get_bridges()
        except ObiEnergyTrackerAuthError:
            raise
        except ObiEnergyTrackerError as err:
            if not self._bridges:
                raise
            if not self._topology_failed:
                self._topology_failed = True
                _LOGGER.warning(
                    "Failed to refresh device metadata (%s), using cached topology", err
                )
        else:
            if self._topology_failed:
                self._topology_failed = False
                _LOGGER.info("Device metadata is available again")

    async def _async_fetch_bridge(
        self, bridge: Bridge
    ) -> tuple[dict[str, DeviceData], dict[str, FirmwareUpdate]]:
        measures, firmware = await asyncio.gather(
            self._async_fetch_measures(bridge),
            self._async_fetch_firmware_update(bridge),
        )

        devices: dict[str, DeviceData] = {}
        for device in bridge.devices:
            series = measures.get(device.id, {})
            devices[device.id] = DeviceData(
                device=device,
                bridge=bridge,
                energy=_latest_value(series.get(Measure.ENERGY)),
                negative_energy=self._feed_in_value(
                    device.id, series.get(Measure.NEGATIVE_ENERGY)
                ),
                rssi=_latest_value(series.get(Measure.RSSI)),
                battery=_latest_value(series.get(Measure.BATTERY)),
            )

        await self._async_update_statistics(bridge, measures)

        return devices, {} if firmware is None else {bridge.id: firmware}

    def _feed_in_value(
        self, device_id: str, records: list[MeasureRecord] | None
    ) -> float:
        value = _latest_value(records)
        if value is None:
            return self._feed_in.get(device_id, 0.0)
        self._feed_in[device_id] = value
        return value

    async def _async_fetch_measures(
        self, bridge: Bridge
    ) -> dict[str, dict[Measure, list[MeasureRecord]]]:
        if not bridge.devices:
            return {}
        try:
            measures = await self.client.async_get_bridge_measures(
                bridge.id,
                measures=DEVICE_MEASURES,
                duration=MEASURES_WINDOW,
            )
        except ObiEnergyTrackerAuthError:
            raise
        except ObiEnergyTrackerError as err:
            if bridge.id not in self._measures_failed:
                self._measures_failed.add(bridge.id)
                _LOGGER.warning(
                    "Failed to get measures for bridge %s: %s", bridge.id, err
                )
            return {}
        if bridge.id in self._measures_failed:
            self._measures_failed.discard(bridge.id)
            _LOGGER.info("Measures for bridge %s are available again", bridge.id)
        return measures

    async def _async_fetch_firmware_update(
        self, bridge: Bridge
    ) -> FirmwareUpdate | None:
        try:
            firmware = await self.client.async_get_bridge_firmware_update(bridge.id)
        except ObiEnergyTrackerAuthError:
            raise
        except ObiEnergyTrackerError as err:
            if bridge.id not in self._firmware_failed:
                self._firmware_failed.add(bridge.id)
                _LOGGER.warning(
                    "Failed to check for firmware updates of bridge %s: %s",
                    bridge.id,
                    err,
                )
            return None
        if bridge.id in self._firmware_failed:
            self._firmware_failed.discard(bridge.id)
            _LOGGER.info(
                "Firmware update information for bridge %s is available again",
                bridge.id,
            )
        return firmware

    async def async_install_bridge_firmware(
        self, bridge_id: str, firmware_id: str
    ) -> None:
        try:
            await self.client.async_trigger_bridge_firmware_update(
                bridge_id, firmware_id
            )
        except ObiEnergyTrackerAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        await self.async_request_refresh()

    async def async_set_outlet_state(self, outlet_id: str, state: OutletState) -> None:
        try:
            await self.client.async_set_outlet_state(outlet_id, state)
        except ObiEnergyTrackerAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="auth_failed"
            ) from err
        await self.async_request_refresh()

    async def async_import_historical_statistics(self) -> None:
        await self._async_resolve_labels()
        for bridge in self._bridges:
            if not bridge.devices:
                continue
            duration = await self._async_catch_up_duration(bridge)
            try:
                readings = await self.client.async_get_bridge_measures(
                    bridge.id,
                    measures=tuple(STATISTIC_MEASURES),
                    duration=duration,
                )
            except ObiEnergyTrackerError:
                _LOGGER.warning(
                    "Failed to fetch historical data for bridge %s", bridge.id
                )
                continue

            warmup = 0 if duration is None else STATISTICS_WARMUP_BUCKETS
            await self._async_write_bridge_statistics(bridge, readings, warmup)

    async def _async_resolve_labels(self) -> None:
        translations = await translation.async_get_translations(
            self.hass, self.hass.config.language, "entity", {DOMAIN}
        )
        for key, fallback in STATISTIC_LABEL_FALLBACKS.items():
            name = translations.get(f"component.{DOMAIN}.entity.sensor.{key}.name")
            self._labels[key] = name or fallback

    async def _async_catch_up_duration(self, bridge: Bridge) -> str | None:
        oldest: datetime | None = None
        for device in bridge.devices:
            for key in STATISTIC_MEASURES.values():
                last = await self._async_last_statistic_start(
                    _statistic_id(device.id, key)
                )
                if last is None:
                    return None
                oldest = last if oldest is None else min(oldest, last)
        if oldest is None:
            return None
        window = dt_util.utcnow() - (oldest - CATCH_UP_MARGIN)
        return f"PT{ceil(window.total_seconds() / 3600)}H"

    async def _async_last_statistic_start(self, statistic_id: str) -> datetime | None:
        rows = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, set()
        )
        row = next(iter(rows.get(statistic_id, [])), None)
        if row is None:
            return None
        return dt_util.utc_from_timestamp(row["start"])

    async def _async_write_bridge_statistics(
        self,
        bridge: Bridge,
        measures: dict[str, dict[Measure, list[MeasureRecord]]],
        warmup: int,
    ) -> None:
        for device in bridge.devices:
            series = measures.get(device.id, {})
            for measure, key in STATISTIC_MEASURES.items():
                await self._async_write_statistics(
                    device,
                    measure,
                    key,
                    _statistic_readings(series, measure),
                    warmup,
                )

    async def _async_advance_statistics(
        self, bridge: Bridge, measures: dict[str, dict[Measure, list[MeasureRecord]]]
    ) -> None:
        for device in bridge.devices:
            readings = measures.get(device.id, {})
            for measure, key in STATISTIC_MEASURES.items():
                cursor = self._cursors.get((device.id, key))
                if cursor is None:
                    continue
                series = [
                    point
                    for point in to_series(_statistic_readings(readings, measure))
                    if point[0] > cursor.time
                ]
                if series:
                    self._advance(device, key, cursor, series)

    @callback
    def _advance(
        self,
        device: Device,
        key: str,
        cursor: MeterCursor,
        series: list[tuple[datetime, float]],
    ) -> None:
        records = [
            MeasureRecord(time=moment, value=value, measure=Measure.ENERGY)
            for moment, value in [(cursor.time, cursor.value), *series]
        ]
        buckets = cumulative(hourly_buckets(records))
        if buckets:
            self._write(device, key, buckets, cursor.total, series[-1])

    async def _async_statistic_sum_at(
        self, statistic_id: str, start: datetime
    ) -> float | None:
        rows = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            start,
            start + timedelta(hours=1),
            {statistic_id},
            "hour",
            None,
            {"sum"},
        )
        row = next(iter(rows.get(statistic_id, [])), None)
        return None if row is None else row.get("sum")

    async def _async_write_statistics(
        self,
        device: Device,
        measure: Measure,
        key: str,
        readings: list[MeasureRecord] | None,
        warmup: int,
    ) -> None:
        if not readings:
            return

        buckets = cumulative(hourly_buckets(readings))[warmup:]
        if not buckets:
            _LOGGER.debug(
                "Not enough %s readings for device %s to build hourly statistics",
                measure,
                device.id,
            )
            return

        statistic_id = _statistic_id(device.id, key)
        anchor = await self._async_statistic_sum_at(statistic_id, buckets[0][0].start)
        if warmup and anchor is None:
            _LOGGER.debug(
                "No %s statistic at %s for device %s, waiting for the next full import",
                measure,
                buckets[0][0].start.isoformat(),
                device.id,
            )
            return

        self._write(
            device,
            key,
            buckets,
            _baseline_shift(buckets, anchor),
            to_series(readings)[-1],
        )

    @callback
    def _write(
        self,
        device: Device,
        key: str,
        buckets: list[tuple[HourlyBucket, float]],
        shift: float,
        last: tuple[datetime, float],
    ) -> None:
        statistic_id = _statistic_id(device.id, key)
        metadata = StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=f"{device.display_name} {self._labels[key]}",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_of_measurement="Wh",
            unit_class="energy",
        )
        statistics = [
            StatisticData(start=bucket.start, state=bucket.meter, sum=total + shift)
            for bucket, total in buckets
        ]
        async_add_external_statistics(self.hass, metadata, statistics)

        last_bucket, last_total = buckets[-1]
        self._cursors[(device.id, key)] = MeterCursor(
            time=last[0], value=last[1], total=last_total + shift
        )
        _LOGGER.debug(
            "Wrote %d hourly rows for %s of device %s (%s .. %s, offset %s)",
            len(statistics),
            key,
            device.id,
            buckets[0][0].start.isoformat(),
            last_bucket.start.isoformat(),
            shift,
        )
