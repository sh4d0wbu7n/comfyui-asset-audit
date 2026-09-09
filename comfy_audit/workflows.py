from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .types import ScanIssue, WorkflowNode, WorkflowRecord


PACKAGE_HINT_KEYS = {
    "cnr_id", "aux_id", "package", "package_id", "pack", "packname",
    "node_pack", "nodepack", "registry_id",
}


def _strings(value: Any, limit: int = 4096) -> list[str]:
    result: list[str] = []
    if isinstance(value, str):
        if 0 < len(value) <= limit:
            result.append(value)
    elif isinstance(value, list):
        for item in value:
            result.extend(_strings(item, limit))
    elif isinstance(value, dict):
        for item in value.values():
            result.extend(_strings(item, limit))
    return result


def _node_state(mode: Any) -> str:
    if mode in (2, "2", "never", "muted", "disabled"):
        return "muted"
    if mode in (4, "4", "bypass", "bypassed"):
        return "bypassed"
    return "active"


def _package_hints(properties: Any) -> list[str]:
    if not isinstance(properties, dict):
        return []
    hints: list[str] = []
    for key, value in properties.items():
        if str(key).casefold().replace(" ", "_") in PACKAGE_HINT_KEYS and isinstance(value, str) and value.strip():
            hints.append(value.strip())
    return hints


def _extract_nodes(data: Any) -> tuple[list[WorkflowNode], str]:
    nodes: list[WorkflowNode] = []
    seen_objects: set[int] = set()
    saw_ui = False
    saw_api = False

    def visit(value: Any) -> None:
        nonlocal saw_ui, saw_api
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen_objects:
                return
            seen_objects.add(identity)

            node_type = value.get("class_type")
            if isinstance(node_type, str):
                saw_api = True
                nodes.append(
                    WorkflowNode(
                        node_type=node_type,
                        state="active",
                        values=_strings(value.get("inputs", {})),
                        package_hints=_package_hints(value.get("properties", {})),
                    )
                )
            elif isinstance(value.get("type"), str) and ("id" in value or "widgets_values" in value):
                saw_ui = True
                nodes.append(
                    WorkflowNode(
                        node_type=value["type"],
                        state=_node_state(value.get("mode")),
                        values=_strings(value.get("widgets_values", [])),
                        package_hints=_package_hints(value.get("properties", {})),
                    )
                )
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(data)
    if saw_ui and saw_api:
        return nodes, "mixed"
    if saw_ui:
        return nodes, "workflow"
    if saw_api:
        return nodes, "api"
    return nodes, "unknown"


def _extract_manifest_values(data: Any) -> list[str]:
    values: list[str] = []
    seen: set[int] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                return
            seen.add(identity)
            models = value.get("models")
            if isinstance(models, list):
                for model in models:
                    if not isinstance(model, dict):
                        continue
                    name = model.get("name")
                    directory = model.get("directory")
                    if isinstance(name, str):
                        values.append(name)
                        if isinstance(directory, str) and directory:
                            values.append(f"{directory}/{name}")
            for nested in value.values():
                visit(nested)
        elif isinstance(value, list):
            for nested in value:
                visit(nested)

    visit(data)
    return values


def scan_workflows(roots: list[Path]) -> tuple[list[WorkflowRecord], list[ScanIssue]]:
    records: list[WorkflowRecord] = []
    issues: list[ScanIssue] = []
    seen_paths: set[str] = set()

    for supplied in roots:
        supplied = supplied.expanduser().resolve()
        if supplied.is_file():
            candidates = [supplied] if supplied.suffix.casefold() == ".json" else []
        elif supplied.is_dir():
            candidates = []
            for current, dirs, files in os.walk(supplied, followlinks=False):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"}]
                candidates.extend(Path(current) / name for name in files if name.casefold().endswith(".json"))
        else:
            issues.append(ScanIssue("error", str(supplied), "Workflow path does not exist"))
            continue

        for path in sorted(candidates):
            canonical = os.path.normcase(str(path.resolve()))
            if canonical in seen_paths:
                continue
            seen_paths.add(canonical)
            try:
                data = json.loads(path.read_text(encoding="utf-8-sig"))
                nodes, workflow_format = _extract_nodes(data)
                if not nodes:
                    issues.append(ScanIssue("warning", str(path), "JSON file contains no recognizable ComfyUI nodes"))
                    continue
                records.append(
                    WorkflowRecord(
                        path=path.resolve(),
                        modified=path.stat().st_mtime,
                        format=workflow_format,
                        nodes=nodes,
                        manifest_values=_extract_manifest_values(data),
                    )
                )
            except json.JSONDecodeError as exc:
                issues.append(ScanIssue("error", str(path), f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}"))
            except OSError as exc:
                issues.append(ScanIssue("error", str(path), f"Could not read workflow: {exc}"))
    return records, issues

