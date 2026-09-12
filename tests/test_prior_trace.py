from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import knowledge_base  # noqa: E402
import prior_trace  # noqa: E402


CARD_FILE = ROOT / "examples" / "synthetic" / "knowledge-cards.jsonl"
ENVELOPE_FILE = ROOT / "examples" / "synthetic" / "task-envelope.json"
AUTHORIZATION_FILE = ROOT / "examples" / "synthetic" / "authorizations.jsonl"
ISSUE_FILE = ROOT / "examples" / "synthetic" / "prior-issue-register.json"


class PriorTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = json.loads(ENVELOPE_FILE.read_text(encoding="utf-8"))
        cls.cards = knowledge_base.load_cards(CARD_FILE)
        cls.authorizations = knowledge_base.load_authorizations(AUTHORIZATION_FILE)

    def build_manifest(self):
        return knowledge_base.build_prior_manifest(
            self.cards,
            copy.deepcopy(self.envelope),
            copy.deepcopy(self.authorizations),
            knowledge_snapshot_hash=knowledge_base.sha256_file(CARD_FILE),
            authorization_snapshot_hash=knowledge_base.sha256_file(AUTHORIZATION_FILE),
            created_at=datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
        )

    def test_valid_trace_binds_pre_draft_manifest_to_discovery_origins(self):
        manifest = self.build_manifest()
        self.assertEqual(prior_trace.validate_prior_manifest(manifest), [])
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "private-draft.bin"
            draft.write_bytes(b"SYNTHETIC-DRAFT-CONTENT")
            receipt = prior_trace.build_draft_receipt(
                manifest,
                draft,
                ingested_at=datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc),
            )
        register = json.loads(ISSUE_FILE.read_text(encoding="utf-8"))
        self.assertEqual(prior_trace.validate_receipt(receipt, manifest), [])
        codes, issue_count, prior_count = prior_trace.validate_issue_register(register, manifest, receipt)
        self.assertEqual(codes, [])
        self.assertEqual(issue_count, 2)
        self.assertEqual(prior_count, 1)

    def test_tampered_manifest_and_unknown_prior_question_fail(self):
        manifest = self.build_manifest()
        tampered = copy.deepcopy(manifest)
        tampered["questions"][0]["atomic_question"] = "TAMPERED"
        self.assertIn("MANIFEST_HASH_MISMATCH", prior_trace.validate_prior_manifest(tampered))

        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft.bin"
            draft.write_bytes(b"synthetic")
            receipt = prior_trace.build_draft_receipt(
                manifest,
                draft,
                ingested_at=datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc),
            )
        register = json.loads(ISSUE_FILE.read_text(encoding="utf-8"))
        register["issues"][0]["discovery_origin"]["prior_question_id"] = "PQ-KC-SYN-UNKNOWN-001"
        codes, _, _ = prior_trace.validate_issue_register(register, manifest, receipt)
        self.assertIn("PRIOR_ORIGIN_UNKNOWN_QUESTION", codes)

    def test_non_prior_origin_cannot_claim_prior_question(self):
        manifest = self.build_manifest()
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "draft.bin"
            draft.write_bytes(b"synthetic")
            receipt = prior_trace.build_draft_receipt(
                manifest,
                draft,
                ingested_at=datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc),
            )
        register = json.loads(ISSUE_FILE.read_text(encoding="utf-8"))
        register["issues"][1]["discovery_origin"]["prior_question_id"] = "PQ-KC-SYN-HEUR-001"
        codes, _, _ = prior_trace.validate_issue_register(register, manifest, receipt)
        self.assertIn("NON_PRIOR_ORIGIN_CLAIMS_QUESTION", codes)

    def test_bind_cli_stdout_omits_draft_content_name_and_path(self):
        manifest = self.build_manifest()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            manifest_path = base / "manifest.json"
            draft_path = base / "PRIVATE-CANARY-NAME.bin"
            receipt_path = base / "receipt.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            draft_path.write_bytes(b"PRIVATE-CANARY-CONTENT")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = prior_trace.main(
                    [
                        "bind-draft",
                        str(manifest_path),
                        str(draft_path),
                        "--output",
                        str(receipt_path),
                        "--ingested-at",
                        "2026-09-06T01:02:00Z",
                    ]
                )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertFalse(receipt["draft_content_logged"])
        self.assertFalse(receipt["draft_path_logged"])
        stdout = stream.getvalue()
        self.assertNotIn("PRIVATE-CANARY", stdout)
        self.assertNotIn(str(draft_path), stdout)


if __name__ == "__main__":
    unittest.main()
