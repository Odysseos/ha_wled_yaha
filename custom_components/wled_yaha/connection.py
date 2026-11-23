"""Connect an owned YaHA fragment to HA packages without rewriting user YAML.

PyYAML's representation nodes retain source positions and unknown !include tags.
Only insert a separate named package. Every edited user file gets an exact backup.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode

from .generator import DEFAULT_OUTPUT

PACKAGE_NAME = "wled_yaha_generated"
PACKAGE_MARKER = "# Managed by WLED YaHA; exported entities are in includes/.\n"


@dataclass
class ConnectionPlan:
    target: Path | None
    before: str
    after: str
    connected: bool


def _child(node, key):
    if not isinstance(node, MappingNode):
        raise ValueError("Ожидался YAML-словарь настроек Home Assistant.")
    matches = [v for k, v in node.value if isinstance(k, ScalarNode) and k.value == key]
    if len(matches) > 1:
        raise ValueError(
            f"В YAML несколько ключей {key}; автоматическое подключение отменено."
        )
    return matches[0] if matches else None


def _safe_path(root, base, relative):
    candidate = base / relative
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
        raise ValueError(
            "Подключаемый YAML должен находиться внутри каталога настроек HA."
        )
    return candidate


def _read(root, path):
    path = _safe_path(root, root, path.relative_to(root))
    text = path.read_text(encoding="utf-8")
    node = yaml.compose(text)
    if node is None:
        node = MappingNode("tag:yaml.org,2002:map", [])
    return text, node


def _insert(text, node, key, value, *, indent=0):
    """Insert one key; keep existing text, tags, comments and ordering verbatim."""
    if not isinstance(node, MappingNode):
        raise ValueError("Неподдерживаемая структура YAML: ожидается словарь.")
    if _child(node, "<<") is not None:
        raise ValueError(
            "Словарь использует YAML merge; автоматическое подключение отменено."
        )
    if node.flow_style:
        if node.value:
            raise ValueError(
                "Непустой словарь в фигурных скобках нельзя безопасно дополнить."
            )
        at, end = node.start_mark.index, node.end_mark.index
        replacement = "\n" + " " * indent + key + ":\n" + value
        return text[:at] + replacement.rstrip("\n") + text[end:]
    if node.value:
        indent = node.value[0][0].start_mark.column
        # Place before the first key so trailing comments retain their position.
        at = text.rfind("\n", 0, node.value[0][0].start_mark.index) + 1
    else:
        at = len(text)
    addition = " " * indent + key + ":\n" + value
    return (
        text[:at] + ("\n" if at and text[at - 1] != "\n" else "") + addition + text[at:]
    )


def plan_connection(config_dir, output_file=DEFAULT_OUTPUT):
    """Support inline packages and standard include-file/directory configurations."""
    root = Path(config_dir).resolve()
    path = root / "configuration.yaml"
    text, document = _read(root, path)
    ha = _child(document, "homeassistant")
    if ha is None:
        value = (
            "  packages:\n    "
            + PACKAGE_NAME
            + ":\n      yandex_smart_home: !include "
            + output_file
            + "\n"
        )
        after = _insert(text, document, "homeassistant", value)
        yaml.compose(after)
        return ConnectionPlan(path, text, after, False)
    if ha.tag == "!include":
        path = _safe_path(root, path.parent, ha.value)
        text, ha = _read(root, path)
    packages = _child(ha, "packages")
    if packages is None:
        indent = ha.value[0][0].start_mark.column if ha.value else 2
        rel = os.path.relpath(root / output_file, path.parent)
        value = (
            " " * (indent + 2)
            + PACKAGE_NAME
            + ":\n"
            + " " * (indent + 4)
            + "yandex_smart_home: !include "
            + rel
            + "\n"
        )
        after = _insert(text, ha, "packages", value, indent=indent)
        yaml.compose(after)
        return ConnectionPlan(path, text, after, False)
    if packages.tag in {"!include_dir_named", "!include_dir_merge_named"}:
        folder = _safe_path(root, path.parent, packages.value)
        target = _safe_path(root, folder, PACKAGE_NAME + ".yaml")
        # !include_dir_named keys each file by its filename; reject a .yml twin.
        if (
            packages.tag == "!include_dir_named"
            and (folder / (PACKAGE_NAME + ".yml")).exists()
        ):
            raise ValueError("Имя пакета WLED уже занято другим YAML-файлом.")
        rel = os.path.relpath(root / output_file, folder)
        body = "yandex_smart_home: !include " + rel + "\n"
        if packages.tag == "!include_dir_merge_named":
            body = PACKAGE_NAME + ":\n  " + body
        after = PACKAGE_MARKER + body
        before = target.read_text(encoding="utf-8") if target.exists() else ""
        if before and before != after:
            raise ValueError(f"Файл {target.name} уже занят; он не изменён.")
        return ConnectionPlan(target, before, after, before == after)
    if packages.tag == "!include":
        path = _safe_path(root, path.parent, packages.value)
        text, packages = _read(root, path)
    if not isinstance(packages, MappingNode):
        raise ValueError("Неподдерживаемый способ подключения homeassistant.packages.")
    rel = os.path.relpath(root / output_file, path.parent)
    if existing := _child(packages, PACKAGE_NAME):
        include = _child(existing, "yandex_smart_home")
        if (
            include is not None
            and include.tag == "!include"
            and _safe_path(root, path.parent, include.value).resolve()
            == (root / output_file).resolve()
        ):
            return ConnectionPlan(None, "", "", True)
        raise ValueError("Имя пакета wled_yaha_generated уже занято другой настройкой.")
    indent = (
        packages.value[0][0].start_mark.column
        if packages.value
        else (packages.start_mark.column if packages.flow_style else 4)
    )
    value = " " * (indent + 2) + "yandex_smart_home: !include " + rel + "\n"
    after = _insert(text, packages, PACKAGE_NAME, value, indent=indent)
    yaml.compose(after)
    return ConnectionPlan(path, text, after, False)


def apply_connection(plan):
    if plan.connected or plan.target is None:
        return False
    target = plan.target
    current = target.read_text(encoding="utf-8") if target.exists() else ""
    if current != plan.before:
        raise ValueError("YAML изменён после проверки; повтори экспорт.")
    target.parent.mkdir(parents=True, exist_ok=True)
    if plan.before:
        backup = target.with_name(target.name + ".wled-yaha.bak")
        if backup.exists():
            if backup.is_symlink():
                raise ValueError("Путь резервной копии является символической ссылкой.")
            if backup.read_text(encoding="utf-8") != plan.before:
                raise ValueError(
                    f"Уже существует другая копия {backup.name}; оригинал не изменён."
                )
        else:
            with backup.open("x", encoding="utf-8") as stream:
                stream.write(plan.before)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent, delete=False
        ) as stream:
            temporary = stream.name
            stream.write(plan.after)
            stream.flush()
            os.fsync(stream.fileno())
        if target.exists():
            os.chmod(temporary, target.stat().st_mode & 0o777)
        os.replace(temporary, target)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return True
