from __future__ import annotations

from collections.abc import Iterator
from typing import Any, override

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityDescription,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from obi_energy_tracker import (
    Bridge,
    FirmwareUpdate,
    ObiEnergyTrackerError,
    OtaStatus,
)

from .const import DOMAIN
from .coordinator import ObiEnergyTrackerConfigEntry, ObiEnergyTrackerCoordinator
from .entity import ObiBridgeEntity, async_add_entities_dynamically

PARALLEL_UPDATES = 1

FIRMWARE_DESCRIPTION = UpdateEntityDescription(
    key="firmware",
    translation_key="firmware",
    device_class=UpdateDeviceClass.FIRMWARE,
)

UPDATING_STATUSES = frozenset(
    {OtaStatus.STARTED, OtaStatus.DOWNLOADING, OtaStatus.INSTALLING}
)


def _build_entities(
    coordinator: ObiEnergyTrackerCoordinator,
) -> Iterator[tuple[str, Entity]]:
    for bridge in coordinator.data.bridges:
        yield (
            f"{bridge.id}_{FIRMWARE_DESCRIPTION.key}",
            ObiBridgeUpdate(coordinator, bridge, FIRMWARE_DESCRIPTION),
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


class ObiBridgeUpdate(ObiBridgeEntity, UpdateEntity):
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(
        self,
        coordinator: ObiEnergyTrackerCoordinator,
        bridge: Bridge,
        description: UpdateEntityDescription,
    ) -> None:
        super().__init__(coordinator, bridge, description.key)
        self.entity_description = description

    @property
    def firmware_update(self) -> FirmwareUpdate | None:
        return self.coordinator.data.firmware_updates.get(self._bridge_id)

    @property
    @override
    def installed_version(self) -> str | None:
        bridge = self.bridge
        return None if bridge is None else bridge.firmware_version

    @property
    @override
    def latest_version(self) -> str | None:
        update = self.firmware_update
        return self.installed_version if update is None else update.version

    @property
    @override
    def release_summary(self) -> str | None:
        update = self.firmware_update
        return None if update is None else update.change_log

    @property
    @override
    def in_progress(self) -> bool:
        bridge = self.bridge
        return bridge is not None and bridge.ota_status in UPDATING_STATUSES

    @property
    @override
    def update_percentage(self) -> int | None:
        bridge = self.bridge
        return None if bridge is None else bridge.ota_progress

    @override
    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        bridge = self.bridge
        label = self._bridge_id if bridge is None else bridge.display_name
        update = self.firmware_update
        if update is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_update_unavailable",
                translation_placeholders={"name": label},
            )
        try:
            await self.coordinator.async_install_bridge_firmware(
                self._bridge_id, update.id
            )
        except ObiEnergyTrackerError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="firmware_update_failed",
                translation_placeholders={"name": label, "error": str(err)},
            ) from err
