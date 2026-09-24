# Contributing

Thanks for taking the time to contribute. This repository is the Home Assistant
integration; everything that talks HTTP to the OBI cloud lives in the
[obi-energy-tracker](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python) package.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Which repository?

| Change | Repository |
|---|---|
| Entities, config flow, coordinator, translations, icons, diagnostics | here |
| New endpoint, payload parsing, models, hourly/daily arithmetic | [obi-energy-tracker](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python) |

A change that needs both starts in the library: release it, then bump the pin in
`manifest.json` and in the `dev` extra of `pyproject.toml` here — CI fails if the two
disagree.

## Development setup

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

The `dev` extra installs `obi-energy-tracker` from PyPI at the version pinned in
`manifest.json`.

## Checks

All four have to pass before a pull request can be merged; CI runs exactly these plus
Home Assistant's `hassfest` and the HACS validation:

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format --check custom_components tests
.venv/bin/mypy
```

## Ground rules

- **No comments.** If something needs explaining, the code is wrong or the explanation
  belongs in `AGENTS.md` or `README.md`.
- **New entities and flow steps come with their translations, icons and tests** in the same
  change: `translations/*.json`, `icons.json` and a test.
- **Fully async, strictly typed.** `mypy --strict` stays clean, the `aiohttp` session comes
  from `async_get_clientsession`, and nothing blocks the event loop.

## Pull requests

1. Branch off `main` (`feat/…`, `fix/…`, `docs/…`).
2. Keep the change focused, and add tests for it.
3. Add an entry under `## Unreleased` in [CHANGELOG.md](CHANGELOG.md).
4. Open the pull request and fill in the template.

## License

The repository is licensed under the Apache License, Version 2.0, and the copyright holder
is OBI Smart Technologies GmbH. Contributions are accepted under the same license (see
section 5 of the license). Do not remove `LICENSE` or `NOTICE`: the release workflow
refuses to run without a `LICENSE` file, and both ship in the HACS archive.

## Releasing

Releases are cut manually by the repository owners.

1. Bump `version` in `custom_components/obi_energy_tracker/manifest.json` and rename the
   `## Unreleased` heading in `CHANGELOG.md` to `## X.Y.Z - YYYY-MM-DD`, leaving a fresh
   empty `## Unreleased` above it.
2. Merge that to `main`.
3. Run the **Release** workflow (Actions → Release → *Run workflow*). It only runs on
   `main`, refuses to continue if `vX.Y.Z` is already tagged, builds
   `obi_energy_tracker.zip`, and creates the tag and the GitHub release that HACS then
   offers as an update.

The version of `obi-energy-tracker` pinned in `manifest.json` has to be on PyPI before the
release: Home Assistant installs it into the user's instance on setup, and an unresolvable
requirement means the integration never loads.

The `release` GitHub environment lists the repository owners as required reviewers, so
starting the workflow is not enough to publish.
