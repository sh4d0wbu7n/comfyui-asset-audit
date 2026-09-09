from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Iterable, Iterator


SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def normalized(value: str) -> str:
    value = value.strip().strip('"').strip("'").replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    return value.casefold().lstrip("./")


def stable_id(path: Path) -> str:
    return hashlib.sha1(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:12]


def display_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def directory_size(path: Path, skip_git: bool = False) -> int:
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            dirs[:] = [
                d for d in dirs
                if d not in SKIP_DIRS and not (skip_git and d == ".git")
            ]
            for filename in files:
                try:
                    total += (Path(root) / filename).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def iter_files(root: Path, suffixes: Iterable[str] | None = None) -> Iterator[Path]:
    wanted = {s.casefold() for s in suffixes} if suffixes else None
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and d != ".git"]
        for filename in files:
            path = Path(current) / filename
            if wanted is None or path.suffix.casefold() in wanted:
                yield path


def expand_environment(value: str) -> str:
    value = os.path.expandvars(os.path.expanduser(value))

    def replace(match: re.Match[str]) -> str:
        return os.environ.get(match.group(1), match.group(0))

    return re.sub(r"%([^%]+)%", replace, value)

