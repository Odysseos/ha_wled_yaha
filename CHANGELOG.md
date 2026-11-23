# 0.2.1

- Stop creating the synthetic YaHA preset bridge device. Export controls and diagnostics are standalone integration entities; setup no longer asks for an area for a bridge device spanning multiple garlands.
- On update, detach the existing export entities before removing only the legacy bridge device. Preserve their registry IDs, entity IDs, custom names and disabled state.
- Keep native WLED devices and each garland's own area unchanged. Add regression tests for distinct areas and legacy-device cleanup.

# 0.2.0

- Replace the sequential configuration wizard with an admin master-detail sidebar panel: all WLED lights on the left, scene mappings on the right, with live status, search and filters.
- Continue Entity ID Manager styling and its shared draft/Confirm/Cancel workflow, including changed-value chips and optimistic revision protection against concurrent edits.
- Show known empty catalogs separately from catalogs that are not loaded. Keep unavailable lights selectable and exportable; preserve existing mappings and disabled lights' settings.
- Add batch copying by unique preset name or explicit ID, fill-empty/replace policies, mismatch/conflict reports and reusable named profiles.
- Save all devices, profiles and export options atomically without reloading the integration or closing the editor. Existing 0.1.x entries retain their mappings.
- Support an explicitly empty selection to clear only this integration's owned export. Do not export a new entry before its first confirmation.
- Keep natural ordering across discovery, editing and export. The legacy flow now skips every empty catalog and counts only visible scene pages.
- Add admin-only WebSocket endpoints, subscription cleanup, revision checks, frontend draft/DOM tests and desktop/mobile browser checks in CI.
- Include Entity ID Manager attribution in NOTICE and use GPL-3.0 for the combined 0.2.0 release; earlier release licenses are unchanged.

# 0.1.3

- Show each light's current on/off/unavailable/unknown/not-loaded state directly in the selection list, in Russian or English.
- Refresh displayed statuses when opening the list; keep state labels separate from sorting, entity identifiers and scene-step titles.

# 0.1.2

- Export selected WLED lights even when the native light or preset selector is currently unavailable.
- Preserve known scene names in configuration and recover legacy mappings from the previous owned export while offline. Unknown scenes are retried when the catalog becomes available; their IDs remain saved.
- Allow saving the configuration independently of live device availability.
- Use one explicit natural order for the device picker, scene steps and summary; break duplicate-name ties by room and entity ID.

# 0.1.1

- Allow setup without any WLED presets or Yandex scene mappings.
- Make scene fields optional, add an explicit skip option, and skip the scene form when no presets exist.
- Treat preset-selector problems as warnings when every scene is disabled; keep blocking errors for configured scenes.

# 0.1.0

Initial integration release.

- HACS repository layout and UI setup, with one integration managing multiple native WLED lights.
- Editable per-device Yandex scene mappings using actual WLED preset IDs and names.
- Stable registry identities preserve selections after entity renames.
- Automatic export after startup, configuration changes, registry updates and preset-list changes.
- Optional automatic package connection, with a backup of each modified user YAML file.
- YaHA reload, export button, status sensor and downloadable diagnostics.
- Preserve the previous export when a selected device or preset is unavailable or missing.
- Russian and English interface translations.
- Branded integration icon using the owner's original Greek ship eye SVG.
