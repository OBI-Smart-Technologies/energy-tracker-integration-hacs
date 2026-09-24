from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from obi_energy_tracker import FirmwareUpdate

from .coordinator import DeviceData, ObiEnergyTrackerConfigEntry

TO_REDACT = {"token", "auth_implementation", "display_name"}


def _firmware_diagnostics(update: FirmwareUpdate | None) -> dict[str, Any] | None:
    return None if update is None else asdict(update)


def _device_diagnostics(data: DeviceData) -> dict[str, Any]:
    return {
        "bridge_id": data.bridge.id,
        "device": asdict(data.device),
        "energy": data.energy,
        "negative_energy": data.negative_energy,
        "rssi": data.rssi,
        "battery": data.battery,
        "battery_level": data.battery_level,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ObiEnergyTrackerConfigEntry
) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    diagnostics = {
        "entry": {
            "data": dict(entry.data),
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "update_interval": str(coordinator.update_interval),
        },
        "bridges": [
            {
                "id": bridge.id,
                "display_name": bridge.display_name,
                "firmware_version": bridge.firmware_version,
                "hardware_version": bridge.hardware_version,
                "ota_status": bridge.ota_status,
                "ota_progress": bridge.ota_progress,
                "firmware_update": _firmware_diagnostics(
                    data.firmware_updates.get(bridge.id)
                ),
                "sensor_ids": [device.id for device in bridge.sensors],
                "outlet_ids": [device.id for device in bridge.outlets],
            }
            for bridge in data.bridges
        ],
        "devices": {
            device_id: _device_diagnostics(device_data)
            for device_id, device_data in data.devices.items()
        },
    }
    return async_redact_data(diagnostics, TO_REDACT)
