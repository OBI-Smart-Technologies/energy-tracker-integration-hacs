# OBI ENERGY TRACKER for Home Assistant

[![CI](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/actions/workflows/ci.yml/badge.svg)](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/OBI-Smart-Technologies/energy-tracker-integration-hacs?sort=semver)](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/releases)
[![HACS](https://img.shields.io/badge/HACS-custom%20repository-41BDF5.svg)](https://hacs.xyz)
[![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2026.8%2B-41BDF5.svg)](https://www.home-assistant.io)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

The official Home Assistant integration for the **OBI ENERGY TRACKER**, the electricity
meter sensor and smart outlet by OBI. Sign in with the account you use in the OBI app, and
every bridge, sensor and outlet shows up as a device in Home Assistant. Consumption and
feed-in go straight into the Energy dashboard, including the history from before the setup.
Outlets can be switched from automations, and the bridge offers its firmware updates.

> [!NOTE]
> The integration is in **beta** while the version is below 1.0. Please report anything
> that does not work as an [issue](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/issues).

![The Energy dashboard with an OBI ENERGY TRACKER as the grid meter](https://raw.githubusercontent.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/main/docs/images/energy.png)

## Contents

- [Supported devices](#supported-devices)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Setup](#setup)
- [Entities](#entities)
- [Energy dashboard](#energy-dashboard)
- [How data is updated](#how-data-is-updated)
- [Examples](#examples)
- [Known limitations](#known-limitations)
- [Troubleshooting](#troubleshooting)
- [Signing out and removal](#signing-out-and-removal)
- [Privacy](#privacy)
- [Development](#development)
- [License](#license)

## Supported devices

| Device | What it does in Home Assistant |
|---|---|
| ENERGY TRACKER Sensor | Reads the electricity meter: consumption, feed-in, battery and radio quality |
| ENERGY TRACKER Outlet | Smart plug with energy metering and an on/off switch |
| ENERGY TRACKER Bridge | The radio gateway the sensors and outlets connect through, with firmware updates |

Every device of the OBI account is added, and devices you add in the OBI app later show up
on their own. Devices you remove from the account are removed from Home Assistant as well.

## Prerequisites

- Home Assistant 2026.8 or newer.
- An OBI account with at least one ENERGY TRACKER, set up in the OBI app.
- The [My Home Assistant](https://www.home-assistant.io/integrations/my/) integration. It is
  part of `default_config`, so it is present unless it was removed on purpose. The OBI
  sign-in returns to Home Assistant through `https://my.home-assistant.io/redirect/oauth`,
  and without `my` the sign-in cannot be completed.

## Installation

### HACS (recommended)

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=OBI-Smart-Technologies&repository=energy-tracker-integration-hacs&category=integration)

Or by hand: **HACS → ⋮ → Custom repositories**, add
`https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs` with the type **Integration**, then
download *OBI ENERGY TRACKER* and restart Home Assistant.

### Manual

Download `obi_energy_tracker.zip` from the
[latest release](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/releases/latest) and
unpack it into `config/custom_components/obi_energy_tracker/`, then restart Home Assistant.

Home Assistant installs the required
[`obi-energy-tracker`](https://pypi.org/project/obi-energy-tracker/) package by itself. There
is no YAML configuration.

## Setup

[![Open your Home Assistant instance and start setting up OBI ENERGY TRACKER.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=obi_energy_tracker)

Or go to **Settings → Devices & services → Add integration → OBI ENERGY TRACKER**.

1. Home Assistant opens the OBI sign-in page. Enter the email address of your OBI account,
   then the code OBI sends to that address. There is no password.
2. The browser returns to Home Assistant through *My Home Assistant*. The first time, it asks
   for the address of your Home Assistant instance and remembers it.
3. The devices and entities are created right away, and the meter history is imported in
   the background.

The setup asks for nothing else. The update interval is fixed, and each OBI account can be
added once.

![The integration page with a bridge, a sensor and an outlet](https://raw.githubusercontent.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/main/docs/images/integration.png)

## Entities

Sensors and outlets are grouped under the bridge they connect through.

### Sensor

| Entity | Type | Description |
|---|---|---|
| Consumption | Sensor, Wh | Meter reading for energy drawn from the grid |
| Feed-in | Sensor, Wh | Meter reading for energy fed into the grid |
| Battery Level | Sensor, %, diagnostic | Charge level of the sensor battery |
| Bridge reception | Sensor, diagnostic | *Bad*, *Okay*, *Good* or *Excellent*, derived from the signal strength |
| Signal Strength | Sensor, dBm, diagnostic | Radio signal strength, disabled by default |
| Online Status | Binary sensor, diagnostic | Whether the device is connected to the bridge |

![A sensor device page](https://raw.githubusercontent.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/main/docs/images/sensor.png)

### Outlet

The outlet has the same entities as the sensor except the battery, plus:

| Entity | Type | Description |
|---|---|---|
| Outlet | Switch | Turns the outlet on and off |

![An outlet device page](https://raw.githubusercontent.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/main/docs/images/outlet.png)

### Bridge

The bridge measures nothing, so it has a single entity:

| Entity | Type | Description |
|---|---|---|
| Firmware | Update | Shows a pending bridge firmware with the OBI release notes and installs it |

**Install** starts the over-the-air update, and the progress bar follows the bridge's own
status. OBI picks the firmware, so you cannot choose a version, and no backup is taken.

The integration provides no actions of its own. The entities work with the standard
actions such as `switch.turn_on` and `update.install`.

## Energy dashboard

Set it up once under **Settings → Dashboards → Energy**:

| Energy dashboard setting | Statistic to pick |
|---|---|
| Grid consumption | *<sensor name> Consumption* |
| Return to grid | *<sensor name> Feed-in* |
| Individual devices | *<outlet name> Consumption* |

The integration imports the meter history from the OBI cloud as long-term statistics, so the
dashboard also shows the time before the setup. These statistics are named after the device
and the measure in the language of your Home Assistant instance, for example *House
Consumption*. The `sensor.` entities of the same name do not appear in the picker on purpose:
each measure has exactly one statistic, so it cannot be counted twice. The entities keep
their states for history cards, automations and templates.

The picker searches these statistics by their ID, not by the displayed name. If typing the
device name finds nothing, type `consumption` or `feed_in` instead.

## How data is updated

The integration polls the OBI cloud every 5 minutes, which is the rate at which the devices
upload their readings. One request per bridge covers all of its devices.

The meter history is imported once when the integration is set up and on every restart of
Home Assistant. The first import loads the full history of the account. Later imports only
fill the gap since the last imported hour, so a restart or a longer outage of Home
Assistant leaves no hole in the Energy dashboard.

Switching an outlet and installing a firmware update are sent to the OBI cloud immediately,
followed by a refresh.

## Examples

**Notify when the daily consumption gets high.** Create a daily
[utility meter](https://www.home-assistant.io/integrations/utility_meter/) helper on the
*Consumption* sensor, then:

```yaml
automation:
  - alias: High consumption today
    triggers:
      - trigger: numeric_state
        entity_id: sensor.house_consumption_daily
        above: 15000
    actions:
      - action: notify.persistent_notification
        data:
          message: More than 15 kWh used today.
```

**Run an appliance only while the sun is up**, for example to use your own PV power:

```yaml
automation:
  - alias: Water heater with the sun
    triggers:
      - trigger: sun
        event: sunrise
        id: "on"
      - trigger: sun
        event: sunset
        id: "off"
    actions:
      - action: "switch.turn_{{ trigger.id }}"
        target:
          entity_id: switch.water_heater_outlet
```

**Notice a sensor that dropped off**, for example because its battery is empty:

```yaml
automation:
  - alias: Energy sensor offline
    triggers:
      - trigger: state
        entity_id: binary_sensor.house_online_status
        to: "off"
        for: "00:30:00"
    actions:
      - action: notify.persistent_notification
        data:
          message: The energy sensor has not reported for 30 minutes.
```

## Known limitations

| Limitation | Details |
|---|---|
| Cloud only | There is no local access to the devices. Without internet there are no new values |
| 5-minute resolution | The devices upload every 5 minutes. There is no real-time mode |
| Only what passes the meter | Consumption and feed-in are what the meter measures towards and from the grid. Solar power used directly in the household never passes the meter, so neither the PV production nor the self-consumption is visible. Add the integration of your inverter for those |

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| The OBI page shows *Invalid parameter: redirect_uri* | The `my` integration is missing. Add it (it is part of `default_config`), restart and try again |
| Setup aborts with *Could not reach the OBI ENERGY TRACKER service* | The sign-in worked but the OBI cloud did not answer. Check the internet connection, DNS and firewall, then try again |
| Home Assistant asks you to sign in again | The OBI session expired. Sign in again with the **same** OBI account. A different account is rejected so that its devices cannot take over existing entities |
| All entities of a device are unavailable | The device does not report. Check its *Online Status*. A sensor with an empty battery drops off the bridge |
| An entity is missing | Only entities a device can report are created. *Signal Strength* is disabled by default and can be enabled on the device page |
| The Energy dashboard shows no history | The import runs in the background after setup and can take a few minutes for a long history. Check the log for `obi_energy_tracker` |

For more detail, enable debug logging on the integration page (**⋮ → Enable debug logging**),
reproduce the problem and disable it again to download the log. Then open an
[issue](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/issues) and attach the
diagnostics (**device page → Download diagnostics**). Tokens and device names are removed
from the diagnostics.

## Signing out and removal

**Sign out:** the cog on the integration entry ends the OBI session and revokes the stored
tokens. Devices, entities and history are kept, and Home Assistant asks you to sign in
again.

**Remove:** go to **Settings → Devices & services → OBI ENERGY TRACKER**, open the ⋮ menu of
the entry and select **Delete**. If you installed through HACS, remove the repository in HACS
afterwards and restart Home Assistant.

## Privacy

The integration talks to two OBI services only: the OBI sign-in at `auth.obi.com` and the
OBI ENERGY TRACKER cloud API. It sends no data to anyone else. Home Assistant stores the
OAuth token issued by the sign-in and the ID of your OBI account in its config entry, and
nothing else about your account.
The meter readings end up in the Home Assistant recorder like those of any other
integration.

## Development

Everything that talks to the OBI cloud, meaning the REST client, the models, the parsing
and the hourly arithmetic, lives in the
[obi-energy-tracker](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python)
library. This repository contains the Home Assistant side only.

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format custom_components tests
.venv/bin/mypy
```

### Local Home Assistant instance

The repository ships a [dev container](https://containers.dev). Open it in the container
(*Reopen in Container*, or `devcontainer up --workspace-folder .`), and `scripts/setup`
runs automatically. `scripts/develop` then starts Home Assistant in debug mode on
http://localhost:8123 with the integration linked into `config/custom_components`, and
`scripts/lint` runs the formatter, the linter and mypy.

`config/configuration.yaml` is the only tracked file of that instance. Everything Home
Assistant writes next to it is ignored by git, so deleting the rest of `config/` gives a
clean slate.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the checks a pull request has to pass and how
the work is split with the library. Please report security issues as described in
[SECURITY.md](SECURITY.md), not as public issues.

## License

Licensed under the [Apache License, Version 2.0](LICENSE). Copyright 2026 OBI Smart
Technologies GmbH. See [NOTICE](NOTICE).

OBI and OBI ENERGY TRACKER are trademarks and are not licensed under the Apache License.
The OBI logos, including the image files that show them, are excluded from the Apache
License and remain the property of their owner.
