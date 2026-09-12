from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_layered_corpus.py"
AUTHORIZATION_ID = "AUTH-SYN-LOCAL-001"
CORPUS_ID = "CORPUS-SYN-001"


def staging_record(body: str = "虚构标题\nOriginal\n虚构作者\nSep 1, 2026, 4:00 PM\n虚构正文。\nReads1") -> dict:
    return {
        "schema_version": "0.1",
        "record_type": "staging_article_capture",
        "snapshot_id": "SNAP-TEST",
        "article_id": "art_test_001",
        "title": "虚构标题",
        "publisher_name": "虚构公众号",
        "published_date_local": "2026-09-01",
        "published_at_raw": "Sep 1, 2026, 4:00 PM",
        "source_locator": {"original_url": None, "native_article_id": None},
        "document_text": body,
        "document_text_sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "capture_status": "captured",
        "captured_at": "2026-09-06T12:00:00.000Z",
        "rights_ref": AUTHORIZATION_ID,
        "instructions_treated_as_data": True,
    }


class LayeredCorpusBuilderTests(unittest.TestCase):
    def run_tool(self, root: Path, record: dict, *extra: str) -> subprocess.CompletedProcess[str]:
        staging = root / "staging.jsonl"
        staging.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--staging",
                str(staging),
                "--corpus-root",
                str(root / "kb"),
                "--snapshot-id",
                "SNAP-TEST",
                "--corpus-id",
                CORPUS_ID,
                "--authorization-id",
                AUTHORIZATION_ID,
                *extra,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_dry_run_makes_no_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_tool(root, staging_record(), "--dry-run")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout)["writes"], 0)
            self.assertFalse((root / "kb").exists())

    def test_builds_l0_and_l1_without_printing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_tool(root, staging_record())
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("SRCV-", result.stdout)
            self.assertNotIn("sha256", result.stdout.casefold())
            report = json.loads(result.stdout)
            self.assertEqual(report["source_record_count"], 1)
            self.assertEqual(report["chunk_record_count"], 1)
            self.assertNotIn("虚构", result.stdout)
            l0 = json.loads((root / "kb" / "l0_raw" / "records.jsonl").read_text(encoding="utf-8"))
            self.assertIsNone(l0["canonical_url"])
            text_files = list((root / "kb" / "l1_index" / "text").glob("*.txt"))
            self.assertEqual(text_files[0].read_text(encoding="utf-8"), "虚构正文。\n")

    def test_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            record = staging_record()
            record["document_text_sha256"] = "0" * 64
            result = self.run_tool(root, record, "--dry-run")
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stdout)["error_code"], "ValueError")

    def test_duplicate_detection_ignores_capture_chrome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = staging_record()
            second = staging_record("虚构标题\nOriginal\n虚构作者\nSep 2, 2026, 4:00 PM\n虚构正文。\nReads99")
            second["article_id"] = "art_test_002"
            second["published_date_local"] = "2026-09-02"
            second["published_at_raw"] = "Sep 2, 2026, 4:00 PM"
            staging = root / "staging.jsonl"
            staging.write_text(
                json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "--staging",
                    str(staging),
                    "--corpus-root",
                    str(root / "kb"),
                    "--snapshot-id",
                    "SNAP-TEST",
                    "--corpus-id",
                    CORPUS_ID,
                    "--authorization-id",
                    AUTHORIZATION_ID,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["duplicate_content_count"], 1)

    def test_merge_preserves_existing_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_result = self.run_tool(root, staging_record())
            self.assertEqual(first_result.returncode, 0, first_result.stdout)

            second = staging_record("第二个虚构标题\n第二篇虚构正文。")
            second["article_id"] = "art_test_002"
            second["title"] = "第二个虚构标题"
            second["published_date_local"] = None
            second["published_at_raw"] = None
            second["document_text_sha256"] = hashlib.sha256(
                second["document_text"].encode("utf-8")
            ).hexdigest()
            result = self.run_tool(root, second, "--merge-existing")
            self.assertEqual(result.returncode, 0, result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["source_record_count"], 2)
            self.assertEqual(report["new_source_record_count"], 1)
            self.assertEqual(report["existing_source_record_count"], 1)
            records = [
                json.loads(line)
                for line in (root / "kb" / "l0_raw" / "records.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            self.assertEqual({record["corpus_id"] for record in records}, {CORPUS_ID})

    def test_merge_links_new_version_of_same_article(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_result = self.run_tool(root, staging_record())
            self.assertEqual(first_result.returncode, 0, first_result.stdout)
            first = json.loads(
                (root / "kb" / "l0_raw" / "records.jsonl").read_text(encoding="utf-8")
            )

            revised = staging_record("虚构标题\nOriginal\n虚构作者\nSep 1, 2026, 4:00 PM\n修订后的虚构正文。\nReads2")
            revised["document_text_sha256"] = hashlib.sha256(
                revised["document_text"].encode("utf-8")
            ).hexdigest()
            result = self.run_tool(root, revised, "--merge-existing")
            self.assertEqual(result.returncode, 0, result.stdout)
            records = [
                json.loads(line)
                for line in (root / "kb" / "l0_raw" / "records.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line
            ]
            self.assertEqual(len(records), 2)
            newest = next(record for record in records if record != first)
            self.assertEqual(
                newest["supersedes_source_version_id"], first["source_version_id"]
            )


if __name__ == "__main__":
    unittest.main()
