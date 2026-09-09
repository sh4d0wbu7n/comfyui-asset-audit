from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from comfy_audit.analyzer import analyze
from comfy_audit.cli import main
from comfy_audit.discovery import discover_comfy_root, parse_extra_model_paths
from comfy_audit.reports import write_reports


class AuditIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.portable = self.root / "portable"
        self.comfy = self.portable / "ComfyUI"
        (self.comfy / "models" / "checkpoints").mkdir(parents=True)
        (self.comfy / "models" / "loras").mkdir(parents=True)
        (self.comfy / "custom_nodes").mkdir()
        (self.comfy / "comfy_extras").mkdir()
        (self.comfy / "folder_paths.py").write_text("# fixture\n", encoding="utf-8")
        (self.comfy / "nodes.py").write_text(
            "NODE_CLASS_MAPPINGS = {'CheckpointLoaderSimple': object, 'KSampler': object}\n",
            encoding="utf-8",
        )
        self.workflows = self.root / "workflows"
        self.workflows.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _add_pack(self, name: str, mapping_source: str) -> Path:
        pack = self.comfy / "custom_nodes" / name
        pack.mkdir()
        (pack / "__init__.py").write_text(mapping_source, encoding="utf-8")
        return pack

    def test_complete_read_only_audit_and_reports(self) -> None:
        self._add_pack("used-pack", "NODE_CLASS_MAPPINGS = {'UsefulNode': object}\n")
        self._add_pack("unused-pack", "NODE_CLASS_MAPPINGS = {'OldNode': object}\n")
        self._add_pack(
            "dynamic-pack",
            "NODE_CLASS_MAPPINGS = {}\nfor name in []:\n    NODE_CLASS_MAPPINGS[name] = object\n",
        )
        checkpoint = self.comfy / "models" / "checkpoints" / "used.safetensors"
        checkpoint.write_bytes(b"used")
        unused_lora = self.comfy / "models" / "loras" / "unused.safetensors"
        unused_lora.write_bytes(b"unused")

        workflow = {
            "version": 1,
            "state": {"lastGroupid": 0, "lastNodeId": 2, "lastLinkId": 0, "lastRerouteId": 0},
            "nodes": [
                {
                    "id": 1,
                    "type": "UsefulNode",
                    "pos": [0, 0],
                    "size": [200, 100],
                    "flags": {},
                    "order": 0,
                    "mode": 0,
                    "properties": {"cnr_id": "used-pack"},
                    "widgets_values": ["used.safetensors"],
                },
                {
                    "id": 2,
                    "type": "KSampler",
                    "pos": [0, 0],
                    "size": [200, 100],
                    "flags": {},
                    "order": 1,
                    "mode": 0,
                    "properties": {},
                },
            ],
        }
        (self.workflows / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
        (self.workflows / "broken.json").write_text("{broken", encoding="utf-8")

        result = analyze(self.comfy, [self.workflows])
        packs = {item["name"]: item for item in result["custom_node_packs"]}
        models = {item["name"]: item for item in result["models"]}

        self.assertEqual(packs["used-pack"]["status"], "referenced")
        self.assertEqual(packs["unused-pack"]["status"], "unreferenced")
        self.assertEqual(packs["dynamic-pack"]["status"], "unknown")
        self.assertEqual(models["used.safetensors"]["status"], "referenced")
        self.assertEqual(models["unused.safetensors"]["status"], "unreferenced")
        self.assertTrue(any("Invalid JSON" in issue["message"] for issue in result["issues"]))

        output = self.root / "report"
        written = write_reports(result, output)
        self.assertEqual(len(written), 6)
        self.assertTrue((output / "report.html").is_file())
        self.assertTrue((output / "audit.json").is_file())

    def test_api_workflow_and_bypassed_ui_node(self) -> None:
        self._add_pack("api-pack", "NODE_CLASS_MAPPINGS.update({'ApiLoader': object})\n")
        model = self.comfy / "models" / "checkpoints" / "api-model.gguf"
        model.write_bytes(b"model")
        api = {"1": {"class_type": "ApiLoader", "inputs": {"model": "api-model.gguf"}}}
        (self.workflows / "api.json").write_text(json.dumps(api), encoding="utf-8")

        result = analyze(self.comfy, [self.workflows])
        pack = next(item for item in result["custom_node_packs"] if item["name"] == "api-pack")
        asset = next(item for item in result["models"] if item["name"] == "api-model.gguf")
        self.assertEqual(pack["status"], "referenced")
        self.assertEqual(asset["status"], "referenced")

    def test_portable_root_and_extra_model_paths(self) -> None:
        external = self.root / "external" / "vae"
        external.mkdir(parents=True)
        yaml_path = self.comfy / "extra_model_paths.yaml"
        yaml_path.write_text(
            f"shared:\n  base_path: {self.root / 'external'}\n  vae: |\n    vae\n  is_default: true\n",
            encoding="utf-8",
        )
        self.assertEqual(discover_comfy_root(self.portable), self.comfy.resolve())
        parsed = parse_extra_model_paths(yaml_path)
        self.assertEqual(parsed[0][0], "shared")
        self.assertEqual(parsed[0][1], "vae")
        self.assertTrue(parsed[0][2].endswith("external/vae"))

    def test_duplicate_model_reference_is_ambiguous(self) -> None:
        duplicate_a = self.comfy / "models" / "checkpoints" / "duplicate.safetensors"
        duplicate_b = self.comfy / "models" / "loras" / "duplicate.safetensors"
        duplicate_a.write_bytes(b"a")
        duplicate_b.write_bytes(b"b")
        api = {"1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": "duplicate.safetensors"}}}
        (self.workflows / "duplicate.json").write_text(json.dumps(api), encoding="utf-8")

        result = analyze(self.comfy, [self.workflows])
        duplicates = [item for item in result["models"] if item["name"] == "duplicate.safetensors"]
        self.assertEqual(len(duplicates), 2)
        self.assertTrue(all(item["status"] == "ambiguous" for item in duplicates))

    def test_cli_end_to_end(self) -> None:
        self._add_pack("cli-pack", "NODE_CLASS_MAPPINGS = {'CliNode': object}\n")
        workflow = {"1": {"class_type": "CliNode", "inputs": {}}}
        (self.workflows / "cli.json").write_text(json.dumps(workflow), encoding="utf-8")
        output = self.root / "cli-report"

        exit_code = main(
            [
                "scan",
                "--comfy-root", str(self.portable),
                "--workflows", str(self.workflows),
                "--output", str(output),
                "--quiet",
            ]
        )
        self.assertEqual(exit_code, 0)
        self.assertTrue((output / "report.html").is_file())
        result = json.loads((output / "audit.json").read_text(encoding="utf-8"))
        self.assertEqual(result["summary"]["workflows_scanned"], 1)


if __name__ == "__main__":
    unittest.main()
