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
