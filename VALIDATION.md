# Validation — 0.2.2

Performed locally on 2026-11-24 with Python 3.14.10 and Home Assistant 2026.10.4:

- **99 Python tests passed with no skips**, using real HA registries and unmodified YaHA 1.1.3 source (commit `7d1121923c91fdd190f3d13d75be8896aed18735`).
- The six real YaHA tests exercise the actual device-list, state-query and action request handlers: five selected offline lights appear in discovery; query and action still return DEVICE_UNREACHABLE; missing HA state is described from known registry data; original entity IDs/room are preserved; reconnecting creates no duplicate; YaHA filters and unloading are respected.
- Adapter unit tests also cover unrelated entities, disabled export selection, cached capabilities, idempotent attachment, incompatible APIs and restoring only our own binding.
- No controller connection or live Yandex account was used. The real YaHA handlers ran locally against fixture HA states; this verifies the protocol payload and errors, not receipt by a live Yandex cloud account.
- Python syntax, Ruff F/I/E9, JSON and flat archive integrity checked. The editor JavaScript is unchanged from 0.2.0; frontend/DOM and browser checks remain in CI.
- Camera modules imported by YaHA reported a missing native libturbojpeg in the test environment; no camera functionality is used by these WLED tests.

The adapter uses YaHA's internal list-provider binding. Its compatibility was verified against the pinned source above; runtime shape checks and export diagnostics report an unsupported API instead of modifying an incompatible provider. YaHA source files and HA device states are never edited.

GitHub Actions runs the pinned YaHA handler tests together with the main Python suite, plus the frontend, HACS and hassfest checks. Remote CI was not run from this workspace.
