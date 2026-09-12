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

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import experience_support  # noqa: E402
import knowledge_base  # noqa: E402
import reading_framework  # noqa: E402


CARD_FILE = ROOT / "examples" / "synthetic" / "knowledge-cards.jsonl"
ENVELOPE_FILE = ROOT / "examples" / "synthetic" / "task-envelope.json"
AUTHORIZATION_FILE = ROOT / "examples" / "synthetic" / "authorizations.jsonl"
SOURCE_MANIFEST_FILE = ROOT / "examples" / "synthetic" / "private-source-manifest.jsonl"
PLAN_FILE = ROOT / "examples" / "synthetic" / "reading-framework-plan.json"
TOPIC_BRIEF_FILE = ROOT / "examples" / "synthetic" / "topic-brief-envelope.json"


class ReadingFrameworkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.envelope = json.loads(ENVELOPE_FILE.read_text(encoding="utf-8"))
        cls.plan_e = json.loads(PLAN_FILE.read_text(encoding="utf-8"))
        cls.topic_brief = json.loads(TOPIC_BRIEF_FILE.read_text(encoding="utf-8"))
        cls.cards = knowledge_base.load_cards(CARD_FILE)
        cls.authorizations = knowledge_base.load_authorizations(AUTHORIZATION_FILE)
        cls.authorization_snapshot_hash = knowledge_base.sha256_file(AUTHORIZATION_FILE)
        cls.source_records = experience_support.load_source_manifest(SOURCE_MANIFEST_FILE)

    def build_prior(self):
        return knowledge_base.build_prior_manifest(
            self.cards,
            copy.deepcopy(self.envelope),
            copy.deepcopy(self.authorizations),
            knowledge_snapshot_hash=knowledge_base.sha256_file(CARD_FILE),
            authorization_snapshot_hash=knowledge_base.sha256_file(AUTHORIZATION_FILE),
            created_at=datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
        )

    def build_support(self, prior):
        return experience_support.build_experience_support_bundle(
            self.cards,
            self.source_records,
            prior,
            copy.deepcopy(self.envelope),
            copy.deepcopy(self.authorizations),
            knowledge_snapshot_hash=knowledge_base.sha256_file(CARD_FILE),
            source_manifest_snapshot_hash=knowledge_base.sha256_file(SOURCE_MANIFEST_FILE),
            authorization_snapshot_hash=knowledge_base.sha256_file(AUTHORIZATION_FILE),
            created_at=datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc),
        )

    @staticmethod
    def plan_f_from(plan_e, support):
        plan = copy.deepcopy(plan_e)
        plan["plan_id"] = "RFP-SYN-F-001"
        plan["created_at"] = "2026-09-06T01:03:00Z"
        plan["input_roles"] = list(reading_framework.F_INPUT_ROLES)
        plan["nodes"][4]["support_item_refs"] = [
            support["support_items"][0]["support_item_id"]
        ]
        return plan

    def test_group_e_freezes_plan_and_binds_later_draft(self):
        prior = self.build_prior()
        framework = reading_framework.build_reading_framework(
            prior,
            copy.deepcopy(self.plan_e),
            copy.deepcopy(self.topic_brief),
            copy.deepcopy(self.authorizations),
            evaluation_group="E",
            authorization_snapshot_hash=self.authorization_snapshot_hash,
            created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc),
        )
        self.assertEqual(
            reading_framework.validate_reading_framework(
                framework,
                prior,
                copy.deepcopy(self.plan_e),
                copy.deepcopy(self.topic_brief),
                copy.deepcopy(self.authorizations),
                authorization_snapshot_hash=self.authorization_snapshot_hash,
                authorization_checked_at=datetime(2026, 9, 6, 1, 3, 30, tzinfo=timezone.utc),
            ),
            [],
        )
        self.assertEqual(framework["evaluation_group"], "E")
        self.assertIsNone(framework["support_bundle_id"])
        self.assertIsNone(framework["support_bundle_hash"])
        self.assertEqual(
            framework["linkage"]["prior_question_ids"],
            ["PQ-KC-SYN-HEUR-001"],
        )
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "synthetic.bin"
            draft.write_bytes(b"FULLY-SYNTHETIC-DRAFT")
            receipt = reading_framework.build_framework_draft_receipt(
                framework,
                prior,
                copy.deepcopy(self.plan_e),
                copy.deepcopy(self.topic_brief),
                copy.deepcopy(self.authorizations),
                draft,
                authorization_snapshot_hash=self.authorization_snapshot_hash,
                ingested_at=datetime(2026, 9, 6, 1, 4, tzinfo=timezone.utc),
            )
        self.assertEqual(reading_framework.validate_framework_receipt(receipt, framework), [])
        self.assertFalse(receipt["draft_content_logged"])
        self.assertFalse(receipt["draft_path_logged"])
        self.assertNotIn("draft_text", receipt)
        self.assertNotIn("draft_path", receipt)

    def test_group_f_requires_valid_support_and_records_actual_use(self):
        prior = self.build_prior()
        support = self.build_support(prior)
        plan_f = self.plan_f_from(self.plan_e, support)
        framework = reading_framework.build_reading_framework(
            prior,
            plan_f,
            copy.deepcopy(self.topic_brief),
            copy.deepcopy(self.authorizations),
            evaluation_group="F",
            authorization_snapshot_hash=self.authorization_snapshot_hash,
            support_bundle=support,
            created_at=datetime(2026, 9, 6, 1, 4, tzinfo=timezone.utc),
        )
        self.assertEqual(
            reading_framework.validate_reading_framework(
                framework,
                prior,
                plan_f,
                copy.deepcopy(self.topic_brief),
                copy.deepcopy(self.authorizations),
                authorization_snapshot_hash=self.authorization_snapshot_hash,
                authorization_checked_at=datetime(2026, 9, 6, 1, 4, 30, tzinfo=timezone.utc),
                support_bundle=support,
            ),
            [],
        )
        self.assertEqual(framework["support_bundle_id"], support["bundle_id"])
        self.assertEqual(framework["support_bundle_hash"], support["bundle_hash"])
        self.assertEqual(
            framework["linkage"]["support_item_ids_used"],
            [support["support_items"][0]["support_item_id"]],
        )

    def test_group_contracts_and_pre_draft_chronology_fail_closed(self):
        prior = self.build_prior()
        support = self.build_support(prior)
        e_with_support = reading_framework.validate_framework_plan(
            copy.deepcopy(self.plan_e),
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
            support_bundle=support,
        )
        self.assertIn("GROUP_E_SUPPORT_FORBIDDEN", e_with_support)

        plan_f = self.plan_f_from(self.plan_e, support)
        f_without_support = reading_framework.validate_framework_plan(
            plan_f,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="F",
        )
        self.assertIn("GROUP_F_SUPPORT_REQUIRED", f_without_support)

        too_early = copy.deepcopy(self.plan_e)
        too_early["created_at"] = prior["created_at"]
        codes = reading_framework.validate_framework_plan(
            too_early,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
        )
        self.assertIn("PLAN_NOT_AFTER_PRIOR_MANIFEST", codes)

    def test_current_model_context_authorization_is_rechecked(self):
        prior = self.build_prior()
        for mutation, expected in (
            ({"status": "revoked"}, "AUTHORIZATION_NOT_ACTIVE"),
            ({"expires_at": "2025-12-31"}, "AUTHORIZATION_EXPIRED"),
            ({"abstract_guidance_allowed": False}, "ABSTRACT_MODEL_CONTEXT_DENIED"),
            ({"allowed_processors": ["local_deterministic"]}, "AUTHORIZATION_PROCESSOR_DENIED"),
        ):
            with self.subTest(expected=expected):
                current = copy.deepcopy(self.authorizations)
                current["AUTH-SYN-001"].update(mutation)
                with self.assertRaisesRegex(reading_framework.FrameworkError, expected):
                    reading_framework.build_reading_framework(
                        prior,
                        copy.deepcopy(self.plan_e),
                        copy.deepcopy(self.topic_brief),
                        current,
                        evaluation_group="E",
                        authorization_snapshot_hash=self.authorization_snapshot_hash,
                        created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc),
                    )

    def test_all_prior_questions_must_be_organized_and_required_node_types_present(self):
        prior = self.build_prior()
        missing_prior = copy.deepcopy(self.plan_e)
        missing_prior["nodes"][8]["prior_question_refs"] = []
        codes = reading_framework.validate_framework_plan(
            missing_prior,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
        )
        self.assertIn("PLAN_PRIOR_COVERAGE", codes)

        missing_node_type = copy.deepcopy(self.plan_e)
        missing_node_type["nodes"] = missing_node_type["nodes"][:-1]
        codes = reading_framework.validate_framework_plan(
            missing_node_type,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
        )
        self.assertIn("PLAN_REQUIRED_NODE_TYPES", codes)

    def test_tampering_and_budget_overrun_are_rejected(self):
        prior = self.build_prior()
        framework = reading_framework.build_reading_framework(
            prior,
            copy.deepcopy(self.plan_e),
            copy.deepcopy(self.topic_brief),
            copy.deepcopy(self.authorizations),
            evaluation_group="E",
            authorization_snapshot_hash=self.authorization_snapshot_hash,
            created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc),
        )
        tampered = copy.deepcopy(framework)
        tampered["semantic_framework"]["nodes"][0]["prompt"] = "TAMPERED"
        codes = reading_framework.validate_reading_framework(
            tampered,
            prior,
            copy.deepcopy(self.plan_e),
            copy.deepcopy(self.topic_brief),
            copy.deepcopy(self.authorizations),
            authorization_snapshot_hash=self.authorization_snapshot_hash,
            authorization_checked_at=datetime(2026, 9, 6, 1, 3, 30, tzinfo=timezone.utc),
        )
        self.assertIn("FRAMEWORK_SEMANTIC_MISMATCH", codes)
        self.assertIn("FRAMEWORK_HASH_MISMATCH", codes)

        with self.assertRaisesRegex(
            reading_framework.FrameworkError,
            "FRAMEWORK_PLAN_CHAR_LIMIT_EXCEEDED",
        ):
            reading_framework.build_reading_framework(
                prior,
                copy.deepcopy(self.plan_e),
                copy.deepcopy(self.topic_brief),
                copy.deepcopy(self.authorizations),
                evaluation_group="E",
                authorization_snapshot_hash=self.authorization_snapshot_hash,
                created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc),
                max_plan_chars=1,
            )

    def test_forbidden_draft_anchors_and_local_paths_are_rejected(self):
        prior = self.build_prior()
        anchored = copy.deepcopy(self.plan_e)
        anchored["nodes"][0]["draft_anchor"] = "paragraph-1"
        codes = reading_framework.validate_framework_plan(
            anchored,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
        )
        self.assertIn("PLAN_FORBIDDEN_FIELD", codes)

        path_leak = copy.deepcopy(self.plan_e)
        path_leak["nodes"][0]["prompt"] = "Read C:\\synthetic\\draft.bin"
        codes = reading_framework.validate_framework_plan(
            path_leak,
            prior,
            copy.deepcopy(self.topic_brief),
            evaluation_group="E",
        )
        self.assertIn("PLAN_LOCAL_PATH", codes)

        for leaked in (r"Read \\server\share\draft.bin", "Read https://invalid.example/draft"):
            with self.subTest(leaked=leaked):
                leaked_plan = copy.deepcopy(self.plan_e)
                leaked_plan["nodes"][0]["prompt"] = leaked
                codes = reading_framework.validate_framework_plan(
                    leaked_plan,
                    prior,
                    copy.deepcopy(self.topic_brief),
                    evaluation_group="E",
                )
                self.assertIn("PLAN_LOCAL_PATH", codes)

    def test_runtime_limits_match_schema(self):
        prior = self.build_prior()
        long_prompt = copy.deepcopy(self.plan_e)
        long_prompt["nodes"][0]["prompt"] = "x" * 2049
        self.assertIn(
            "PLAN_NODE_PROMPT",
            reading_framework.validate_framework_plan(
                long_prompt,
                prior,
                copy.deepcopy(self.topic_brief),
                evaluation_group="E",
            ),
        )
        many_boundaries = copy.deepcopy(self.topic_brief)
        many_boundaries["known_boundaries"] = [f"boundary-{index}" for index in range(13)]
        many_boundaries["brief_hash"] = reading_framework.compute_topic_brief_hash(many_boundaries)
        self.assertIn(
            "TOPIC_BRIEF_KNOWN_BOUNDARIES",
            reading_framework.validate_topic_brief(many_boundaries, prior),
        )

    def test_topic_brief_tampering_and_time_reversal_fail_closed(self):
        prior = self.build_prior()
        self.assertEqual(
            reading_framework.validate_topic_brief(
                copy.deepcopy(self.topic_brief),
                prior,
            ),
            [],
        )

        tampered = copy.deepcopy(self.topic_brief)
        tampered["research_question"] = "TAMPERED"
        self.assertIn(
            "TOPIC_BRIEF_HASH_MISMATCH",
            reading_framework.validate_topic_brief(tampered, prior),
        )

        late = copy.deepcopy(self.topic_brief)
        late["created_at"] = prior["created_at"]
        late["brief_hash"] = reading_framework.compute_topic_brief_hash(late)
        self.assertIn(
            "TOPIC_BRIEF_NOT_BEFORE_PRIOR_MANIFEST",
            reading_framework.validate_topic_brief(late, prior),
        )

    def test_cli_stdout_omits_semantic_and_draft_canaries(self):
        prior = self.build_prior()
        plan = copy.deepcopy(self.plan_e)
        plan["nodes"][0]["prompt"] += " SEMANTIC-CANARY-771"
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            prior_path = base / "prior.json"
            plan_path = base / "PLAN-CANARY-NAME.json"
            topic_brief_path = base / "TOPIC-BRIEF-CANARY-NAME.json"
            framework_path = base / "framework.json"
            draft_path = base / "DRAFT-CANARY-NAME.bin"
            receipt_path = base / "receipt.json"
            prior_path.write_text(json.dumps(prior), encoding="utf-8")
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            topic_brief_path.write_text(json.dumps(self.topic_brief), encoding="utf-8")
            draft_path.write_bytes(b"DRAFT-CANARY-CONTENT-882")

            freeze_stdout = io.StringIO()
            with redirect_stdout(freeze_stdout):
                freeze_code = reading_framework.main(
                    [
                        "freeze",
                        str(prior_path),
                        str(plan_path),
                        str(topic_brief_path),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--group",
                        "E",
                        "--output",
                        str(framework_path),
                        "--created-at",
                        "2026-09-06T01:03:00Z",
                    ]
                )
            frozen_bytes = framework_path.read_bytes()
            duplicate_stdout = io.StringIO()
            with redirect_stdout(duplicate_stdout):
                duplicate_code = reading_framework.main(
                    [
                        "freeze",
                        str(prior_path),
                        str(plan_path),
                        str(topic_brief_path),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--group",
                        "E",
                        "--output",
                        str(framework_path),
                        "--created-at",
                        "2026-09-06T01:03:00Z",
                    ]
                )
            bind_stdout = io.StringIO()
            with redirect_stdout(bind_stdout):
                bind_code = reading_framework.main(
                    [
                        "bind-draft",
                        str(prior_path),
                        str(plan_path),
                        str(topic_brief_path),
                        str(framework_path),
                        str(draft_path),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--output",
                        str(receipt_path),
                        "--ingested-at",
                        "2026-09-06T01:04:00Z",
                    ]
                )
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            frozen_bytes_after_duplicate = framework_path.read_bytes()

        self.assertEqual(freeze_code, 0)
        self.assertEqual(duplicate_code, 2)
        self.assertEqual(frozen_bytes_after_duplicate, frozen_bytes)
        self.assertEqual(bind_code, 0)
        combined = freeze_stdout.getvalue() + duplicate_stdout.getvalue() + bind_stdout.getvalue()
        for forbidden in (
            "SEMANTIC-CANARY-771",
            "PLAN-CANARY-NAME",
            "TOPIC-BRIEF-CANARY-NAME",
            "DRAFT-CANARY-NAME",
            "DRAFT-CANARY-CONTENT-882",
            str(draft_path),
        ):
            self.assertNotIn(forbidden, combined)
        self.assertFalse(receipt["draft_content_logged"])
        self.assertFalse(receipt["draft_path_logged"])

    def test_public_json_schemas_validate_synthetic_artifacts(self):
        schemas = {}
        for name in (
            "topic-brief-envelope.schema.json",
            "reading-framework-plan.schema.json",
            "reading-framework-manifest.schema.json",
            "reading-framework-draft-receipt.schema.json",
        ):
            with self.subTest(name=name):
                parsed = json.loads((ROOT / "references" / name).read_text(encoding="utf-8"))
                self.assertEqual(parsed["$schema"], "https://json-schema.org/draft/2020-12/schema")
                Draft202012Validator.check_schema(parsed)
                schemas[name] = parsed

        prior = self.build_prior()
        framework = reading_framework.build_reading_framework(
            prior,
            copy.deepcopy(self.plan_e),
            copy.deepcopy(self.topic_brief),
            copy.deepcopy(self.authorizations),
            evaluation_group="E",
            authorization_snapshot_hash=self.authorization_snapshot_hash,
            created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc),
        )
        with tempfile.TemporaryDirectory() as directory:
            draft = Path(directory) / "synthetic.bin"
            draft.write_bytes(b"FULLY-SYNTHETIC-DRAFT")
            receipt = reading_framework.build_framework_draft_receipt(
                framework,
                prior,
                copy.deepcopy(self.plan_e),
                copy.deepcopy(self.topic_brief),
                copy.deepcopy(self.authorizations),
                draft,
                authorization_snapshot_hash=self.authorization_snapshot_hash,
                ingested_at=datetime(2026, 9, 6, 1, 4, tzinfo=timezone.utc),
            )
        fixtures = {
            "topic-brief-envelope.schema.json": self.topic_brief,
            "reading-framework-plan.schema.json": self.plan_e,
            "reading-framework-manifest.schema.json": framework,
            "reading-framework-draft-receipt.schema.json": receipt,
        }
        for name, instance in fixtures.items():
            with self.subTest(instance=name):
                validator = Draft202012Validator(schemas[name], format_checker=FormatChecker())
                self.assertEqual(list(validator.iter_errors(instance)), [])


if __name__ == "__main__":
    unittest.main()
