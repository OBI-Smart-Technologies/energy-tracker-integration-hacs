# Changelog

All notable changes to this integration are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and this
project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## 0.1.1 - 2026-09-24

### Fixed

- Sensors and outlets are linked to their bridge through the device id, as Home Assistant
  2026.9 expects. The previous identifier-based link is deprecated and stops working in
  Home Assistant 2027.8.
- Devices added to the OBI account later are linked to their bridge as well, and the
  firmware version shown on a device follows its updates.

## 0.1.0 - 2026-09-24

First public beta release.

### Added

- Config flow with OBI sign-in (Keycloak OAuth2 with PKCE), reauthentication and a logout
  option flow.
- Automatic discovery of every bridge, sensor and outlet of the OBI account, refreshed
  every 5 minutes.
- Consumption and feed-in sensors in watt-hours, plus battery level, signal strength,
  bridge reception and online status per device.
- Import of the meter history from the OBI cloud as long-term statistics, so the Energy
  dashboard shows data from before the setup.
- Outlet switching.
- Bridge firmware updates with progress, including the vendor change log.
- Diagnostics download with tokens and device names redacted.
- German and English translations.
- Apache License, Version 2.0, with `NOTICE`; both files ship in the HACS archive.
