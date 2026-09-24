# AGENTS.md

This repository is a Home Assistant custom integration (distributed through HACS) for OBI
ENERGY TRACKER devices. `CLAUDE.md` is a symlink to this file — never edit it directly.

## Development commands

```bash
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"

.venv/bin/python -m pytest -q
.venv/bin/ruff check custom_components tests
.venv/bin/ruff format custom_components tests
.venv/bin/mypy
```

All four checks have to pass before finishing a change. The `dev` extra installs
`obi-energy-tracker` from PyPI at the pinned version. In the dev container use
`scripts/setup`, `scripts/lint` and `scripts/develop` instead — see README's *Local Home
Assistant instance*.

## Hard rules

- **No HTTP in this repository.** Requests, payload parsing, models and the hourly
  arithmetic belong to the
  [obi-energy-tracker](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python) library.
  A change that needs both starts there, is released, and only then is the pin bumped in
  `manifest.json` **and** in the `dev` extra of `pyproject.toml` — CI fails if they differ.
  A limitation of the library gets fixed in the library, never worked around here.
- **No user-facing knobs that Home Assistant forbids.** The polling interval stays fixed —
  no `scan_interval` or update-frequency option in the config flow or the entry — and users
  do not name the config entry; the title is fixed and renaming happens in the UI.
- **No comments.** No `#`, `//`, `/* */` or docstrings, in any file. If something needs
  explaining, the code is wrong
- **No dead code and no new files without need.** No unused constants, fields or helpers,
  no scratch scripts, no extra markdown reports.
- Fully async, no blocking I/O in the event loop. The `aiohttp` session comes from
  `async_get_clientsession`.
- New entities or config-flow steps come with their `translations/*.json` and `icons.json`
  entries and a test, in the same change.
- Never commit caches, build artefacts or IDE files. `git add` new files
  immediately after creating them. Use `gh`; this is a GitHub project.

## Testing

- One `test_{module}.py` per module, under `tests/components/obi_energy_tracker/`.
- Build data with the `conftest.py` factories. Mock at the library boundary with
  `AsyncMock(spec=ObiEnergyTrackerApi)` (the `mock_api` fixture), never internal
  integration details; only the config flow's reachability check needs HTTP-level mocking
  (`mock_responses`).
- Annotate every test parameter, prefer concrete types over `Any`, and parametrize instead
  of branching inside a test.
- Parsing and the hourly arithmetic are the library's tests, not ours.

## Environment gotchas

- `[tool.mypy]` needs `mypy_path`, `explicit_package_bases` and `namespace_packages`.
  Without them mypy resolves the top-level `obi_energy_tracker` import to
  `custom_components/obi_energy_tracker/` and every library import fails.
- `[tool.setuptools] packages = []` keeps the flat repository root installable.
- Python 3.14. `asyncio_mode = auto`, so async tests need no marks.
- The integration's domain is also the library's package name, so `custom_components/`
  must never be on `PYTHONPATH`: it shadows the library and every import of it fails as
  a circular import. `scripts/develop` links `config/custom_components` instead.

## Do not "fix" these

- Day boundaries follow the Home Assistant time zone.
- Entities, diagnostics and `async_remove_config_entry_device` carry no `data is None`
  guards: the coordinator guarantees data once platforms are forwarded, and
  `warn_unreachable` reports such guards as dead code.
- `feed_in` never drops; the coordinator carries the last reading forward so a
  `total_increasing` sensor cannot fake a meter reset.
- `_statistic_readings()` fills `feed_in` with zeros at the `energy` timestamps when the
  cloud has no `negative_energy` records, so every device offers both statistics and the
  Energy dashboard's feed-in picker is never empty. It also keeps
  `_async_catch_up_duration()` honest: without the zero-fill the `feed_in` statistic has
  no row on a PV-less account, so every entry setup pulled the full history again.
- Both API paths use one request per bridge for all of its devices. The per-device meter
  route cannot serve either — it rejects `rssi` and `battery`.
- The statistics import writes **external** statistics (`async_add_external_statistics`,
  id `{DOMAIN}:{slugify(device_id)}_{key}`, `source=DOMAIN`), never
  `async_import_statistics` under a sensor entity id. Do not revert that. An entity id has
  two writers: the import writes long-term rows, while the sensor recorder platform
  compiles the same `total_increasing` entity and carries its own sum forward from the
  latest **short-term** row, which the import can never write. The baselines drift apart
  and the Energy dashboard, reading these rows as hour-to-hour `sum` deltas, renders the
  gap as one hour of hugely negative consumption while the entity's history card stays
  clean. Core does the same: `opower` (platinum), `tibber`, `solaredge` and eight others.
