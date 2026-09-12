from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_article_corpus as corpus  # noqa: E402


class ArticleCorpusValidatorTests(unittest.TestCase):
    def test_synthetic_directory_is_valid(self):
        report = corpus.validate_corpus(
            ROOT / "examples" / "synthetic" / "article-corpus",
            as_of=date(2026, 9, 6),
            min_date=date(2000, 1, 1),
        )
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["summary"]["raw_count"], 2)
        self.assertEqual(report["summary"]["derived_count"], 1)
        self.assertEqual(report["issues"], [])

    def test_detects_duplicates_dates_ids_accounts_and_layer_leakage(self):
        first_id = corpus.stable_article_id("wechat", "acct-1", "native-1")
        records = [
            {
                "schema_version": "1.0",
                "layer": "raw",
                "record_id": "raw-one",
                "article_id": first_id,
                "source_account": {"platform": "wechat", "account_id": "acct-1", "name": "合成账号甲"},
                "source_article_id": "native-1",
                "title": "重复标题",
                "body": "重复正文",
                "published_at": "2026-01-02T08:00:00+08:00",
                "original_url": "https://example.com/one",
                "captured_at": "2026-01-03T08:00:00+08:00",
            },
            {
                "schema_version": "1.0",
                "layer": "raw",
                "record_id": "raw-two",
                "article_id": "art_000000000000000000000000",
                "source_account": {"platform": "wechat", "account_id": "acct-1", "name": "合成账号乙"},
                "source_article_id": "native-2",
                "title": " 重复标题 ",
                "body": "重复正文\r\n",
                "published_at": "2099-01-01",
                "original_url": "https://example.com/two",
                "captured_at": "2026-01-03T08:00:00+08:00",
                "content": {"summary": "泄漏到原始层"},
            },
            {
                "schema_version": "1.0",
                "layer": "derived",
                "record_id": "derived-one",
                "article_id": first_id,
                "source_record_id": "raw-one",
                "body": "派生层不应复制原始正文字段",
                "content": {"clean_text": "清洗文本"},
                "derivation": {
                    "type": "clean_text",
                    "method": "synthetic-cleaner",
                    "method_version": "1.0",
                    "created_at": "2026-01-04T08:00:00+08:00",
                    "input_body_sha256": "0" * 64,
                },
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corpus.jsonl"
            path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records), encoding="utf-8")
            report = corpus.validate_corpus(path, as_of=date(2026, 9, 6), min_date=date(2000, 1, 1))

        codes = {issue["code"] for issue in report["issues"]}
        self.assertEqual(report["status"], "invalid")
        self.assertIn("DUPLICATE_TITLE", codes)
        self.assertIn("DUPLICATE_BODY_SHA256", codes)
        self.assertIn("PUBLISHED_AT_IN_FUTURE", codes)
        self.assertIn("ARTICLE_ID_NOT_STABLE", codes)
        self.assertIn("SOURCE_ACCOUNT_NAME_CONFLICT", codes)
        self.assertIn("LAYER_BOUNDARY_RAW_HAS_CONTENT", codes)
        self.assertIn("LAYER_BOUNDARY_DERIVED_HAS_BODY", codes)
        self.assertIn("DERIVED_INPUT_HASH_MISMATCH", codes)
        self.assertNotIn("重复正文", json.dumps(report, ensure_ascii=False))
        account_conflicts = [issue for issue in report["issues"] if issue["code"] == "SOURCE_ACCOUNT_NAME_CONFLICT"]
        self.assertTrue(account_conflicts)
        self.assertTrue(all(issue["severity"] == "warning" for issue in account_conflicts))

    def test_empty_body_is_error_and_cli_report_is_machine_readable(self):
        record = {
            "schema_version": "1.0",
            "layer": "raw",
            "record_id": "raw-empty",
            "article_id": corpus.stable_article_id("web", "acct-empty", "native-empty"),
            "source_account": {"platform": "web", "account_id": "acct-empty", "name": "合成账号"},
            "source_article_id": "native-empty",
            "title": "合成空正文测试",
            "body": "   ",
            "published_at": "2026-01-01",
            "original_url": "https://example.com/empty",
            "captured_at": "2026-01-02",
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "corpus.jsonl"
            output = Path(directory) / "report.json"
            source.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            exit_code = corpus.main([str(source), "--as-of", "2026-09-06", "--output", str(output)])
            report = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "invalid")
        self.assertIn("BODY_EMPTY", {issue["code"] for issue in report["issues"]})

    def test_stable_id_preserves_case_sensitive_source_identifiers(self):
        self.assertEqual(
            corpus.stable_article_id("WeChat", "Account-A", "Native-X"),
            corpus.stable_article_id("wechat", "Account-A", "Native-X"),
        )
        self.assertNotEqual(
            corpus.stable_article_id("wechat", "Account-A", "Native-X"),
            corpus.stable_article_id("wechat", "account-a", "native-x"),
        )

    def test_dry_run_never_opens_jsonl_content(self):
        source = ROOT / "examples" / "synthetic" / "article-corpus"
        with patch.object(Path, "read_text", side_effect=AssertionError("content read")):
            report = corpus.structure_dry_run(source)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["summary"]["raw_file_count"], 1)
        self.assertEqual(report["summary"]["derived_file_count"], 1)
        self.assertEqual(report["checks"]["detected_layout"], "raw_derived")
        self.assertEqual(report["checks"]["content_files_opened"], 0)
        self.assertFalse(report["checks"]["content_fields_read"])

    def test_dry_run_accepts_empty_layered_private_skeleton_without_opening_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in corpus.LAYERED_CORPUS_DIRS:
                (root / name).mkdir()
            for name in corpus.LAYERED_CONTROL_DIRS:
                (root / name).mkdir()
            with patch.object(Path, "read_text", side_effect=AssertionError("content read")):
                report = corpus.structure_dry_run(root)
        self.assertEqual(report["status"], "valid_with_warnings")
        self.assertEqual(report["checks"]["detected_layout"], "layered_l0_l6")
        self.assertEqual(report["checks"]["content_files_opened"], 0)
        self.assertEqual(report["issues"], [{"code": "EMPTY_CORPUS_SKELETON", "severity": "warning", "count": 1}])

    def test_dry_run_rejects_output_file(self):
        source = ROOT / "examples" / "synthetic" / "article-corpus"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "report.json"
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                exit_code = corpus.main([str(source), "--dry-run", "--output", str(output)])
            self.assertFalse(output.exists())
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "error")


if __name__ == "__main__":
    unittest.main()
