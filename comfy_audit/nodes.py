from __future__ import annotations

import ast
import configparser
import os
import re
from pathlib import Path

from .types import NodePack, ScanIssue
from .util import directory_size, iter_files, normalized


class MappingVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.saw_mapping = False
        self.dynamic = False

    @staticmethod
    def _target_is_mapping(target: ast.AST) -> bool:
        return isinstance(target, ast.Name) and target.id.endswith("NODE_CLASS_MAPPINGS")

    def _dict_keys(self, value: ast.AST) -> None:
        if not isinstance(value, ast.Dict):
            self.dynamic = True
            return
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                self.names.add(key.value)
            elif key is not None:
                self.dynamic = True

    def visit_Assign(self, node: ast.Assign) -> None:
        if any(self._target_is_mapping(target) for target in node.targets):
            self.saw_mapping = True
            self._dict_keys(node.value)
        for target in node.targets:
            if isinstance(target, ast.Subscript) and self._target_is_mapping(target.value):
                self.saw_mapping = True
                key = target.slice
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    self.names.add(key.value)
                else:
                    self.dynamic = True
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if self._target_is_mapping(node.target):
            self.saw_mapping = True
            if node.value is not None:
                self._dict_keys(node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        function = node.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr == "update"
            and self._target_is_mapping(function.value)
        ):
            self.saw_mapping = True
            if node.args:
                self._dict_keys(node.args[0])
            else:
                self.dynamic = True
        self.generic_visit(node)


def _read_pyproject(path: Path) -> tuple[str, str, set[str]]:
    version = ""
    project_name = ""
    aliases: set[str] = set()
    try:
        import tomllib  # Python 3.11+

        data = tomllib.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
        project = data.get("project", {})
        if isinstance(project, dict):
            project_name = str(project.get("name", ""))
            version = str(project.get("version", ""))
        comfy = data.get("tool", {}).get("comfy", {})
        if isinstance(comfy, dict):
            for key in ("PublisherId", "DisplayName", "publisher_id", "display_name"):
                value = comfy.get(key)
                if isinstance(value, str) and value:
                    aliases.add(value)
    except (OSError, ValueError, AttributeError, ModuleNotFoundError):
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            project_match = re.search(r"(?ms)^\[project\].*?^name\s*=\s*['\"]([^'\"]+)", text)
            version_match = re.search(r"(?ms)^\[project\].*?^version\s*=\s*['\"]([^'\"]+)", text)
            project_name = project_match.group(1) if project_match else ""
            version = version_match.group(1) if version_match else ""
        except OSError:
            pass
    if project_name:
        aliases.add(project_name)
    return project_name, version, aliases


def _git_remote(path: Path) -> str:
    config_path = path / ".git" / "config"
    if not config_path.is_file():
        return ""
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path, encoding="utf-8")
        section = 'remote "origin"'
        return parser.get(section, "url", fallback="")
    except (configparser.Error, OSError):
        return ""


def _repo_slug(remote: str) -> str:
    if not remote:
        return ""
    value = remote.rstrip("/").removesuffix(".git").replace("\\", "/")
    return value.rsplit("/", 1)[-1].rsplit(":", 1)[-1]


def _scan_mapping_files(paths: list[Path]) -> tuple[set[str], bool, list[str], bool]:
    names: set[str] = set()
    dynamic = False
    errors: list[str] = []
    saw_mapping = False
    for python_file in paths:
        try:
            if python_file.stat().st_size > 4 * 1024 * 1024:
                errors.append(f"Skipped unusually large Python file: {python_file}")
                continue
            tree = ast.parse(python_file.read_text(encoding="utf-8-sig", errors="replace"), filename=str(python_file))
            visitor = MappingVisitor()
            visitor.visit(tree)
            names.update(visitor.names)
            dynamic = dynamic or visitor.dynamic
            saw_mapping = saw_mapping or visitor.saw_mapping
        except (SyntaxError, OSError) as exc:
            errors.append(f"{python_file}: {exc}")
    return names, dynamic, errors, saw_mapping


def scan_node_packs(custom_nodes_root: Path) -> tuple[list[NodePack], list[ScanIssue]]:
    packs: list[NodePack] = []
    issues: list[ScanIssue] = []
    try:
        entries = sorted(custom_nodes_root.iterdir(), key=lambda p: p.name.casefold())
    except OSError as exc:
        return [], [ScanIssue("error", str(custom_nodes_root), f"Could not list custom nodes: {exc}")]

    for path in entries:
        if path.name.startswith(".") or path.name == "__pycache__":
            continue
        if path.is_file() and path.suffix.casefold() != ".py":
            continue
        if not path.is_dir() and not path.is_file():
            continue

        python_files = [path] if path.is_file() else list(iter_files(path, {".py"}))
        node_types, dynamic, parse_errors, saw_mapping = _scan_mapping_files(python_files)
        aliases = {path.stem if path.is_file() else path.name}
        version = ""
        remote = ""
        if path.is_dir():
            pyproject = path / "pyproject.toml"
            if pyproject.is_file():
                _, version, metadata_aliases = _read_pyproject(pyproject)
                aliases.update(metadata_aliases)
            remote = _git_remote(path)
            slug = _repo_slug(remote)
            if slug:
                aliases.add(slug)

        has_javascript = bool(path.is_dir() and any(iter_files(path, {".js", ".ts"})))
        javascript_only = has_javascript and not python_files
        pack = NodePack(
            name=path.stem if path.is_file() else path.name,
            path=path.resolve(),
            size_bytes=path.stat().st_size if path.is_file() else directory_size(path),
            node_types=node_types,
            aliases={normalized(alias) for alias in aliases if alias},
            version=version,
            git_remote=remote,
            disabled=path.name.casefold().endswith((".disabled", ".disable")),
            javascript_only=javascript_only,
            dynamic_mapping=dynamic or (saw_mapping and not node_types),
            parse_errors=parse_errors,
        )
        packs.append(pack)
        for error in parse_errors:
            issues.append(ScanIssue("warning", str(path), f"Static node mapping incomplete: {error}"))
    return packs, issues


def scan_core_node_types(comfy_root: Path) -> tuple[set[str], list[ScanIssue]]:
    files: list[Path] = []
    if (comfy_root / "nodes.py").is_file():
        files.append(comfy_root / "nodes.py")
    extras = comfy_root / "comfy_extras"
    if extras.is_dir():
        files.extend(iter_files(extras, {".py"}))
    names, _, errors, _ = _scan_mapping_files(files)
    issues = [ScanIssue("warning", str(comfy_root), f"Could not fully inspect core nodes: {error}") for error in errors]
    return names, issues

