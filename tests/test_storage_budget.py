from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "audit_storage_budget.py"
SPEC = importlib.util.spec_from_file_location("audit_storage_budget", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def write_sized(path: Path, size: int, byte: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(byte * size)


class StorageBudgetTests(unittest.TestCase):
    def run_cli(self, root: Path, *arguments: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), str(root), *arguments],
            capture_output=True,
            text=True,
        )
        return result, json.loads(result.stdout)

    def test_public_skill_summary_uses_fixed_aggregate_groups(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "synthetic-skill"
            write_sized(root / "SKILL.md", 3)
            write_sized(root / "scripts" / "tool.py", 5)
            write_sized(root / "private-title-token" / "private-path-token.txt", 7, b"q")

            result, report = self.run_cli(root, "--profile", "public-skill")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["observed"]["file_count"], 3)
            self.assertEqual(report["observed"]["total_bytes"], 15)
            self.assertEqual(report["observed"]["max_file_bytes"], 7)
            groups = {item["group"]: item for item in report["observed"]["groups"]}
            self.assertEqual(groups["root"]["total_bytes"], 3)
            self.assertEqual(groups["scripts"]["total_bytes"], 5)
            self.assertEqual(groups["other"]["total_bytes"], 7)
            for forbidden in ("private-title-token", "private-path-token", str(root)):
                self.assertNotIn(forbidden, result.stdout)

    def test_layered_corpus_projects_by_article_count_and_warns_at_soft_limit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "synthetic-corpus"
            write_sized(root / "l0_raw" / "private-title.txt", 10)
            write_sized(root / "l1_index" / "private-body.txt", 20)
            write_sized(root / "l2_maps" / "private-map.jsonl", 30)
            write_sized(root / "l3_patterns" / "private-pattern.jsonl", 40)
            write_sized(root / "l4_contradictions" / "private-contradiction.jsonl", 50)
            write_sized(root / "l5_rubrics" / "private-rubric.jsonl", 60)
            write_sized(root / "l6_policy" / "private-policy.jsonl", 70)
            write_sized(root / "corpus-manifest.json", 5)

            result, report = self.run_cli(
                root,
                "--profile",
                "layered-corpus",
                "--sample-articles",
                "2",
                "--target-articles",
                "10",
                "--soft-budget-bytes",
                "1400",
                "--hard-budget-bytes",
                "1500",
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "warning")
            self.assertEqual(report["observed"]["total_bytes"], 285)
            self.assertEqual(report["projection"]["projected_total_bytes"], 1425)
            self.assertEqual(report["budget"]["basis"], "projected_total_bytes")
            self.assertEqual(report["issues"], [{"code": "STORAGE_SOFT_BUDGET_EXCEEDED", "count": 1}])
            groups = {item["group"]: item for item in report["observed"]["groups"]}
            self.assertEqual(groups["L0"]["total_bytes"], 10)
            self.assertEqual(groups["L1"]["total_bytes"], 20)
            self.assertEqual(groups["L2"]["total_bytes"], 30)
            self.assertEqual(groups["L3"]["total_bytes"], 40)
            self.assertEqual(groups["L4"]["total_bytes"], 50)
            self.assertEqual(groups["L5"]["total_bytes"], 60)
            self.assertEqual(groups["L6"]["total_bytes"], 70)
            self.assertEqual(groups["support"]["total_bytes"], 5)
            for forbidden in (
                "private-title",
                "private-body",
                "private-map",
                "private-pattern",
                "private-contradiction",
                "private-rubric",
                "private-policy",
                str(root),
            ):
                self.assertNotIn(forbidden, result.stdout)

    def test_hard_limit_blocks_with_stable_issue_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "synthetic-corpus"
            write_sized(root / "l6_prior_packs" / "private-pack.jsonl", 101)
            result, report = self.run_cli(
                root,
                "--profile",
                "layered-corpus",
                "--soft-budget-bytes",
                "50",
                "--hard-budget-bytes",
                "100",
            )
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["issues"], [{"code": "STORAGE_HARD_BUDGET_EXCEEDED", "count": 1}])
            self.assertNotIn("private-pack", result.stdout)

    def test_incomplete_projection_and_invalid_budget_are_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "synthetic-corpus"
            root.mkdir()
            report = MODULE.audit(
                root,
                profile="layered-corpus",
                sample_articles=2,
                soft_budget_bytes=200,
                hard_budget_bytes=100,
            )
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(
                {item["code"] for item in report["issues"]},
                {"BUDGET_CONFIGURATION_INVALID", "PROJECTION_ARGUMENTS_INCOMPLETE"},
            )

    def test_scanner_does_not_open_or_read_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "synthetic-corpus"
            write_sized(root / "l3_cards" / "content-must-not-be-read.jsonl", 17)
            with (
                mock.patch("builtins.open", side_effect=AssertionError("content opened")),
                mock.patch.object(Path, "open", side_effect=AssertionError("content opened")),
                mock.patch.object(Path, "read_bytes", side_effect=AssertionError("content read")),
                mock.patch.object(Path, "read_text", side_effect=AssertionError("content read")),
            ):
                report = MODULE.audit(root, profile="layered-corpus")
            self.assertEqual(report["status"], "within_budget")
            self.assertEqual(report["observed"]["file_count"], 1)
            self.assertEqual(report["observed"]["total_bytes"], 17)

    def test_missing_root_reports_only_aggregate_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "private-missing-root"
            result, report = self.run_cli(missing, "--profile", "layered-corpus")
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["issues"], [{"code": "ROOT_NOT_DIRECTORY", "count": 1}])
            self.assertNotIn("private-missing-root", result.stdout)
            self.assertNotIn(str(missing), result.stdout)


if __name__ == "__main__":
    unittest.main()
