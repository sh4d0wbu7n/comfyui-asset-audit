from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ScanIssue:
    level: str
    path: str
    message: str

    def as_dict(self) -> dict[str, Any]:
        return {"level": self.level, "path": self.path, "message": self.message}


@dataclass
class WorkflowNode:
    node_type: str
    state: str
    values: list[str] = field(default_factory=list)
    package_hints: list[str] = field(default_factory=list)


@dataclass
class WorkflowRecord:
    path: Path
    modified: float
    format: str
    nodes: list[WorkflowNode] = field(default_factory=list)
    manifest_values: list[str] = field(default_factory=list)


@dataclass
class NodePack:
    name: str
    path: Path
    size_bytes: int
    node_types: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)
    version: str = ""
    git_remote: str = ""
    disabled: bool = False
    javascript_only: bool = False
    dynamic_mapping: bool = False
    parse_errors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ModelRoot:
    category: str
    path: Path
    source: str
    priority: int
    recursive: bool = True


@dataclass
class ModelAsset:
    asset_id: str
    name: str
    category: str
    path: Path
    root: Path
    source: str
    size_bytes: int
    kind: str
    aliases: set[str] = field(default_factory=set)
