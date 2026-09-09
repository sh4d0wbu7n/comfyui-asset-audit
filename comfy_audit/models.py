from __future__ import annotations

import json
import os
from pathlib import Path

from .types import ModelAsset, ModelRoot, ScanIssue
from .util import directory_size, normalized, stable_id


MODEL_EXTENSIONS = {
    ".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".onnx",
    ".engine", ".tflite", ".pb", ".pkl", ".pickle", ".h5", ".hdf5",
    ".msgpack", ".npz", ".npy", ".model", ".weights",
}
SIDECAR_EXTENSIONS = {".json", ".yaml", ".yml", ".png", ".jpg", ".jpeg", ".webp", ".txt"}


def _is_bundle(path: Path, files: list[str]) -> bool:
    lowered = {name.casefold() for name in files}
    if "model_index.json" in lowered:
        return True
    index_files = [name for name in files if name.casefold().endswith((".safetensors.index.json", ".bin.index.json"))]
    if index_files:
        return True
    if "config.json" not in lowered:
        return False
    config_path = path / next(name for name in files if name.casefold() == "config.json")
    try:
        if config_path.stat().st_size > 2 * 1024 * 1024:
            return False
        data = json.loads(config_path.read_text(encoding="utf-8-sig", errors="replace"))
        markers = {"model_type", "architectures", "transformers_version", "_class_name"}
        return isinstance(data, dict) and bool(markers.intersection(data)) and any(Path(name).suffix.casefold() in MODEL_EXTENSIONS for name in files)
    except (OSError, ValueError, StopIteration):
        return False


def _aliases_for(path: Path, root: ModelRoot, kind: str) -> set[str]:
    aliases = {normalized(path.name)}
    try:
        relative = path.relative_to(root.path)
        aliases.add(normalized(relative.as_posix()))
        aliases.add(normalized(f"{root.category}/{relative.as_posix()}"))
    except ValueError:
        pass
    aliases.add(normalized(str(path)))
    return {alias for alias in aliases if alias}


def scan_models(roots: list[ModelRoot]) -> tuple[list[ModelAsset], list[ScanIssue]]:
    assets: list[ModelAsset] = []
    issues: list[ScanIssue] = []
    seen_paths: set[str] = set()

    for model_root in roots:
        try:
            for current, dirs, files in os.walk(model_root.path, topdown=True, followlinks=False):
                dirs[:] = [d for d in dirs if d not in {".git", "__pycache__"} and not d.startswith(".")]
                if not model_root.recursive:
                    dirs[:] = []
                directory = Path(current)
                if _is_bundle(directory, files):
                    canonical = os.path.normcase(str(directory.resolve()))
                    if canonical not in seen_paths:
                        assets.append(
                            ModelAsset(
                                asset_id=stable_id(directory),
                                name=directory.name,
                                category=model_root.category,
                                path=directory.resolve(),
                                root=model_root.path,
                                source=model_root.source,
                                size_bytes=directory_size(directory),
                                kind="directory",
                                aliases=_aliases_for(directory.resolve(), model_root, "directory"),
                            )
                        )
                        seen_paths.add(canonical)
                    dirs[:] = []
                    continue

                file_paths = {name: directory / name for name in files}
                for filename, path in file_paths.items():
                    if path.suffix.casefold() not in MODEL_EXTENSIONS:
                        continue
                    canonical = os.path.normcase(str(path.resolve()))
                    if canonical in seen_paths:
                        continue
                    size = 0
                    try:
                        size = path.stat().st_size
                    except OSError as exc:
                        issues.append(ScanIssue("warning", str(path), f"Could not read model size: {exc}"))
                    for sidecar_ext in SIDECAR_EXTENSIONS:
                        sidecar = path.with_suffix(sidecar_ext)
                        if sidecar.is_file():
                            try:
                                size += sidecar.stat().st_size
                            except OSError:
                                pass
                    assets.append(
                        ModelAsset(
                            asset_id=stable_id(path),
                            name=filename,
                            category=model_root.category,
                            path=path.resolve(),
                            root=model_root.path,
                            source=model_root.source,
                            size_bytes=size,
                            kind="file",
                            aliases=_aliases_for(path.resolve(), model_root, "file"),
                        )
                    )
                    seen_paths.add(canonical)
        except OSError as exc:
            issues.append(ScanIssue("error", str(model_root.path), f"Could not scan model directory: {exc}"))
    return assets, issues


def looks_like_model_reference(value: str) -> bool:
    lowered = value.strip().replace("\\", "/").casefold()
    return any(lowered.endswith(extension) for extension in MODEL_EXTENSIONS)
