from __future__ import annotations

import os
import re
from pathlib import Path

from .types import ModelRoot, ScanIssue
from .util import expand_environment


IGNORED_YAML_KEYS = {"base_path", "is_default", "download_model_base", "custom_nodes"}
DIRECT_MODEL_EXTENSIONS = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx",
    ".engine", ".tflite", ".pb", ".pkl", ".pickle", ".h5", ".hdf5",
    ".msgpack", ".npz", ".npy", ".model", ".weights",
}


def discover_comfy_root(supplied: Path) -> Path:
    supplied = supplied.expanduser().resolve()
    direct = [supplied, supplied / "ComfyUI", supplied / "resources" / "ComfyUI"]
    for candidate in direct:
        if (candidate / "folder_paths.py").is_file():
            return candidate

    matches: list[Path] = []
    if supplied.is_dir():
        start_depth = len(supplied.parts)
        for current, dirs, files in os.walk(supplied):
            depth = len(Path(current).parts) - start_depth
            if depth >= 4:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if d not in {".git", "models", "custom_nodes", "venv", ".venv"}]
            if "folder_paths.py" in files:
                matches.append(Path(current))
    if len(matches) == 1:
        return matches[0].resolve()
    if not matches:
        raise ValueError(
            f"Could not find folder_paths.py below '{supplied}'. "
            "Pass either the ComfyUI directory or the portable installation directory."
        )
    raise ValueError(
        "Several ComfyUI installations were found. Pass the exact directory containing folder_paths.py: "
        + ", ".join(str(p) for p in matches)
    )


def discover_custom_nodes_root(comfy_root: Path, override: Path | None = None) -> Path:
    path = (override or (comfy_root / "custom_nodes")).expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Custom-nodes directory does not exist: {path}")
    return path


def _strip_comment(value: str) -> str:
    quote = ""
    for index, char in enumerate(value):
        if char in {'"', "'"}:
            if not quote:
                quote = char
            elif quote == char:
                quote = ""
        elif char == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.rstrip()


def _unquote(value: str) -> str:
    value = _strip_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def parse_extra_model_paths(path: Path) -> list[tuple[str, str, str]]:
    """Parse the deliberately small YAML subset used by extra_model_paths.yaml.

    Returns tuples of (profile, category, path). This avoids a PyYAML dependency
    while supporting scalars, block scalars, and simple lists.
    """
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    profiles: dict[str, dict[str, list[str]]] = {}
    current_profile = ""
    current_key = ""
    block_indent = -1

    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        stripped = raw.strip()

        if indent == 0 and stripped.endswith(":"):
            current_profile = _unquote(stripped[:-1])
            profiles.setdefault(current_profile, {})
            current_key = ""
            block_indent = -1
            continue
        if not current_profile:
            continue

        if indent <= 2 and ":" in stripped:
            key, value = stripped.split(":", 1)
            current_key = _unquote(key)
            value = _unquote(value)
            profiles[current_profile].setdefault(current_key, [])
            if value in {"|", ">", ""}:
                block_indent = indent
            else:
                profiles[current_profile][current_key].append(value)
                block_indent = -1
            continue

        if current_key and indent > block_indent:
            value = stripped[1:].strip() if stripped.startswith("-") else stripped
            value = _unquote(value)
            if value:
                profiles[current_profile][current_key].append(value)

    result: list[tuple[str, str, str]] = []
    for profile, values in profiles.items():
        base_values = values.get("base_path", [])
        base = base_values[0] if base_values else ""
        for category, paths in values.items():
            if category in IGNORED_YAML_KEYS:
                continue
            for value in paths:
                result.append((profile, category, str(Path(expand_environment(base)) / expand_environment(value)) if base else expand_environment(value)))
    return result


def discover_model_roots(
    comfy_root: Path,
    model_overrides: list[Path] | None = None,
    extra_overrides: list[tuple[str, Path]] | None = None,
    include_yaml: bool = True,
) -> tuple[list[ModelRoot], list[ScanIssue]]:
    issues: list[ScanIssue] = []
    roots: list[ModelRoot] = []
    priority = 0

    base_roots = [p.expanduser().resolve() for p in model_overrides] if model_overrides else [comfy_root / "models"]
    for base in base_roots:
        if not base.is_dir():
            issues.append(ScanIssue("error", str(base), "Model root does not exist"))
            continue
        subdirs = sorted((p for p in base.iterdir() if p.is_dir() and not p.name.startswith(".")), key=lambda p: p.name.casefold())
        if subdirs:
            for subdir in subdirs:
                roots.append(ModelRoot(subdir.name, subdir.resolve(), "standard", priority))
                priority += 1
        direct_model_files = any(p.is_file() and p.suffix.casefold() in DIRECT_MODEL_EXTENSIONS for p in base.iterdir())
        if direct_model_files:
            roots.append(ModelRoot("models", base.resolve(), "standard", priority, recursive=False))
            priority += 1

    if include_yaml:
        yaml_candidates = [comfy_root / "extra_model_paths.yaml"]
        for yaml_path in yaml_candidates:
            if not yaml_path.is_file():
                continue
            try:
                for profile, category, raw_path in parse_extra_model_paths(yaml_path):
                    model_path = Path(raw_path)
                    if not model_path.is_absolute():
                        model_path = (yaml_path.parent / model_path).resolve()
                    else:
                        model_path = model_path.resolve()
                    if model_path.is_dir():
                        roots.append(ModelRoot(category, model_path, f"extra_model_paths.yaml:{profile}", priority))
                        priority += 1
                    else:
                        issues.append(ScanIssue("warning", str(model_path), f"External model path for '{category}' does not exist"))
            except OSError as exc:
                issues.append(ScanIssue("error", str(yaml_path), f"Could not read extra model paths: {exc}"))

    for category, path in extra_overrides or []:
        path = path.expanduser().resolve()
        if path.is_dir():
            roots.append(ModelRoot(category, path, "command-line", priority))
            priority += 1
        else:
            issues.append(ScanIssue("error", str(path), "Additional model path does not exist"))

    deduplicated: list[ModelRoot] = []
    seen: set[tuple[str, str]] = set()
    for root in roots:
        key = (root.category.casefold(), os.path.normcase(str(root.path)))
        if key not in seen:
            deduplicated.append(root)
            seen.add(key)
    return deduplicated, issues
