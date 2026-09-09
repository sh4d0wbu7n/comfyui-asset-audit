# ComfyUI Asset Auditor

A standalone, read-only Python CLI that compares a collection of ComfyUI workflows with the custom-node packs and models installed in a ComfyUI installation.

It identifies:

- Custom-node packs referenced by active workflow nodes
- Packs referenced only by bypassed or muted nodes
- Packs with no reference in the supplied workflows
- Models referenced by workflow widget values, API inputs, or workflow model metadata
- Unreferenced model candidates and potentially reclaimable storage
- Ambiguous duplicate models
- Missing models, missing nodes, malformed workflows, and uncertain mappings

The auditor never imports custom-node code, executes workflows, moves files, or deletes anything.

## Important limitation

“Unreferenced” means **not referenced by the workflow folders you supplied**. It does not prove that an asset has never been used or that deleting it is safe. Workflows stored elsewhere, generated dynamically, or embedded only in output media are outside the scan.

## Requirements

- Python 3.10 or newer
- No third-party Python packages
- Windows, Linux, or macOS

## Run without installing

Open a terminal in this project directory:

```powershell
python -m comfy_audit scan `
  --comfy-root "C:\ComfyUI_windows_portable" `
  --workflows "D:\ComfyUI_Workflows" `
  --output "D:\ComfyUI_Audit"
```

The `--comfy-root` value may point either to the portable wrapper directory or directly to the directory containing `folder_paths.py`.

## Install the command

```powershell
python -m pip install .
```

Then run:

```powershell
comfy-audit scan `
  --comfy-root "C:\ComfyUI_windows_portable" `
  --workflows "D:\ComfyUI_Workflows" `
  --output "D:\ComfyUI_Audit"
```

## Scan multiple workflow locations

Repeat `--workflows`:

```powershell
comfy-audit scan `
  --comfy-root "C:\ComfyUI_windows_portable" `
  --workflows "D:\Current_Workflows" `
  --workflows "Z:\Production\Archived_Workflows"
```

Both individual JSON files and folders are accepted. Folders are scanned recursively.

## Model path discovery

The auditor automatically scans:

1. Subdirectories of `ComfyUI\models`
2. Existing paths configured in `ComfyUI\extra_model_paths.yaml`

Disable the YAML discovery with:

```powershell
comfy-audit scan ... --no-extra-model-paths
```

Add another model folder manually:

```powershell
comfy-audit scan ... --extra-model-path "controlnet=Z:\SharedModels\ControlNet"
```

Override the standard models directory entirely:

```powershell
comfy-audit scan ... --models-root "D:\AI\models"
```

## Protect assets

Keep patterns prevent matching assets from being listed as unreferenced candidates:

```powershell
comfy-audit scan ... `
  --keep-node-pack "ComfyUI-Manager" `
  --keep-node-pack "*Asset-Auditor*" `
  --keep-model "*production_approved*" `
  --keep-model "*\base_models\*"
```

Patterns are matched case-insensitively against both names and complete paths.

## Reports

The output directory contains:

| File | Purpose |
| --- | --- |
| `report.html` | Searchable overview for human review |
| `audit.json` | Complete machine-readable result and evidence |
| `custom_nodes.csv` | Custom-node pack summary |
| `models.csv` | Model summary |
| `unresolved_references.csv` | Missing or ambiguous workflow dependencies |
| `issues.csv` | Parsing and filesystem problems |

Use `--json-only` if only the machine-readable report is required.

## Classification

### Custom-node packs

- `referenced`: at least one active node maps to the pack
- `bypassed_only`: all references are bypassed or muted
- `unreferenced`: registered node types were identified, but none occur in the supplied workflows
- `unknown`: dynamic registration, incomplete parsing, or no identifiable node mappings
- `disabled`: the directory is already marked as disabled
- `protected`: matched a command-line keep pattern

### Models

- `referenced`: one installed asset uniquely matches a workflow value
- `bypassed_only`: references occur only in bypassed or muted nodes
- `ambiguous`: a workflow reference matches more than one physical asset
- `unreferenced`: no exact reference was found in the supplied workflows
- `protected`: matched a command-line keep pattern

Model matching is intentionally conservative. Partial filename guesses do not count as safe evidence.

## Exit codes

- `0`: scan and report generation completed
- `2`: invalid path, discovery failure, or report-writing failure

Invalid individual workflows are recorded in the report and do not abort the entire scan.

## Run tests

```powershell
python -m unittest discover -s tests -v
```

