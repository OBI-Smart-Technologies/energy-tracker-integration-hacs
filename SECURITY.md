# Security Policy

## Supported versions

Only the latest release of the integration receives security fixes. Keep Home Assistant
and the integration up to date through HACS.

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it through GitHub's private vulnerability reporting: open the
[Security tab](https://github.com/OBI-Smart-Technologies/energy-tracker-integration-hacs/security/advisories/new)
of this repository and file a draft advisory. Only the maintainers can see it.

Include the affected version, what an attacker could do, and how to reproduce it. Never
attach an unredacted diagnostics download, a Home Assistant log with tokens in it, or your
OBI credentials.

We aim to acknowledge a report within five working days and to ship a fix or a mitigation
before the advisory is published.

## Scope

This repository is the Home Assistant integration. The REST client lives in
[obi-energy-tracker](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python) — report issues
in request handling there. Issues in the OBI ENERGY TRACKER cloud service, in the OBI
account system or in Home Assistant core are out of scope here; report those to OBI or to
the Home Assistant project directly.

## What the integration stores

The config entry holds the OAuth2 token issued by the OBI sign-in and the name of the auth
implementation, nothing else. The diagnostics download redacts both, as well as the device
names, which often contain an address, so it can be attached to a public bug report as is.
