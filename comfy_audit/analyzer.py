from __future__ import annotations

import fnmatch
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discovery import discover_custom_nodes_root, discover_model_roots
from .models import looks_like_model_reference, scan_models
from .nodes import scan_core_node_types, scan_node_packs
from .types import ModelAsset, ScanIssue, WorkflowRecord
from .util import display_size, normalized
from .workflows import scan_workflows


def _iso_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _is_kept(name: str, path: Path, patterns: list[str]) -> bool:
    candidates = [name.casefold(), str(path).casefold(), path.as_posix().casefold()]
    return any(fnmatch.fnmatch(candidate, pattern.casefold()) for pattern in patterns for candidate in candidates)


def _workflow_summary(records: list[WorkflowRecord]) -> list[dict[str, Any]]:
    return [
        {
            "path": str(record.path),
            "modified": _iso_timestamp(record.modified),
            "format": record.format,
            "node_count": len(record.nodes),
        }
        for record in records
    ]


def analyze(
    comfy_root: Path,
    workflow_roots: list[Path],
    custom_nodes_override: Path | None = None,
    model_overrides: list[Path] | None = None,
    extra_model_overrides: list[tuple[str, Path]] | None = None,
    include_yaml: bool = True,
    keep_node_packs: list[str] | None = None,
    keep_models: list[str] | None = None,
) -> dict[str, Any]:
    keep_node_packs = keep_node_packs or []
    keep_models = keep_models or []
    issues: list[ScanIssue] = []

    custom_nodes_root = discover_custom_nodes_root(comfy_root, custom_nodes_override)
    model_roots, discovery_issues = discover_model_roots(
        comfy_root, model_overrides, extra_model_overrides, include_yaml
    )
    issues.extend(discovery_issues)

    workflows, workflow_issues = scan_workflows(workflow_roots)
    packs, node_issues = scan_node_packs(custom_nodes_root)
    core_types, core_issues = scan_core_node_types(comfy_root)
    models, model_issues = scan_models(model_roots)
    issues.extend(workflow_issues + node_issues + core_issues + model_issues)

    type_to_packs: dict[str, list[int]] = defaultdict(list)
    alias_to_packs: dict[str, list[int]] = defaultdict(list)
    for index, pack in enumerate(packs):
        for node_type in pack.node_types:
            type_to_packs[node_type].append(index)
        for alias in pack.aliases:
            alias_to_packs[alias].append(index)

    pack_references: dict[int, list[dict[str, str]]] = defaultdict(list)
    unresolved_nodes: list[dict[str, Any]] = []
    for workflow in workflows:
        for node in workflow.nodes:
            candidates = set(type_to_packs.get(node.node_type, []))
            for hint in node.package_hints:
                candidates.update(alias_to_packs.get(normalized(hint), []))
            if len(candidates) == 1:
                pack_index = next(iter(candidates))
                pack_references[pack_index].append(
                    {"workflow": str(workflow.path), "node_type": node.node_type, "state": node.state}
                )
            elif len(candidates) > 1:
                unresolved_nodes.append(
                    {
                        "workflow": str(workflow.path),
                        "node_type": node.node_type,
                        "state": node.state,
                        "reason": "Several installed packs claim this node type",
                        "candidates": [packs[index].name for index in sorted(candidates)],
                    }
                )
            elif node.node_type not in core_types:
                unresolved_nodes.append(
                    {
                        "workflow": str(workflow.path),
                        "node_type": node.node_type,
                        "state": node.state,
                        "reason": "No installed pack or inspected core mapping claims this node type",
                        "candidates": [],
                    }
                )

    pack_results: list[dict[str, Any]] = []
    for index, pack in enumerate(packs):
        references = pack_references.get(index, [])
        active = sum(ref["state"] == "active" for ref in references)
        inactive = len(references) - active
        kept = _is_kept(pack.name, pack.path, keep_node_packs)
        if kept:
            status, confidence, reason = "protected", "high", "Matched a keep pattern"
        elif active:
            status, confidence, reason = "referenced", "high", "At least one active workflow node maps to this pack"
        elif inactive:
            status, confidence, reason = "bypassed_only", "high", "Referenced only by bypassed or muted workflow nodes"
        elif pack.disabled:
            status, confidence, reason = "disabled", "high", "The installed directory is already marked as disabled"
        elif pack.javascript_only:
            status, confidence, reason = "unknown", "low", "Frontend-only extensions cannot be proven unused from workflow node types"
        elif pack.dynamic_mapping or pack.parse_errors:
            status, confidence, reason = "unknown", "low", "The pack uses dynamic or incompletely parsed node registration"
        elif pack.node_types:
            status, confidence, reason = "unreferenced", "high", "None of the statically registered node types occur in the supplied workflows"
        else:
            status, confidence, reason = "unknown", "low", "No static node registration could be identified"
        pack_results.append(
            {
                "name": pack.name,
                "path": str(pack.path),
                "version": pack.version,
                "git_remote": pack.git_remote,
                "size_bytes": pack.size_bytes,
                "size": display_size(pack.size_bytes),
                "registered_node_count": len(pack.node_types),
                "registered_node_types": sorted(pack.node_types),
                "reference_count": len(references),
                "active_reference_count": active,
                "inactive_reference_count": inactive,
                "referencing_workflows": sorted({ref["workflow"] for ref in references}),
                "references": references,
                "status": status,
                "confidence": confidence,
                "reason": reason,
            }
        )

    alias_map: dict[str, list[int]] = defaultdict(list)
    stem_map: dict[str, list[int]] = defaultdict(list)
    for index, asset in enumerate(models):
        for alias in asset.aliases:
            alias_map[alias].append(index)
        if asset.kind == "file":
            stem_map[normalized(asset.path.stem)].append(index)

    model_references: dict[int, list[dict[str, str]]] = defaultdict(list)
    ambiguous_model_references: dict[int, list[dict[str, str]]] = defaultdict(list)
    unresolved_model_references: list[dict[str, str]] = []
    seen_reference_keys: set[tuple[str, str, str, str]] = set()

    def match_reference(value: str, workflow: WorkflowRecord, node_type: str, state: str, source: str) -> None:
        ref = normalized(value)
        if not ref:
            return
        key = (str(workflow.path), ref, node_type, state)
        if key in seen_reference_keys:
            return
        seen_reference_keys.add(key)
        candidates = set(alias_map.get(ref, []))
        match_confidence = "high"
        if not candidates and "/" in ref:
            suffix_matches = {
                index for alias, indexes in alias_map.items()
                if alias and ref.endswith("/" + alias)
                for index in indexes
            }
            candidates.update(suffix_matches)
        if not candidates and "." not in Path(ref).name:
            candidates.update(stem_map.get(ref, []))
            if candidates:
                match_confidence = "medium"
        reference = {
            "workflow": str(workflow.path),
            "node_type": node_type,
            "state": state,
            "source": source,
            "value": value,
            "match_confidence": match_confidence,
        }
        if len(candidates) == 1:
            model_references[next(iter(candidates))].append(reference)
        elif len(candidates) > 1:
            for index in candidates:
                ambiguous_model_references[index].append(reference)
        elif looks_like_model_reference(value):
            unresolved_model_references.append(reference)

    for workflow in workflows:
        for node in workflow.nodes:
            for value in node.values:
                match_reference(value, workflow, node.node_type, node.state, "node_value")
        for value in workflow.manifest_values:
            match_reference(value, workflow, "<workflow manifest>", "active", "model_manifest")

    model_results: list[dict[str, Any]] = []
    for index, asset in enumerate(models):
        references = model_references.get(index, [])
        ambiguous = ambiguous_model_references.get(index, [])
        active = sum(ref["state"] == "active" for ref in references)
        inactive = len(references) - active
        reference_confidence = "high" if any(ref["match_confidence"] == "high" for ref in references) else "medium"
        kept = _is_kept(asset.name, asset.path, keep_models)
        if kept:
            status, confidence, reason = "protected", "high", "Matched a keep pattern"
        elif active:
            status, confidence, reason = "referenced", reference_confidence, "A unique workflow value matches this asset"
        elif inactive:
            status, confidence, reason = "bypassed_only", reference_confidence, "Referenced only by bypassed or muted workflow nodes"
        elif ambiguous:
            status, confidence, reason = "ambiguous", "low", "A workflow value matches more than one physical model asset"
        else:
            status, confidence, reason = "unreferenced", "medium", "No exact reference was found in the supplied workflows"
        model_results.append(
            {
                "asset_id": asset.asset_id,
                "name": asset.name,
                "category": asset.category,
                "path": str(asset.path),
                "root": str(asset.root),
                "source": asset.source,
                "kind": asset.kind,
                "size_bytes": asset.size_bytes,
                "size": display_size(asset.size_bytes),
                "reference_count": len(references),
                "ambiguous_reference_count": len(ambiguous),
                "referencing_workflows": sorted({ref["workflow"] for ref in references + ambiguous}),
                "references": references,
                "ambiguous_references": ambiguous,
                "status": status,
                "confidence": confidence,
                "reason": reason,
            }
        )

    total_model_size = sum(item["size_bytes"] for item in model_results)
    reclaimable_model_size = sum(item["size_bytes"] for item in model_results if item["status"] == "unreferenced")
    reclaimable_pack_size = sum(item["size_bytes"] for item in pack_results if item["status"] == "unreferenced")
    invalid_workflows = sum(
        (issue.level == "error")
        and ("workflow" in issue.message.casefold() or "invalid json" in issue.message.casefold())
        for issue in workflow_issues
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope_statement": "Unreferenced means not referenced by the supplied workflow collection; it does not prove that an asset has never been used.",
        "configuration": {
            "comfy_root": str(comfy_root),
            "custom_nodes_root": str(custom_nodes_root),
            "workflow_roots": [str(path.expanduser().resolve()) for path in workflow_roots],
            "model_roots": [
                {"category": root.category, "path": str(root.path), "source": root.source, "priority": root.priority, "recursive": root.recursive}
                for root in model_roots
            ],
            "keep_node_packs": keep_node_packs,
            "keep_models": keep_models,
        },
        "summary": {
            "workflows_scanned": len(workflows),
            "workflow_nodes_scanned": sum(len(workflow.nodes) for workflow in workflows),
            "invalid_workflows": invalid_workflows,
            "custom_node_packs": len(pack_results),
            "unreferenced_custom_node_packs": sum(item["status"] == "unreferenced" for item in pack_results),
            "unknown_custom_node_packs": sum(item["status"] == "unknown" for item in pack_results),
            "models": len(model_results),
            "total_model_size_bytes": total_model_size,
            "total_model_size": display_size(total_model_size),
            "potentially_reclaimable_model_size_bytes": reclaimable_model_size,
            "potentially_reclaimable_model_size": display_size(reclaimable_model_size),
            "potentially_reclaimable_node_pack_size_bytes": reclaimable_pack_size,
            "potentially_reclaimable_node_pack_size": display_size(reclaimable_pack_size),
            "unresolved_node_references": len(unresolved_nodes),
            "unresolved_model_references": len(unresolved_model_references),
            "issues": len(issues),
        },
        "workflows": _workflow_summary(workflows),
        "custom_node_packs": sorted(pack_results, key=lambda item: (item["status"], item["name"].casefold())),
        "models": sorted(model_results, key=lambda item: (item["status"], -item["size_bytes"], item["name"].casefold())),
        "unresolved_nodes": unresolved_nodes,
        "unresolved_model_references": unresolved_model_references,
        "issues": [issue.as_dict() for issue in issues],
    }
