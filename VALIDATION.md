# Validation — 0.2.0

Performed locally on 2026-11-23:

- Python 3.14.10 with **Home Assistant 2026.10.4**, imported before the test suite to use HA's voluptuous compatibility layer.
- **82 Python tests**: real HA registries, config/options flows, preset discovery, export and YAML connection handling, unavailable devices, cached names, stable ordering, admin-only editor commands, subscription cleanup, atomic/stale saves, migration of legacy records, disabled lights and an empty selection.
- **20 JavaScript tests** using Node.js 24 and jsdom: draft persistence and cancellation, profile copying, real target IDs, ambiguous names, conflicts and preset swaps, empty catalogs, filters, status/focus updates, locale initialization, HTML escaping, save failures and concurrent edits.
- Python syntax, Ruff F/I/E9, JavaScript syntax and JSON files checked.
- Archive integrity and flat root layout checked; no node_modules, Python caches or browser runtime files included.

`npm run test:browser` adds Playwright checks for desktop/mobile layout, horizontal overflow, dialogs, profiles/copying, saved drafts and stale revisions. It is included in GitHub Actions. **It was not run successfully in this workspace:** local Chromium cannot open its required socket (`Operation not permitted`), and the cloud browser does not permit local file URLs. DOM tests do not verify pixel layout or native dialog rendering.

HACS and hassfest are configured in GitHub Actions. Their remote checks were not run locally. No live Home Assistant installation, physical WLED controller, YaHA connection or Yandex account was modified during validation. Network state and preset data in the tests are fixtures.

HACS repository topics must be set in GitHub's About settings. The archive cannot change repository metadata.
