from __future__ import annotations

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date, datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import knowledge_base  # noqa: E402
import promote_l3_priors  # noqa: E402


L3_FILE = ROOT / "examples" / "synthetic" / "l3-prior-candidates.jsonl"
AUTH_FILE = ROOT / "examples" / "synthetic" / "l3-prior-authorizations.jsonl"
ENVELOPE_FILE = ROOT / "examples" / "synthetic" / "task-envelope.json"


class L3PriorPromotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.records = promote_l3_priors.load_l3_records(L3_FILE)
        cls.authorizations = knowledge_base.load_authorizations(AUTH_FILE)

    def promote(self):
        return promote_l3_priors.promote_records(
            self.records,
            self.authorizations,
            at_date=date(2026, 9, 6),
        )

    def test_only_human_accepted_active_authorized_abstractions_promote(self):
        promoted, issues = self.promote()

        self.assertEqual(len(self.records), 8)
        self.assertEqual(len(promoted), 2)
        self.assertEqual(
            dict(issues),
            {
                "HUMAN_REVIEW_NOT_ACCEPTED": 1,
                "CARD_TYPE_NOT_PROMOTABLE": 2,
                "LIFECYCLE_NOT_ACTIVE": 1,
                "ABSTRACT_GUIDANCE_DENIED": 1,
                "AUTHORIZATION_NOT_FOUND": 1,
            },
        )
        self.assertEqual({card["card_type"] for card in promoted}, {"expert_heuristic", "control"})
        self.assertEqual(knowledge_base.validate_cards(list(enumerate(promoted, start=1))), [])
        for card in promoted:
            self.assertEqual(card["epistemic"]["status"], "reported")
            self.assertNotIn(card["epistemic"]["fact_inference_boundary"], {"fact", "calculation"})
            self.assertEqual(card["rights_and_access"]["sensitivity"], "private")
            self.assertFalse(card["rights_and_access"]["export_allowed"])
            search_record = knowledge_base.safe_search_record(card, 1, date(2026, 9, 6))
            self.assertEqual(search_record["use_mode"], "review_prompt")
            self.assertFalse(search_record["eligible_as_evidence"])

    def test_conversion_preserves_source_ids_and_full_locator_in_private_output(self):
        promoted, _ = self.promote()
        expert = next(card for card in promoted if card["card_type"] == "expert_heuristic")
        source_ids = set(expert["provenance"]["source_ids"])
        self.assertTrue(
            {
                "L3-SYN-HEUR-001",
                "CORPUS-SYNTHETIC-001",
                "L2-SYN-001",
                "ARTICLE-SYN-001",
                "SOURCE-VERSION-SYN-001",
                "PRIVATE-SOURCE-CANARY",
                "INDEPENDENT-SYN-001",
            }.issubset(source_ids)
        )
        locator = json.loads(expert["provenance"]["locators"][0].removeprefix("l3-locator:"))
        self.assertEqual(
            locator,
            {
                "source_version_id": "SOURCE-VERSION-SYN-001",
                "chunk_id": "PRIVATE-SOURCE-CANARY",
                "section_path": ["synthetic-section"],
                "paragraph_index": 2,
                "char_start": 10,
                "char_end": 40,
            },
        )
        self.assertEqual(expert["provenance"]["independent_source_ids"], ["INDEPENDENT-SYN-001"])
        self.assertIsNotNone(expert["provenance"]["content_hash"])

    def test_promoted_interface_is_readable_by_prior_without_becoming_evidence(self):
        promoted, _ = self.promote()
        envelope = json.loads(ENVELOPE_FILE.read_text(encoding="utf-8"))
        envelope["authorization_context"]["authorization_id"] = "AUTH-SYN-PROMOTE"
        manifest = knowledge_base.build_prior_manifest(
            list(enumerate(promoted, start=1)),
            envelope,
            self.authorizations,
            knowledge_snapshot_hash="a" * 64,
            authorization_snapshot_hash="b" * 64,
            created_at=datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
        )

        self.assertEqual(len(manifest["questions"]), 2)
        for question in manifest["questions"]:
            self.assertEqual(question["knowledge_role"], "review_prompt")
            self.assertFalse(question["eligible_as_evidence"])

    def test_unreviewed_only_input_has_valid_zero_card_result(self):
        unreviewed = [
            item
            for item in self.records
            if item[1].get("human_review", {}).get("status") == "unreviewed"
        ]
        promoted, issues = promote_l3_priors.promote_records(
            unreviewed,
            self.authorizations,
            at_date=date(2026, 9, 6),
        )
        self.assertEqual(promoted, [])
        self.assertEqual(issues, {"HUMAN_REVIEW_NOT_ACCEPTED": 1})


class L3PriorPromotionCliTests(unittest.TestCase):
    def run_cli(self, arguments):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = promote_l3_priors.main(arguments)
        return code, stream.getvalue(), json.loads(stream.getvalue())

    def test_cli_stdout_is_aggregate_only(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "promoted.jsonl"
            code, stdout, payload = self.run_cli(
                [
                    str(L3_FILE),
                    "--authorizations",
                    str(AUTH_FILE),
                    "--output",
                    str(output),
                    "--at-date",
                    "2026-09-06",
                ]
            )
            output_cards = knowledge_base.load_cards(output)

        self.assertEqual(code, 0)
        self.assertEqual(payload["input_count"], 8)
        self.assertEqual(payload["promoted_count"], 2)
        self.assertEqual(payload["skipped_count"], 6)
        self.assertEqual(set(payload), {"status", "input_count", "promoted_count", "skipped_count", "issue_counts", "privacy"})
        self.assertEqual(len(output_cards), 2)
        for canary in (
            "SYNTHETIC-PRIVATE-TITLE-CANARY",
            "PRIVATE-CONTENT-CANARY",
            "PRIVATE-SOURCE-CANARY",
            str(L3_FILE),
            str(AUTH_FILE),
        ):
            self.assertNotIn(canary, stdout)

    def test_cli_refuses_publishable_output_path(self):
        output = ROOT / "examples" / "synthetic" / "generated-promoted-cards.jsonl"
        self.assertFalse(output.exists())
        code, _, payload = self.run_cli(
            [
                str(L3_FILE),
                "--authorizations",
                str(AUTH_FILE),
                "--output",
                str(output),
                "--at-date",
                "2026-09-06",
            ]
        )
        self.assertEqual(code, 2)
        self.assertEqual(payload["promoted_count"], 0)
        self.assertEqual(
            payload["issue_counts"],
            {"OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION": 1},
        )
        self.assertFalse(output.exists())

    def test_malformed_private_input_reports_only_a_code(self):
        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "PRIVATE-INPUT-PATH-CANARY.jsonl"
            output_path = Path(directory) / "output.jsonl"
            input_path.write_text('{"private":"PRIVATE-BODY-CANARY"', encoding="utf-8")
            code, stdout, payload = self.run_cli(
                [
                    str(input_path),
                    "--authorizations",
                    str(AUTH_FILE),
                    "--output",
                    str(output_path),
                ]
            )
        self.assertEqual(code, 2)
        self.assertEqual(payload["issue_counts"], {"INPUT_JSON_INVALID": 1})
        self.assertNotIn("PRIVATE-BODY-CANARY", stdout)
        self.assertNotIn("PRIVATE-INPUT-PATH-CANARY", stdout)


if __name__ == "__main__":
    unittest.main()
