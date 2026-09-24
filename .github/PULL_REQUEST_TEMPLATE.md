## What does this change?

<!-- One or two sentences, and the issue it closes. -->

## Type of change

- [ ] Bug fix
- [ ] New feature (new entity, sensor, config flow step, …)
- [ ] Breaking change (entity IDs, unique IDs or the config entry change)
- [ ] Documentation or tooling only

## Checklist

- [ ] `pytest` passes
- [ ] `ruff check custom_components tests` and `ruff format --check custom_components tests` pass
- [ ] `mypy` passes
- [ ] Tests cover the change
- [ ] New entities or flow steps have their `translations/*.json` and `icons.json` entries
- [ ] No HTTP client and no `homeassistant`-free logic was added here instead of in the
      [library](https://github.com/OBI-Smart-Technologies/energy-tracker-api-client-python)
- [ ] `CHANGELOG.md` has an entry under `## Unreleased`
