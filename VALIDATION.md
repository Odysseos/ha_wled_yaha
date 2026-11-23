# Validation — 0.2.1

Performed locally on 2026-11-23 with Python 3.14.10 and Home Assistant 2026.10.4:

- **85 Python tests passed**, including new checks for standalone export entities, legacy-device cleanup preserving entity IDs/custom names/disabled state, and garlands in distinct HA areas.
- Cleanup uses entry-scoped registry lookup. Export entities are detached before removing the old device, so HA does not delete their registry entries.
- Native WLED devices, area assignments and saved scene mappings are unchanged.
- Ruff F/I/E9, Python syntax, JSON and archive integrity checked.
- The frontend JavaScript is byte-for-byte identical to 0.2.0. Its 20 JavaScript/DOM tests passed for 0.2.0; they were not repeated for this backend-only patch. The standalone demo subsequently verified desktop rendering, copying and profiles in a browser.
- No live HA installation or physical device was modified. New checks use real HA registries with fixture data.

GitHub Actions continues to run Python, JavaScript/DOM, browser, HACS and hassfest checks. Remote checks were not run from this workspace. The archive has a flat root and excludes dependency folders and caches.
