from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryTests(unittest.TestCase):
    def test_release_version_is_semantic_and_documented(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertRegex(version, r"^\d+\.\d+\.\d+$")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"## {version}", changelog)
        self.assertIn(f"v{version}", readme)

    def test_skill_reference_links_exist(self):
        content = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        links = re.findall(r"\[[^\]]+\]\(([^)]+)\)", content)
        relative_links = [link.split("#", 1)[0] for link in links if "://" not in link]
        self.assertTrue(relative_links)
        missing = [link for link in relative_links if not (ROOT / link).is_file()]
        self.assertEqual(missing, [])

    def test_interface_metadata_is_discoverable(self):
        content = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("display_name:", content)
        self.assertIn("short_description:", content)
        self.assertIn("$industry-research-audit", content)
        match = re.search(r'short_description:\s*"([^"]+)"', content)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertGreaterEqual(len(match.group(1)), 25)
        self.assertLessEqual(len(match.group(1)), 64)

    def test_external_default_routes_to_public_full_evidence_mode(self):
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        interface = (ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        for relative_path in (
            "references/public-full-evidence-workflow.md",
            "references/public-industrial-park-priors.md",
            "references/industrial-park-lens.md",
        ):
            self.assertTrue((ROOT / relative_path).is_file())
            self.assertIn(relative_path, skill)
        self.assertIn("Default public full-evidence runtime", skill)
        self.assertIn("browse by default", skill)
        self.assertIn("full-evidence mode", interface)
        self.assertIn("browse authoritative primary sources", interface)
        self.assertIn("loaded-resource hashes", interface)
        self.assertIn("actual web trace", interface)

    def test_public_tree_contains_no_source_binaries(self):
        blocked = {
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".pdf",
            ".png", ".jpg", ".jpeg", ".gif", ".webp",
        }
        found = [str(path.relative_to(ROOT)) for path in ROOT.rglob("*") if path.is_file() and path.suffix.lower() in blocked]
        self.assertEqual(found, [])

    def test_synthetic_files_identify_their_status(self):
        for path in (ROOT / "examples" / "synthetic").glob("*.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertRegex(text, r"虚构|合成")
        expected = json.loads((ROOT / "examples" / "synthetic" / "expected-review.json").read_text(encoding="utf-8"))
        self.assertEqual(expected["fixture"], "fully_synthetic")

    def test_expected_review_uses_dual_axis_routing(self):
        expected = json.loads(
            (ROOT / "examples" / "synthetic" / "expected-review.json").read_text(encoding="utf-8")
        )
        issues = expected["expected_minimum_issues"]
        self.assertTrue(issues)
        for issue in issues:
            with self.subTest(category=issue["category"]):
                self.assertIn(issue["risk_level"], {"R1", "R2", "R3"})
                self.assertIn(issue["verification_level"], {"V1", "V2", "V3"})
                self.assertNotIn("severity", issue)

    def test_private_knowledge_store_is_not_embedded(self):
        for name in ("private-kb", "local-knowledge", "knowledge-private"):
            self.assertFalse((ROOT / name).exists())

    def test_public_code_has_no_private_corpus_identity(self):
        marker = "YI" + "HAN"
        found = []
        for path in ROOT.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in {".py", ".md", ".json", ".jsonl", ".yaml"}:
                continue
            if marker.casefold() in path.read_text(encoding="utf-8", errors="ignore").casefold():
                found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [])

    def test_readme_is_product_facing(self):
        content = (ROOT / "README.md").read_text(encoding="utf-8")
        for marker in ("面" + "试", "求" + "职", "简" + "历"):
            self.assertNotIn(marker, content)
        self.assertFalse((ROOT / "docs" / ("inter" + "view-guide.md")).exists())

    def test_prior_manifest_schema_matches_current_runtime(self):
        schema = json.loads(
            (ROOT / "references" / "prior-question-manifest.schema.json").read_text(encoding="utf-8")
        )
        properties = schema["properties"]
        self.assertEqual(properties["schema_version"]["const"], "0.2")
        self.assertEqual(
            properties["selection_policy_version"]["const"],
            "metadata-scope-char-budget-v0.2",
        )
        for field in ("character_budget", "budget_closure_code", "budget_issues"):
            self.assertIn(field, schema["required"])


if __name__ == "__main__":
    unittest.main()
