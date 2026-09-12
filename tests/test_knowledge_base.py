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


CARD_FILE = ROOT / "examples" / "synthetic" / "knowledge-cards.jsonl"
ENVELOPE_FILE = ROOT / "examples" / "synthetic" / "task-envelope.json"
AUTHORIZATION_FILE = ROOT / "examples" / "synthetic" / "authorizations.jsonl"


class KnowledgeCardValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = knowledge_base.load_cards(CARD_FILE)

    def test_synthetic_cards_are_valid(self):
        self.assertEqual(len(self.cards), 4)
        self.assertEqual(knowledge_base.validate_cards(self.cards), [])

    def test_private_card_requires_non_exporting_authorization(self):
        line, original = self.cards[1]
        card = copy.deepcopy(original)
        card["rights_and_access"]["authorization_id"] = None
        card["rights_and_access"]["export_allowed"] = True
        card["rights_and_access"]["verbatim_quote_allowed"] = True
        codes = {issue.code for issue in knowledge_base.validate_card(card, line)}
        self.assertIn("PRIVATE_AUTHORIZATION_REQUIRED", codes)
        self.assertIn("PRIVATE_EXPORT_MUST_BE_FALSE", codes)
        self.assertIn("PRIVATE_QUOTE_MUST_BE_FALSE", codes)

    def test_unverified_meeting_statement_cannot_be_upgraded_without_independent_source(self):
        line, original = self.cards[3]
        card = copy.deepcopy(original)
        card["epistemic"]["status"] = "verified"
        card["epistemic"]["fact_inference_boundary"] = "fact"
        card["temporal"]["last_verified_at"] = "2099-03-02"
        codes = {issue.code for issue in knowledge_base.validate_card(card, line)}
        self.assertIn("MEETING_STATEMENT_NOT_VERIFIED", codes)
        self.assertIn("MEETING_STATEMENT_BOUNDARY", codes)


class KnowledgeSearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = knowledge_base.load_cards(CARD_FILE)

    def test_default_search_omits_content_sources_and_paths(self):
        results = knowledge_base.search_cards(
            self.cards,
            "utilization",
            as_of=date(2099, 6, 1),
        )
        self.assertEqual([result["card_id"] for result in results], ["KC-SYN-FACT-001"])
        serialized = json.dumps(results, ensure_ascii=False)
        self.assertNotIn("claim_or_guidance", serialized)
        self.assertNotIn("source_ids", serialized)
        self.assertNotIn("locators", serialized)
        self.assertNotIn("Synthetic utilization baseline", serialized)
        self.assertNotIn(str(CARD_FILE), serialized)
        self.assertTrue(results[0]["eligible_as_evidence"])
        self.assertTrue(results[0]["human_review_required"])

    def test_private_heuristic_is_withheld_without_authorization(self):
        hidden = knowledge_base.search_cards(
            self.cards,
            "quantifier",
            as_of=date(2099, 6, 1),
        )
        self.assertEqual(hidden, [])

        visible_metadata = knowledge_base.search_cards(
            self.cards,
            "quantifier",
            as_of=date(2099, 6, 1),
            include_private=True,
            authorization_ids={"AUTH-SYN-001"},
        )
        self.assertEqual(len(visible_metadata), 1)
        self.assertEqual(visible_metadata[0]["use_mode"], "review_prompt")
        self.assertFalse(visible_metadata[0]["eligible_as_evidence"])
        serialized = json.dumps(visible_metadata, ensure_ascii=False)
        self.assertNotIn("universal quantifier", serialized)
        self.assertNotIn("SRC-SYN-PRIVATE-001", serialized)

    def test_expired_fact_is_refresh_lead_not_current_evidence(self):
        results = knowledge_base.search_cards(
            self.cards,
            "unit-cost",
            as_of=date(2099, 6, 1),
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["freshness"], "expired")
        self.assertEqual(results[0]["use_mode"], "refresh_required")
        self.assertTrue(results[0]["requires_live_verification"])
        self.assertFalse(results[0]["eligible_as_evidence"])

    def test_meeting_statement_remains_reported_lead(self):
        results = knowledge_base.search_cards(
            self.cards,
            "review-time",
            as_of=date(2099, 6, 1),
            include_private=True,
            authorization_ids={"AUTH-SYN-002"},
        )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["use_mode"], "reported_lead")
        self.assertTrue(results[0]["requires_live_verification"])
        self.assertFalse(results[0]["eligible_as_evidence"])

    def test_chinese_query_produces_overlapping_bigrams(self):
        tokens = knowledge_base.tokenize("园区招商机制")
        self.assertIn("园区", tokens)
        self.assertIn("招商", tokens)
        self.assertIn("机制", tokens)


