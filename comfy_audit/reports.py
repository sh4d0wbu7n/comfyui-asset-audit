from __future__ import annotations

import csv
import html
import json
from pathlib import Path
from typing import Any


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            cooked = dict(row)
            for key, value in cooked.items():
                if isinstance(value, list):
                    cooked[key] = " | ".join(str(item) for item in value)
            writer.writerow(cooked)


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]], table_id: str) -> str:
    headers = "".join(f"<th>{_e(label)}</th>" for _, label in columns)
    body: list[str] = []
    for row in rows:
        status = str(row.get("status", ""))
        cells = []
        for key, _ in columns:
            value = row.get(key, "")
            if isinstance(value, list):
                value = "\n".join(str(item) for item in value)
            if key == "status":
                cells.append(f'<td><span class="status status-{_e(status)}">{_e(value)}</span></td>')
            elif key in {"path", "referencing_workflows", "reason"}:
                cells.append(f'<td class="wrap">{_e(value)}</td>')
            else:
                cells.append(f"<td>{_e(value)}</td>")
        search = _e(" ".join(str(value) for value in row.values() if not isinstance(value, (dict, list))))
        body.append(f'<tr data-search="{search.casefold()}">{"".join(cells)}</tr>')
    if not body:
        body.append(f'<tr><td colspan="{len(columns)}">No entries</td></tr>')
    return f'<div class="table-wrap"><table id="{_e(table_id)}"><thead><tr>{headers}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def _html_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    cards = [
        ("Workflows", summary["workflows_scanned"]),
        ("Custom-node packs", summary["custom_node_packs"]),
        ("Unreferenced packs", summary["unreferenced_custom_node_packs"]),
        ("Models", summary["models"]),
        ("Model storage", summary["total_model_size"]),
        ("Potential model savings", summary["potentially_reclaimable_model_size"]),
    ]
    card_html = "".join(f'<div class="card"><span>{_e(label)}</span><strong>{_e(value)}</strong></div>' for label, value in cards)

    pack_columns = [
        ("name", "Pack"), ("status", "Status"), ("confidence", "Confidence"),
        ("size", "Size"), ("registered_node_count", "Nodes"), ("reference_count", "References"),
        ("path", "Path"), ("reason", "Reason"),
    ]
    model_columns = [
        ("name", "Model"), ("category", "Category"), ("status", "Status"),
        ("confidence", "Confidence"), ("size", "Size"), ("reference_count", "References"),
        ("path", "Path"), ("reason", "Reason"),
    ]
    issue_rows = result["issues"] + [
        {"level": "warning", "path": row["workflow"], "message": f"Unresolved node: {row['node_type']} — {row['reason']}"}
        for row in result["unresolved_nodes"]
    ] + [
        {"level": "warning", "path": row["workflow"], "message": f"Unresolved model reference: {row['value']} ({row['node_type']})"}
        for row in result["unresolved_model_references"]
    ]
    issue_columns = [("level", "Level"), ("path", "Path"), ("message", "Message")]

    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ComfyUI Asset Audit</title>
