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

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import experience_support  # noqa: E402
import knowledge_base  # noqa: E402


CARD_FILE = ROOT / "examples" / "synthetic" / "knowledge-cards.jsonl"
SOURCE_MANIFEST_FILE = ROOT / "examples" / "synthetic" / "private-source-manifest.jsonl"
AUTHORIZATION_FILE = ROOT / "examples" / "synthetic" / "authorizations.jsonl"
ENVELOPE_FILE = ROOT / "examples" / "synthetic" / "task-envelope.json"


class ExperienceSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cards = knowledge_base.load_cards(CARD_FILE)
        cls.sources = experience_support.load_source_manifest(SOURCE_MANIFEST_FILE)
        cls.authorizations = knowledge_base.load_authorizations(AUTHORIZATION_FILE)
        cls.envelope = json.loads(ENVELOPE_FILE.read_text(encoding="utf-8"))

    def build_prior(self, *, cards=None, knowledge_hash=None, created_at=None):
        return knowledge_base.build_prior_manifest(
            copy.deepcopy(self.cards if cards is None else cards),
            copy.deepcopy(self.envelope),
            copy.deepcopy(self.authorizations),
            knowledge_snapshot_hash=(
                knowledge_base.sha256_file(CARD_FILE)
                if knowledge_hash is None
                else knowledge_hash
            ),
            authorization_snapshot_hash=knowledge_base.sha256_file(AUTHORIZATION_FILE),
            created_at=created_at or datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
        )

    def build_bundle(
        self,
        *,
        cards=None,
        sources=None,
        prior=None,
        authorizations=None,
        knowledge_hash=None,
        authorization_hash=None,
        created_at=None,
        source_root=None,
        **budget_overrides,
    ):
        cards = copy.deepcopy(self.cards if cards is None else cards)
        knowledge_hash = (
            knowledge_base.sha256_file(CARD_FILE)
            if knowledge_hash is None
            else knowledge_hash
        )
        prior = self.build_prior(cards=cards, knowledge_hash=knowledge_hash) if prior is None else prior
        return experience_support.build_experience_support_bundle(
            cards,
            copy.deepcopy(self.sources if sources is None else sources),
            copy.deepcopy(prior),
            copy.deepcopy(self.envelope),
            copy.deepcopy(self.authorizations if authorizations is None else authorizations),
            knowledge_snapshot_hash=knowledge_hash,
            source_manifest_snapshot_hash=knowledge_base.sha256_file(SOURCE_MANIFEST_FILE),
            authorization_snapshot_hash=(
                knowledge_base.sha256_file(AUTHORIZATION_FILE)
                if authorization_hash is None
                else authorization_hash
            ),
            created_at=created_at or datetime(2026, 9, 6, 1, 2, tzinfo=timezone.utc),
            source_root=source_root,
            **budget_overrides,
        )

    def test_source_fixture_and_json_schemas_are_valid_json(self):
        self.assertEqual(experience_support.validate_source_manifest(self.sources), [])
        self.assertEqual(
            experience_support.validate_card_source_links(self.cards, self.sources),
            [],
        )
        schemas = {}
        for name in (
            "private-source-manifest-record.schema.json",
            "experience-support-bundle.schema.json",
        ):
            value = json.loads((ROOT / "references" / name).read_text(encoding="utf-8"))
            self.assertEqual(value["$schema"], "https://json-schema.org/draft/2020-12/schema")
            Draft202012Validator.check_schema(value)
            schemas[name] = value

        source_validator = Draft202012Validator(
            schemas["private-source-manifest-record.schema.json"]
        )
        for _, source in self.sources:
            self.assertEqual(list(source_validator.iter_errors(source)), [])

        unsafe = copy.deepcopy(self.sources[0][1])
        unsafe["relative_path"] = "https://example.test/private-source.txt"
        self.assertTrue(list(source_validator.iter_errors(unsafe)))
        unsafe = copy.deepcopy(self.sources[0][1])
        unsafe["undeclared_metadata"] = "must fail closed"
        self.assertTrue(list(source_validator.iter_errors(unsafe)))

    def test_bundle_is_hash_bound_and_origin_trace_is_not_fact_evidence(self):
        prior = self.build_prior()
        bundle = self.build_bundle(prior=prior)
        self.assertEqual(experience_support.validate_experience_support_bundle(bundle, prior), [])
        self.assertEqual(len(bundle["support_items"]), 1)
        item = bundle["support_items"][0]
        self.assertEqual(item["knowledge_card_id"], "KC-SYN-HEUR-001")
        self.assertEqual(item["support_level"], "origin_trace_only")
        self.assertEqual(item["origin_trace_status"], "manifest_declared_not_verified")
        self.assertEqual(item["source_asset_verification_status"], "manifest_declared_not_verified")
        self.assertEqual(item["locator_verification_status"], "declared_not_reproduced")
        self.assertEqual(item["truth_status"], "not_established_by_origin_trace")
        self.assertFalse(item["eligible_as_fact_evidence"])
        self.assertTrue(item["fact_use_requires_separate_verification"])
        self.assertEqual(item["origin_source_refs"][0]["source_type"], "reviewer_comment")
        self.assertRegex(item["origin_source_refs"][0]["declared_source_sha256"], r"^[a-f0-9]{64}$")
        self.assertEqual(bundle["bundle_id"], experience_support.compute_bundle_id(bundle))
        self.assertEqual(bundle["bundle_hash"], experience_support.compute_bundle_hash(bundle))
        self.assertEqual(bundle["processor_class"], "model_context_abstract")
        self.assertEqual(
            bundle["source_asset_verification"],
            {
                "status": "manifest_declared_not_verified",
                "declared_source_count": 4,
                "byte_verified_source_count": 0,
                "hash_algorithm": "sha256",
            },
        )

        serialized = json.dumps(bundle, ensure_ascii=False)
        self.assertNotIn("relative_path", serialized)
        self.assertNotIn("fixtures/private", serialized)
        self.assertNotIn("universal quantifier", serialized)
        self.assertNotIn("A narrow sample", serialized)

    def test_rehashing_cannot_turn_origin_trace_into_fact_evidence(self):
        prior = self.build_prior()
        bundle = self.build_bundle(prior=prior)
        item = bundle["support_items"][0]
        item["eligible_as_fact_evidence"] = True
        item["support_item_id"] = experience_support.compute_support_item_id(item)
        bundle["bundle_hash"] = experience_support.compute_bundle_hash(bundle)
        codes = experience_support.validate_experience_support_bundle(bundle, prior)
        self.assertIn("SUPPORT_FACT_EVIDENCE_FORBIDDEN", codes)

    def test_source_root_verifies_every_fixture_byte_hash(self):
        prior = self.build_prior()
        bundle = self.build_bundle(prior=prior, source_root=ROOT)
        verification = bundle["source_asset_verification"]
        self.assertEqual(verification["status"], "byte_hash_verified")
        self.assertEqual(verification["declared_source_count"], 4)
        self.assertEqual(verification["byte_verified_source_count"], 4)
        item = bundle["support_items"][0]
        self.assertEqual(item["origin_trace_status"], "byte_hash_verified")
        self.assertEqual(item["source_asset_verification_status"], "byte_hash_verified")
        self.assertEqual(item["locator_verification_status"], "declared_not_reproduced")
        self.assertEqual(experience_support.validate_experience_support_bundle(bundle, prior), [])

    def test_source_root_escape_missing_and_hash_mismatch_fail_closed(self):
        escaped = copy.deepcopy(self.sources)
        escaped[0][1]["relative_path"] = "../outside-source.txt"
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "SOURCE_ASSET_PATH_ESCAPE",
        ):
            self.build_bundle(sources=escaped, source_root=ROOT)

        url_path = copy.deepcopy(self.sources)
        url_path[0][1]["relative_path"] = "https://example.test/private-source.txt"
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "SOURCE_ASSET_PATH_ESCAPE",
        ):
            self.build_bundle(sources=url_path, source_root=ROOT)

        extra_field = copy.deepcopy(self.sources)
        extra_field[0][1]["undeclared_metadata"] = "must fail closed"
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "SOURCE_MANIFEST_INVALID",
        ):
            self.build_bundle(sources=extra_field, source_root=ROOT)

        missing = copy.deepcopy(self.sources)
        missing[0][1]["relative_path"] = "examples/synthetic/source-assets/not-present.txt"
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "SOURCE_ASSET_MISSING",
        ):
            self.build_bundle(sources=missing, source_root=ROOT)

        mismatched = copy.deepcopy(self.sources)
        mismatched[0][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "SOURCE_ASSET_HASH_MISMATCH",
        ):
            self.build_bundle(sources=mismatched, source_root=ROOT)

    def test_tampering_and_binding_to_another_prior_are_detected(self):
        prior = self.build_prior()
        bundle = self.build_bundle(prior=prior)
        tampered = copy.deepcopy(bundle)
        tampered["support_items"][0]["origin_source_refs"][0]["declared_source_sha256"] = "0" * 64
        codes = experience_support.validate_experience_support_bundle(tampered, prior)
        self.assertIn("SUPPORT_ITEM_HASH_MISMATCH", codes)
        self.assertIn("SUPPORT_BUNDLE_HASH_MISMATCH", codes)

        later_prior = self.build_prior(created_at=datetime(2026, 9, 6, 1, 3, tzinfo=timezone.utc))
        self.assertIn(
            "SUPPORT_BOUND_PRIOR_MISMATCH",
            experience_support.validate_experience_support_bundle(bundle, later_prior),
        )

        malformed_prior_id = copy.deepcopy(bundle)
        malformed_prior_id["prior_manifest_id"] = "not-a-prior-manifest-id"
        malformed_prior_id["bundle_id"] = experience_support.compute_bundle_id(
            malformed_prior_id
        )
        malformed_prior_id["bundle_hash"] = experience_support.compute_bundle_hash(
            malformed_prior_id
        )
        self.assertIn(
            "SUPPORT_PRIOR_MANIFEST_ID",
            experience_support.validate_experience_support_bundle(malformed_prior_id),
        )

    def test_missing_or_mistyped_origin_source_fails_closed(self):
        sources = [
            pair for pair in self.sources if pair[1]["source_id"] != "SRC-SYN-PRIVATE-001"
        ]
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "KNOWLEDGE_SOURCE_LINKS_INVALID",
        ):
            self.build_bundle(sources=sources)

        mismatched = copy.deepcopy(self.sources)
        for _, source in mismatched:
            if source["source_id"] == "SRC-SYN-PRIVATE-001":
                source["source_type"] = "meeting_transcript"
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "KNOWLEDGE_SOURCE_LINKS_INVALID",
        ):
            self.build_bundle(sources=mismatched)

        unselected_missing = [
            pair for pair in self.sources if pair[1]["source_id"] != "SRC-SYN-MEETING-001"
        ]
        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "KNOWLEDGE_SOURCE_LINKS_INVALID",
        ):
            self.build_bundle(sources=unselected_missing)

    def test_authorization_and_snapshot_hashes_are_enforced(self):
        denied = copy.deepcopy(self.authorizations)
        denied["AUTH-SYN-001"]["abstract_guidance_allowed"] = False
        with self.assertRaisesRegex(
            knowledge_base.AuthorizationError,
            "ABSTRACT_MODEL_CONTEXT_DENIED",
        ):
            self.build_bundle(authorizations=denied)

        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "AUTHORIZATION_SNAPSHOT_MISMATCH",
        ):
            self.build_bundle(authorization_hash="f" * 64)

        with self.assertRaisesRegex(
            experience_support.ExperienceSupportError,
            "KNOWLEDGE_SNAPSHOT_MISMATCH",
        ):
            self.build_bundle(
                prior=self.build_prior(),
                knowledge_hash="e" * 64,
            )

    def test_authorization_is_rechecked_at_support_processing_time(self):
        expiring = copy.deepcopy(self.authorizations)
        expiring["AUTH-SYN-001"]["expires_at"] = "2026-09-10"
        auth_hash = "b" * 64
        prior = knowledge_base.build_prior_manifest(
            copy.deepcopy(self.cards),
            copy.deepcopy(self.envelope),
            copy.deepcopy(expiring),
            knowledge_snapshot_hash=knowledge_base.sha256_file(CARD_FILE),
            authorization_snapshot_hash=auth_hash,
            created_at=datetime(2026, 9, 6, 1, 1, tzinfo=timezone.utc),
        )
        with self.assertRaisesRegex(knowledge_base.AuthorizationError, "AUTHORIZATION_EXPIRED"):
            self.build_bundle(
                prior=prior,
                authorizations=expiring,
                authorization_hash=auth_hash,
                created_at=datetime(2026, 9, 12, 1, 2, tzinfo=timezone.utc),
            )

        prior = self.build_prior()
        revoked = copy.deepcopy(self.authorizations)
        revoked["AUTH-SYN-001"]["status"] = "revoked"
        with self.assertRaisesRegex(knowledge_base.AuthorizationError, "AUTHORIZATION_NOT_ACTIVE"):
            self.build_bundle(prior=prior, authorizations=revoked)

    def test_declared_independent_source_remains_an_unverified_lead(self):
        cards = copy.deepcopy(self.cards)
        cards[1][1]["provenance"]["independent_source_ids"] = ["SRC-SYN-PUBLIC-001"]
        knowledge_hash = "d" * 64
        prior = self.build_prior(cards=cards, knowledge_hash=knowledge_hash)
        bundle = self.build_bundle(
            cards=cards,
            prior=prior,
            knowledge_hash=knowledge_hash,
        )
        item = bundle["support_items"][0]
        self.assertEqual(item["support_level"], "origin_trace_with_declared_independent_leads")
        self.assertEqual(item["independence_status"], "declared_not_verified")
        self.assertEqual(len(item["independent_source_refs"]), 1)
        self.assertFalse(item["eligible_as_fact_evidence"])
        self.assertEqual(experience_support.validate_experience_support_bundle(bundle, prior), [])

    def test_budget_omits_whole_item_and_reports_question_id(self):
        cards = copy.deepcopy(self.cards)
        cards[1][1]["provenance"]["independent_source_ids"] = ["SRC-SYN-PUBLIC-001"]
        knowledge_hash = "c" * 64
        prior = self.build_prior(cards=cards, knowledge_hash=knowledge_hash)
        bundle = self.build_bundle(
            cards=cards,
            prior=prior,
            knowledge_hash=knowledge_hash,
            max_source_refs_per_item=1,
        )
        self.assertEqual(bundle["support_items"], [])
        self.assertEqual(bundle["budget_closure_code"], "SUPPORT_BUDGET_TRUNCATED_TO_LIMIT")
        self.assertEqual(
            bundle["coverage"]["omissions"],
            [{
                "prior_question_id": "PQ-KC-SYN-HEUR-001",
                "code": "SUPPORT_SOURCE_REF_LIMIT_OMITTED",
            }],
        )
        self.assertEqual(experience_support.validate_experience_support_bundle(bundle, prior), [])

    def test_cli_stdout_is_metadata_only_and_public_output_is_rejected(self):
        prior = self.build_prior()
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            prior_path = base / "prior.json"
            output_path = base / "support.json"
            prior_path.write_text(json.dumps(prior), encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = experience_support.main(
                    [
                        "build",
                        str(CARD_FILE),
                        str(SOURCE_MANIFEST_FILE),
                        str(prior_path),
                        str(ENVELOPE_FILE),
                        "--authorizations",
                        str(AUTHORIZATION_FILE),
                        "--output",
                        str(output_path),
                        "--created-at",
                        "2026-09-06T01:02:00Z",
                    ]
                )
            payload = json.loads(stream.getvalue())
            bundle = json.loads(output_path.read_text(encoding="utf-8"))

        self.assertEqual(code, 0)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["source_asset_verification_status"], "manifest_declared_not_verified")
        self.assertNotIn("bundle_id", payload)
        self.assertNotIn("bundle_hash", payload)
        self.assertEqual(experience_support.validate_experience_support_bundle(bundle, prior), [])
        stdout = stream.getvalue()
        self.assertNotIn("SRC-SYN", stdout)
        self.assertNotIn("KC-SYN", stdout)
        self.assertNotIn("fixtures/private", stdout)
        self.assertNotIn(str(CARD_FILE), stdout)
        self.assertNotIn(str(SOURCE_MANIFEST_FILE), stdout)

        with self.assertRaisesRegex(
            knowledge_base.PriorManifestError,
            "OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION",
        ):
            knowledge_base.ensure_private_output_path(
                ROOT / "examples" / "synthetic" / "generated-support.json"
            )


if __name__ == "__main__":
    unittest.main()