class KnowledgeCliTests(unittest.TestCase):
    def run_cli(self, arguments):
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = knowledge_base.main(arguments)
        return code, json.loads(stream.getvalue())

    def test_validate_and_search_subcommands(self):
        code, payload = self.run_cli(["validate", str(CARD_FILE)])
        self.assertEqual(code, 0)
        self.assertEqual(payload, {"card_count": 4, "issue_count": 0, "status": "valid"})

        code, payload = self.run_cli(
            ["search", str(CARD_FILE), "utilization", "--at-date", "2099-06-01"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(payload["result_count"], 1)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn(str(CARD_FILE), serialized)
        self.assertNotIn("sample utilization is", serialized)

    def test_invalid_file_reports_codes_without_source_content(self):
        invalid = copy.deepcopy(knowledge_base.load_cards(CARD_FILE)[3][1])
        invalid["epistemic"]["status"] = "verified"
        invalid["epistemic"]["fact_inference_boundary"] = "fact"
        invalid["content"]["claim_or_guidance"] = "PRIVATE-CANARY-CONTENT"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cards.jsonl"
            path.write_text(json.dumps(invalid), encoding="utf-8")
            code, payload = self.run_cli(["validate", str(path)])
        self.assertEqual(code, 1)
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertIn("MEETING_STATEMENT_NOT_VERIFIED", serialized)
        self.assertNotIn("PRIVATE-CANARY-CONTENT", serialized)
        self.assertNotIn(str(path), serialized)


class KnowledgePriorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = knowledge_base.load_cards(CARD_FILE)
        cls.envelope = json.loads(ENVELOPE_FILE.read_text(encoding="utf-8"))
        cls.authorizations = knowledge_base.load_authorizations(AUTHORIZATION_FILE)

    def build_manifest(self, envelope=None, authorizations=None, cards=None, **budget_overrides):
        return knowledge_base.build_prior_manifest(
            self.cards if cards is None else cards,
            copy.deepcopy(self.envelope) if envelope is None else envelope,
            copy.deepcopy(self.authorizations) if authorizations is None else authorizations,
            knowledge_snapshot_hash=knowledge_base.sha256_file(CARD_FILE),
            authorization_snapshot_hash=knowledge_base.sha256_file(AUTHORIZATION_FILE),
            created_at=datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
            **budget_overrides,
        )

    def make_prior_card(self, card_id, *, family="quantifier", question=None):
        line, original = self.cards[1]
        card = copy.deepcopy(original)
        card["card_id"] = card_id
        card["tags"] = ["synthetic", family]
        if question is not None:
            card["content"]["claim_or_guidance"] = question
        return line, card

    def test_prior_uses_only_authorized_current_heuristics_or_controls(self):
        manifest = self.build_manifest()
        self.assertEqual([question["knowledge_card_id"] for question in manifest["questions"]], ["KC-SYN-HEUR-001"])
        question = manifest["questions"][0]
        self.assertEqual(question["knowledge_role"], "review_prompt")
        self.assertFalse(question["eligible_as_evidence"])
        self.assertEqual(question["prior_question_id"], "PQ-KC-SYN-HEUR-001")
        self.assertNotIn("provenance", json.dumps(manifest, ensure_ascii=False))
        self.assertEqual(manifest["manifest_hash"], knowledge_base.compute_manifest_hash(manifest))
        self.assertEqual(manifest["schema_version"], "0.2")
        self.assertEqual(manifest["selection_policy_version"], "metadata-scope-char-budget-v0.2")
        self.assertEqual(
            manifest["character_budget"],
            {
                "metric": "canonical-json-unicode-code-points-v1",
                "max_text_array_items": 4,
                "max_text_array_chars": 512,
                "max_question_chars": 2048,
                "max_manifest_chars": 16384,
            },
        )
        self.assertEqual(manifest["budget_closure_code"], "PRIOR_BUDGET_WITHIN_LIMIT")
        self.assertEqual(manifest["budget_issues"], [])
        self.assertLessEqual(
            knowledge_base.canonical_char_count(manifest),
            manifest["character_budget"]["max_manifest_chars"],
        )

    def test_array_item_and_character_boundaries_omit_whole_question(self):
        _, card = self.make_prior_card("KC-SYN-HEUR-010")
        card["scope"]["applies_when"] = ["a", "b"]
        cards = [(2, card)]

        at_item_boundary = self.build_manifest(cards=cards, max_text_array_items=2)
        self.assertEqual(len(at_item_boundary["questions"]), 1)
        over_item_boundary = self.build_manifest(cards=cards, max_text_array_items=1)
        self.assertEqual(over_item_boundary["questions"], [])
        self.assertEqual(
            over_item_boundary["budget_issues"],
            [{"code": "PRIOR_TEXT_ARRAY_ITEM_LIMIT_OMITTED", "count": 1}],
        )

        question = knowledge_base.question_from_card(card)
        array_chars = max(
            knowledge_base.canonical_char_count(question[field])
            for field in knowledge_base.PRIOR_QUESTION_ARRAY_FIELDS
        )
        at_char_boundary = self.build_manifest(cards=cards, max_text_array_chars=array_chars)
        self.assertEqual(len(at_char_boundary["questions"]), 1)
        over_char_boundary = self.build_manifest(cards=cards, max_text_array_chars=array_chars - 1)
        self.assertEqual(over_char_boundary["questions"], [])
        self.assertEqual(
            over_char_boundary["budget_issues"],
            [{"code": "PRIOR_TEXT_ARRAY_CHAR_LIMIT_OMITTED", "count": 1}],
        )

    def test_question_boundary_and_oversized_first_candidate_are_deterministic(self):
        _, ordinary = self.make_prior_card("KC-SYN-HEUR-001")
        ordinary_question = knowledge_base.question_from_card(ordinary)
        question_chars = knowledge_base.canonical_char_count(ordinary_question)
        at_boundary = self.build_manifest(cards=[(2, ordinary)], max_question_chars=question_chars)
        self.assertEqual(len(at_boundary["questions"]), 1)
        over_boundary = self.build_manifest(cards=[(2, ordinary)], max_question_chars=question_chars - 1)
        self.assertEqual(over_boundary["questions"], [])
        self.assertEqual(
            over_boundary["budget_issues"],
            [{"code": "PRIOR_QUESTION_CHAR_LIMIT_OMITTED", "count": 1}],
        )

        canary = "PRIVATE-CANARY-" + ("x" * 2200)
        _, oversized = self.make_prior_card("KC-SYN-HEUR-000", question=canary)
        manifest = self.build_manifest(cards=[(2, ordinary), (2, oversized)])
        self.assertEqual(
            [question["knowledge_card_id"] for question in manifest["questions"]],
            ["KC-SYN-HEUR-001"],
        )
        self.assertEqual(
            manifest["budget_issues"],
            [{"code": "PRIOR_QUESTION_CHAR_LIMIT_OMITTED", "count": 1}],
        )
        self.assertNotIn("PRIVATE-CANARY", json.dumps(manifest, ensure_ascii=False))

    def test_manifest_budget_removes_only_stable_tail_and_base_fails_closed(self):
        first = self.make_prior_card("KC-SYN-HEUR-001")
        second = self.make_prior_card("KC-SYN-HEUR-002")
        cards = [second, first]
        full = self.build_manifest(cards=cards)
        self.assertEqual(
            [question["knowledge_card_id"] for question in full["questions"]],
            ["KC-SYN-HEUR-001", "KC-SYN-HEUR-002"],
        )
        tight = self.build_manifest(
            cards=cards,
            max_manifest_chars=knowledge_base.canonical_char_count(full) - 10,
        )
        self.assertEqual(tight["questions"], full["questions"][:-1])
        self.assertEqual(
            tight["budget_issues"],
            [{"code": "PRIOR_MANIFEST_CHAR_LIMIT_OMITTED", "count": 1}],
        )
        self.assertEqual(tight["budget_closure_code"], "PRIOR_BUDGET_TRUNCATED_TO_LIMIT")
        self.assertLessEqual(
            knowledge_base.canonical_char_count(tight),
            tight["character_budget"]["max_manifest_chars"],
        )
        with self.assertRaisesRegex(
            knowledge_base.PriorManifestError,
            "PRIOR_MANIFEST_BASE_OVER_BUDGET",
        ):
            self.build_manifest(max_manifest_chars=1)

    def test_budget_hard_caps_cannot_be_raised(self):
        with self.assertRaisesRegex(
            knowledge_base.PriorManifestError,
            "PRIOR_BUDGET_ABOVE_HARD_LIMIT",
        ):
            self.build_manifest(
                max_question_chars=knowledge_base.HARD_MAX_QUESTION_CHARS + 1
            )

    def test_prior_fails_closed_when_authorization_is_missing_or_too_broad(self):
        with self.assertRaisesRegex(knowledge_base.AuthorizationError, "AUTHORIZATION_NOT_FOUND"):
            self.build_manifest(authorizations={})

        denied = copy.deepcopy(self.authorizations)
        denied["AUTH-SYN-001"].pop("abstract_guidance_allowed")
        with self.assertRaisesRegex(knowledge_base.AuthorizationError, "ABSTRACT_MODEL_CONTEXT_DENIED"):
            self.build_manifest(authorizations=denied)

    def test_legacy_authorization_defaults_to_local_deterministic_private_review(self):
        envelope = copy.deepcopy(self.envelope)
        envelope["authorization_context"]["processor_class"] = "local_deterministic"
        legacy = copy.deepcopy(self.authorizations)
        legacy["AUTH-SYN-001"].pop("allowed_processors")
        legacy["AUTH-SYN-001"].pop("output_audiences")
        manifest = self.build_manifest(envelope=envelope, authorizations=legacy)
        self.assertEqual(len(manifest["questions"]), 1)

    def test_precommit_rejects_draft_derived_routing(self):
        envelope = copy.deepcopy(self.envelope)
        envelope["field_provenance"]["domain_codes"] = "draft_text"
        with self.assertRaisesRegex(knowledge_base.PriorManifestError, "DRAFT_DERIVED_ROUTING_FORBIDDEN"):
            self.build_manifest(envelope=envelope)

    def test_prior_cli_writes_manifest_but_omits_private_body_and_paths_from_stdout(self):
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "prior.json"
            with redirect_stdout(stream):
                code = knowledge_base.main(
                    [
                        "prior",
                        str(CARD_FILE),
                        str(ENVELOPE_FILE),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--output",
                        str(output),
                        "--created-at",
                        "2026-09-06T01:01:00Z",
                    ]
                )
            manifest = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        self.assertEqual(len(manifest["questions"]), 1)
        stdout = stream.getvalue()
        self.assertNotIn("universal quantifier", stdout)
        self.assertNotIn("SRC-SYN-PRIVATE", stdout)
        self.assertNotIn(str(CARD_FILE), stdout)
        self.assertNotIn(str(AUTHORIZATION_FILE), stdout)
        payload = json.loads(stdout)
        self.assertEqual(payload["budget_closure_code"], "PRIOR_BUDGET_WITHIN_LIMIT")
        self.assertEqual(payload["issue_codes"], [])
        self.assertEqual(
            payload["canonical_char_count"],
            knowledge_base.canonical_char_count(manifest),
        )

    def test_budget_cli_omits_canary_and_does_not_write_on_fatal_budget(self):
        _, oversized = self.make_prior_card(
            "KC-SYN-HEUR-000",
            question="PRIVATE-CANARY-CONTENT-" + ("x" * 2200),
        )
        _, ordinary = self.make_prior_card("KC-SYN-HEUR-001")
        stream = io.StringIO()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            cards_path = base / "PRIVATE-CANARY-CARDS.jsonl"
            output = base / "prior.json"
            cards_path.write_text(
                "\n".join(json.dumps(card) for card in (oversized, ordinary)) + "\n",
                encoding="utf-8",
            )
            with redirect_stdout(stream):
                code = knowledge_base.main(
                    [
                        "prior",
                        str(cards_path),
                        str(ENVELOPE_FILE),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--output",
                        str(output),
                        "--created-at",
                        "2026-09-06T01:01:00Z",
                    ]
                )
            payload = json.loads(stream.getvalue())
            self.assertTrue(output.exists())
            fatal_output = base / "fatal.json"
            fatal_stream = io.StringIO()
            with redirect_stdout(fatal_stream):
                fatal_code = knowledge_base.main(
                    [
                        "prior",
                        str(cards_path),
                        str(ENVELOPE_FILE),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--output",
                        str(fatal_output),
                        "--created-at",
                        "2026-09-06T01:01:00Z",
                        "--max-manifest-chars",
                        "1",
                    ]
                )
            fatal_payload = json.loads(fatal_stream.getvalue())
            self.assertFalse(fatal_output.exists())
        self.assertEqual(code, 0)
        self.assertEqual(payload["issue_codes"], ["PRIOR_QUESTION_CHAR_LIMIT_OMITTED"])
        self.assertNotIn("PRIVATE-CANARY", stream.getvalue())
        self.assertEqual(fatal_code, 2)
        self.assertEqual(fatal_payload["error"], "PRIOR_MANIFEST_BASE_OVER_BUDGET")
        self.assertNotIn("PRIVATE-CANARY", fatal_stream.getvalue())

    def test_prior_artifact_cannot_be_written_to_publishable_tree(self):
        with self.assertRaisesRegex(
            knowledge_base.PriorManifestError,
            "OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION",
        ):
            knowledge_base.ensure_private_output_path(ROOT / "examples" / "synthetic" / "generated-private.json")


if __name__ == "__main__":
    unittest.main()