<style>
:root {{ color-scheme: dark; --bg:#111318; --panel:#1a1e26; --line:#303746; --text:#edf1f7; --muted:#aeb8c8; --accent:#68a7ff; }}
* {{ box-sizing:border-box }} body {{ margin:0; font:14px/1.45 system-ui,sans-serif; background:var(--bg); color:var(--text) }}
main {{ max-width:1600px; margin:auto; padding:28px }} h1 {{ margin:0 0 4px; font-size:28px }} h2 {{ margin:32px 0 12px }}
.muted {{ color:var(--muted) }} .notice {{ border-left:4px solid #e9b949; padding:12px 16px; background:#292414; margin:20px 0 }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:20px 0 }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:14px }} .card span {{ display:block;color:var(--muted) }} .card strong {{ display:block;font-size:22px;margin-top:5px }}
input {{ width:100%; max-width:520px; background:var(--panel); color:var(--text); border:1px solid var(--line); padding:10px 12px; border-radius:7px }}
.table-wrap {{ overflow:auto; border:1px solid var(--line); border-radius:9px }} table {{ border-collapse:collapse; width:100%; min-width:900px }}
th,td {{ text-align:left; padding:9px 11px; border-bottom:1px solid var(--line); vertical-align:top }} th {{ position:sticky;top:0;background:#222833 }} tr:hover td {{ background:#181d25 }}
.wrap {{ max-width:440px; white-space:pre-line; overflow-wrap:anywhere }} .status {{ padding:3px 7px;border-radius:12px;background:#38404f }}
.status-referenced {{ background:#173f2c;color:#8de2b5 }} .status-unreferenced {{ background:#512727;color:#ffb1b1 }} .status-unknown,.status-ambiguous {{ background:#51441e;color:#ffe08a }} .status-protected {{ background:#243b59;color:#acd0ff }}
footer {{ margin:35px 0;color:var(--muted) }}
</style></head><body><main>
<h1>ComfyUI Asset Audit</h1><div class="muted">Generated {_e(result['generated_at'])}</div>
<div class="notice"><strong>Scope:</strong> {_e(result['scope_statement'])}</div>
<div class="cards">{card_html}</div>
<label for="filter">Filter all tables</label><br><input id="filter" placeholder="Name, status, path…" autocomplete="off">
<h2>Custom-node packs</h2>{_table(result['custom_node_packs'], pack_columns, 'packs')}
<h2>Models</h2>{_table(result['models'], model_columns, 'models')}
<h2>Issues and unresolved references</h2>{_table(issue_rows, issue_columns, 'issues')}
<footer>This report is read-only. Review candidates and preserve backups before changing the ComfyUI installation.</footer>
</main><script>
const input=document.getElementById('filter'); input.addEventListener('input',()=>{{const q=input.value.trim().toLowerCase();document.querySelectorAll('tbody tr[data-search]').forEach(row=>row.hidden=q&&!row.dataset.search.includes(q));}});
</script></body></html>'''


def write_reports(result: dict[str, Any], output: Path, json_only: bool = False) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    json_path = output / "audit.json"
    json_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    written.append(json_path)
    if json_only:
        return written

    packs_csv = output / "custom_nodes.csv"
    _write_csv(
        packs_csv,
        result["custom_node_packs"],
        ["name", "status", "confidence", "size_bytes", "size", "registered_node_count", "reference_count", "version", "path", "reason", "referencing_workflows", "git_remote"],
    )
    written.append(packs_csv)

    models_csv = output / "models.csv"
    _write_csv(
        models_csv,
        result["models"],
        ["asset_id", "name", "category", "status", "confidence", "size_bytes", "size", "kind", "reference_count", "ambiguous_reference_count", "path", "root", "source", "reason", "referencing_workflows"],
    )
    written.append(models_csv)

    unresolved_csv = output / "unresolved_references.csv"
    unresolved_rows = [
        {"kind": "node", "workflow": row["workflow"], "node_type": row["node_type"], "value": "", "state": row["state"], "reason": row["reason"]}
        for row in result["unresolved_nodes"]
    ] + [
        {"kind": "model", "workflow": row["workflow"], "node_type": row["node_type"], "value": row["value"], "state": row["state"], "reason": "No installed model matched this filename"}
        for row in result["unresolved_model_references"]
    ]
    _write_csv(unresolved_csv, unresolved_rows, ["kind", "workflow", "node_type", "value", "state", "reason"])
    written.append(unresolved_csv)

    issues_csv = output / "issues.csv"
    _write_csv(issues_csv, result["issues"], ["level", "path", "message"])
    written.append(issues_csv)

    html_path = output / "report.html"
    html_path.write_text(_html_report(result), encoding="utf-8")
    written.append(html_path)
    return written

