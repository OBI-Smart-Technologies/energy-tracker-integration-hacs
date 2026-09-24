from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from obi_energy_tracker import ConnectionStrength

from .const import KEY_CONSUMPTION, KEY_FEED_IN
from .coordinator import (
    DeviceData,
    ObiEnergyTrackerConfigEntry,
    ObiEnergyTrackerCoordinator,
)
from .entity import ObiDeviceEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ObiDeviceSensorDescription(SensorEntityDescription):
    value_fn: Callable[[DeviceData], float | str | None]
    exists_fn: Callable[[DeviceData], bool] = lambda _: True


DEVICE_SENSORS: tuple[ObiDeviceSensorDescription, ...] = (
    ObiDeviceSensorDescription(
        key=KEY_CONSUMPTION,
        translation_key=KEY_CONSUMPTION,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda data: data.energy,
    ),
    ObiDeviceSensorDescription(
        key=KEY_FEED_IN,
        translation_key=KEY_FEED_IN,
        native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=lambda data: data.negative_energy,
    ),
    ObiDeviceSensorDescription(
        key="battery_level",
        translation_key="battery_level",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.battery_level,
        exists_fn=lambda data: data.battery_level is not None,
    ),
    ObiDeviceSensorDescription(
        key="signal_strength",
        translation_key="signal_strength",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda data: data.rssi,
        exists_fn=lambda data: data.rssi is not None,
    ),
    ObiDeviceSensorDescription(
        key="connection_strength",
        translation_key="connection_strength",
        device_class=SensorDeviceClass.ENUM,
        options=[strength.value for strength in ConnectionStrength],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: data.connection_strength,
        exists_fn=lambda data: data.connection_strength is not None,
    ),
)


def _build_entities(
    coordinator: ObiEnergyTrackerCoordinator,
) -> Iterator[tuple[str, Entity]]:
    for device_id, device_data in coordinator.data.devices.items():
        for description in DEVICE_SENSORS:
            if description.exists_fn(device_data):
                yield (
                    f"{device_id}_{description.key}",
                    ObiDeviceSensor(coordinator, device_id, description),
                )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ObiEnergyTrackerConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    entry.async_on_unload(
        async_add_entities_dynamically(coordinator, async_add_entities, _build_entities)
    )


class ObiDeviceSensor(ObiDeviceEntity, SensorEntity):
    entity_description: ObiDeviceSensorDescription

    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        device_id: str,
        description: ObiDeviceSensorDescription,
    ) -> None:
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description

    @property
    @override
    def native_value(self) -> float | str | None:
        data = self.device_data
        return None if data is None else self.entity_description.value_fn(data)
