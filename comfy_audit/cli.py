from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from . import __version__
from .analyzer import analyze
from .discovery import discover_comfy_root
from .reports import write_reports


def _extra_model_path(value: str) -> tuple[str, Path]:
    if "=" in value:
        category, path = value.split("=", 1)
        if category.strip() and path.strip():
            return category.strip(), Path(path.strip())
    return "external", Path(value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="comfy-audit",
        description="Find ComfyUI custom-node packs and models not referenced by a supplied workflow collection.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)
    scan = subparsers.add_parser("scan", help="Run a read-only audit")
    scan.add_argument("--comfy-root", required=True, type=Path, help="ComfyUI directory or portable installation directory")
    scan.add_argument("--workflows", required=True, action="append", type=Path, help="Workflow file or folder; repeat for multiple locations")
    scan.add_argument("--output", type=Path, help="Report directory (default: timestamped directory in the current folder)")
    scan.add_argument("--custom-nodes-root", type=Path, help="Override the detected custom_nodes directory")
    scan.add_argument("--models-root", action="append", type=Path, help="Override models root; repeat for multiple roots")
    scan.add_argument(
        "--extra-model-path",
        action="append",
        default=[],
        metavar="[CATEGORY=]PATH",
        help="Add an external model path; repeat as needed",
    )
    scan.add_argument("--no-extra-model-paths", action="store_true", help="Do not read extra_model_paths.yaml")
    scan.add_argument("--keep-node-pack", action="append", default=[], help="Protect a node-pack name/path glob from recommendations")
    scan.add_argument("--keep-model", action="append", default=[], help="Protect a model name/path glob from recommendations")
    scan.add_argument("--json-only", action="store_true", help="Only write audit.json")
    scan.add_argument("--quiet", action="store_true", help="Suppress the terminal summary")
    return parser


def run_scan(args: argparse.Namespace) -> int:
    try:
        comfy_root = discover_comfy_root(args.comfy_root)
        output = args.output or Path.cwd() / f"comfy-audit-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        extra_model_paths = [_extra_model_path(value) for value in args.extra_model_path]
        result = analyze(
            comfy_root=comfy_root,
            workflow_roots=args.workflows,
            custom_nodes_override=args.custom_nodes_root,
            model_overrides=args.models_root,
            extra_model_overrides=extra_model_paths,
            include_yaml=not args.no_extra_model_paths,
            keep_node_packs=args.keep_node_pack,
            keep_models=args.keep_model,
        )
        written = write_reports(result, output.expanduser().resolve(), args.json_only)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not args.quiet:
        summary = result["summary"]
        print("ComfyUI Asset Audit complete")
        print(f"  Workflows scanned:          {summary['workflows_scanned']}")
        print(f"  Custom-node packs:          {summary['custom_node_packs']}")
        print(f"  Unreferenced pack candidates: {summary['unreferenced_custom_node_packs']}")
        print(f"  Models:                     {summary['models']}")
        print(f"  Model storage:              {summary['total_model_size']}")
        print(f"  Potential model savings:    {summary['potentially_reclaimable_model_size']}")
        print(f"  Unresolved references:      {summary['unresolved_node_references'] + summary['unresolved_model_references']}")
        print(f"  Report directory:           {output.expanduser().resolve()}")
        print("\nUnreferenced means only: not referenced by the supplied workflow collection.")
        print("No files were modified or deleted.")
        if written:
            print("\nWritten files:")
            for path in written:
                print(f"  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "scan":
        return run_scan(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