- The `consumption` and `feed_in` sensors carry **no** `state_class`, so the sensor
  recorder platform compiles nothing for them and each measure of each device has exactly
  one statistic. Giving them `TOTAL_INCREASING` back reintroduces both the negative spike
  and a duplicate entry per measure in every statistic picker. The entities keep their
  `states`, so history cards, templates and `utility_meter` helpers are unaffected.
- `slugify()` in the statistic id is load-bearing: device ids carry hyphens
  (`sensor-001`) and neither `VALID_STATISTIC_ID` nor `VALID_ENTITY_ID` admits one.
- The statistic metadata `name` is `{device.display_name} {label}`, where `label` comes
  from the integration's own entity translations for `hass.config.language`
  (`_async_resolve_labels()` reads `component.{DOMAIN}.entity.sensor.{key}.name`), so a
  German instance gets *Hausstrom Verbrauch* and an English one *Hausstrom Consumption*,
  matching the sensor entity names. `STATISTIC_LABEL_FALLBACKS` covers a missing
  translation. The statistic **id** stays English on purpose: it is the persistent key,
  and localising it would orphan the history on a language switch. The picker searches by
  that id, not by the translated name.
- `_baseline_shift()` anchors on the **oldest** bucket of the series it is about to
  write, read with `statistics_during_period` for that one hour. Anchoring on the newest
  row was tried and is wrong: that row is usually the running, incomplete hour, so the
  series pins it to a partial value and it never grows again.
- The recurring statistics path recomputes nothing and asks for nothing of its own.
  `_async_advance_statistics()` carries a `MeterCursor` per device and measure, holding
  the newest reading it has accounted for and the `sum` it wrote for it, and advances it
  with whatever the poll response contains beyond that timestamp. The remembered reading
  is prepended to the new ones so `hourly_buckets()` sees a predecessor for the first
  one, and the shift is simply `cursor.total`. Readings the cursor has already passed are
  filtered out, so a repeated response writes nothing.
- `MEASURES_WINDOW` is `PT15M`. The poll only has to contain the readings since the last
  cycle, and the window exists only because `/measures` requires a `duration` and an
  exact five-minute window can come back empty. Recomputing from a two-hour window was
  the previous design and is what forced a warmup bucket, a `statistics_during_period`
  read per device and measure on every cycle, and about 100 readings per device per poll
  instead of 12. Because the meter is cumulative the cursor is loss-free without any
  overlap: a missed upload only makes the next delta larger.
- `async_import_historical_statistics()` runs once per entry setup and is the only path
  that asks for a span of history. It follows core's `tibber`: the full history
  (`duration=None`, `from=start`) only when there is nothing to continue from, a bounded
  catch-up otherwise. `_async_catch_up_duration()` returns `None` as soon as any
  statistic of the bridge has no row, and otherwise takes the **oldest** last row across
  every device and measure, minus `CATCH_UP_MARGIN` (2h).
- `STATISTICS_WARMUP_BUCKETS` (1) belongs to the setup paths only, because those still
  recompute from a response: the first reading of a span has no predecessor, so the
  increment that began before it is missing from the bucket that reading falls into. The
  full-history import passes warmup 0, since there is no earlier reading it could miss.
- The cursor lives in memory and is seeded by whichever setup path ran, which is why the
  recurring path writes nothing when there is no cursor yet. It also makes the `energy`
  carry-forward free, since the remembered reading is the last known meter value.
- The app parity is gone from `obi-energy-tracker` and must not come back: it was the
  only reason an hourly bucket needed a whole day of context. Without a stored sum at the anchor hour the cycle writes
  nothing rather than restarting at zero, which is the case where Home Assistant was down
  longer than the poll window; the next entry setup closes that gap.

## Conventions

- Entity unique_id `{device_id}_{description_key}`, or `{bridge_id}_firmware` for the
  bridge's update entity.
- `entry.runtime_data` holds the coordinator; `hass.data` is not used.
- The bridge is a gateway: its only entity is the firmware update.

Home Assistant's own guidance applies even though this is not a core integration:
[integration guidelines](https://developers.home-assistant.io/docs/creating_component_index/)
and the [quality scale rules](https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/),
which are worth checking before adding a feature.
