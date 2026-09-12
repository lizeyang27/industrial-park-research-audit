from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "scripts" / "build_layered_corpus.py"
VALIDATE = ROOT / "scripts" / "validate_layered_corpus.py"
AUTHORIZATION_ID = "AUTH-SYN-LOCAL-001"
CORPUS_ID = "CORPUS-SYN-001"


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


class LayeredCorpusValidationTests(unittest.TestCase):
    maxDiff = None

    def build_corpus(self, directory: str, *, duplicate_clean_text: bool = False) -> tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]:
        root = Path(directory) / "corpus"
        root.mkdir()
        capture_count = 2 if duplicate_clean_text else 1
        staging_rows: list[dict[str, Any]] = []
        discovery_rows: list[dict[str, Any]] = []
        for index in range(capture_count):
            article_id = f"ART-SYN-{index + 1}"
            # The differing read counter changes L0 while being stripped from L1,
            # creating a fully synthetic duplicate-publication test case.
            body = f"Synthetic title\nOriginal\nSynthetic author\nSep 0{index + 1}, 2099, 4:00 PM\nSYNTHETIC_SECRET_SENTENCE\nReads{index + 1}"
            staging_rows.append(
                {
                    "record_type": "staging_article_capture",
                    "snapshot_id": "SNAP-SYN",
                    "article_id": article_id,
                    "title": "Synthetic title",
                    "publisher_name": "Synthetic publisher",
                    "published_date_local": f"2099-09-0{index + 1}",
                    "published_at_raw": f"Sep 0{index + 1}, 2099, 4:00 PM",
                    "source_locator": {"original_url": None, "native_article_id": None},
                    "document_text": body,
                    "document_text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                    "capture_status": "captured",
                    "captured_at": "2099-09-06T12:00:00Z",
                    "rights_ref": AUTHORIZATION_ID,
                    "instructions_treated_as_data": True,
                }
            )
            discovery_rows.append(
                {
                    "record_id": f"DISC-SYN-{index + 1}",
                    "article_id": article_id,
                    "published_date_local": f"2099-09-0{index + 1}",
                    "instructions_treated_as_data": True,
                }
            )
        staging = root / "staging.jsonl"
        write_jsonl(staging, staging_rows)
        built = subprocess.run(
            [
                sys.executable,
                str(BUILD),
                "--staging",
                str(staging),
                "--corpus-root",
                str(root),
                "--snapshot-id",
                "SNAP-SYN",
                "--corpus-id",
                CORPUS_ID,
                "--authorization-id",
                AUTHORIZATION_ID,
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(built.returncode, 0, built.stdout)

        discovery_rows.sort(key=lambda row: row["published_date_local"], reverse=True)
        discovery = root / "snapshots" / "SNAP-SYN" / "discovery-index.jsonl"
        write_jsonl(discovery, discovery_rows)
        digest = hashlib.sha256(discovery.read_bytes()).hexdigest()
        (discovery.parent / "snapshot-manifest.json").write_text(
            json.dumps(
                {
                    "status": "frozen",
                    "selection": {"target_count": capture_count},
                    "counts": {"selected": capture_count},
                    "input_manifest_sha256": digest,
                }
            ),
            encoding="utf-8",
        )
        (root / "corpus-manifest.json").write_text(
            json.dumps(
                {
                    "current_snapshot_id": "SNAP-SYN",
                    "record_counts": {
                        "l0_raw": capture_count,
                        "l1_index": capture_count,
                        "l2_article_maps": 0,
                    },
                }
            ),
            encoding="utf-8",
        )
        l0 = [json.loads(line) for line in (root / "l0_raw" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        l1 = [json.loads(line) for line in (root / "l1_index" / "records.jsonl").read_text(encoding="utf-8").splitlines()]
        return root, l0, l1

    def run_validator(self, root: Path) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        result = subprocess.run([sys.executable, str(VALIDATE), str(root)], capture_output=True, text=True)
        return result, json.loads(result.stdout)

    @staticmethod
    def l2_record(record_id: str, l0: dict[str, Any]) -> dict[str, Any]:
        return {
            "record_id": record_id,
            "source_version_id": l0["source_version_id"],
            "article_id": l0["article_id"],
            "rights_ref": AUTHORIZATION_ID,
            "claim_nodes": [],
        }

    @staticmethod
    def update_counts(root: Path, **counts: int) -> None:
        path = root / "corpus-manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["record_counts"].update(counts)
        path.write_text(json.dumps(manifest), encoding="utf-8")

    def test_empty_high_layers_are_a_valid_partial_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.build_corpus(directory)
            for name in ("l3_cards", "l4_clusters", "l5_routes", "l6_prior_packs"):
                (root / name).mkdir()
            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "valid")
            self.assertEqual(report["summary"]["logical_articles"], 1)
            self.assertEqual([report["summary"][f"l{level}"] for level in range(3, 7)], [0, 0, 0, 0])
            self.assertNotIn("SYNTHETIC_SECRET_SENTENCE", result.stdout)

    def test_valid_l3_to_l6_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, l0, l1 = self.build_corpus(directory)
            l2 = self.l2_record("L2-SYN-001", l0[0])
            write_jsonl(root / "l2_article_maps" / "synthetic.jsonl", [l2])
            l3 = {
                "record_id": "L3-SYN-001",
                "card_type": "fact_evidence",
                "rights_ref": AUTHORIZATION_ID,
                "source_refs": [
                    {
                        "article_id": l1[0]["article_id"],
                        "source_version_id": l1[0]["source_version_id"],
                        "locator_refs": [dict(l1[0]["locator"])],
                    }
                ],
                "independent_source_ids": ["INDEPENDENT-SYN-001"],
                "epistemic": {"status": "verified", "fact_inference_boundary": "fact"},
            }
            write_jsonl(root / "l3_cards" / "synthetic.jsonl", [l3])
            l4 = {
                "record_id": "L4-SYN-001",
                "memberships": [
                    {"member_id": "L2-SYN-001", "member_type": "article_map", "score": 1},
                    {"member_id": "L3-SYN-001", "member_type": "knowledge_card", "score": 1},
                ],
            }
            write_jsonl(root / "l4_clusters" / "synthetic.jsonl", [l4])
            l5 = {
                "record_id": "L5-SYN-001",
                "candidate_cluster_ids": ["L4-SYN-001"],
                "authorization_id": AUTHORIZATION_ID,
                "allowed_card_types": ["fact_evidence"],
                "source_priority": ["fact_evidence", "expert_heuristic", "reported_statement"],
                "evidence_requirement": "verified evidence required",
            }
            write_jsonl(root / "l5_routes" / "synthetic.jsonl", [l5])
            l6 = {
                "record_id": "L6-SYN-001",
                "included_record_ids": ["L2-SYN-001", "L3-SYN-001", "L4-SYN-001", "L5-SYN-001"],
                "build_provenance": {
                    "snapshot_ids": ["SNAP-SYN"],
                    "route_ids": ["L5-SYN-001"],
                    "cluster_ids": ["L4-SYN-001"],
                    "card_ids": ["L3-SYN-001"],
                    "build_run_id": "RUN-SYN-001",
                },
                "human_approval": {"required": True, "status": "pending", "approved_at": None},
                "evidence_status": "prior_only",
            }
            write_jsonl(root / "l6_prior_packs" / "synthetic.jsonl", [l6])
            self.update_counts(
                root,
                l2_article_maps=1,
                l3_cards=1,
                l4_clusters=1,
                l5_routes=1,
                l6_prior_packs=1,
            )

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "valid")
            self.assertNotIn("SYNTHETIC_SECRET_SENTENCE", result.stdout)

    def test_l3_to_l6_fail_closed_issue_codes_are_aggregate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, l0, l1 = self.build_corpus(directory, duplicate_clean_text=True)
            l2_rows = [self.l2_record(f"L2-SYN-{index + 1}", row) for index, row in enumerate(l0)]
            write_jsonl(root / "l2_article_maps" / "synthetic.jsonl", l2_rows)

            bad_locator = dict(l1[0]["locator"])
            bad_locator["char_start"] += 1
            l3_rows = [
                {
                    "record_id": "L3-SYN-REPORTED",
                    "card_type": "reported_statement",
                    "rights_ref": AUTHORIZATION_ID,
                    "source_refs": [
                        {
                            "article_id": l1[0]["article_id"],
                            "source_version_id": l1[0]["source_version_id"],
                            "locator_refs": [bad_locator],
                        }
                    ],
                    "independent_source_ids": [],
                    "verification": {"required_before_evidence_use": False},
                    "epistemic": {"status": "verified", "fact_inference_boundary": "reported_statement"},
                },
                {
                    "record_id": "L3-SYN-FACT",
                    "card_type": "fact_evidence",
                    "rights_ref": AUTHORIZATION_ID,
                    "source_refs": [
                        {
                            "article_id": l1[0]["article_id"],
                            "source_version_id": l1[0]["source_version_id"],
                            "locator_refs": [dict(l1[0]["locator"])],
                        }
                    ],
                    "independent_source_ids": [],
                    "epistemic": {"status": "verified", "fact_inference_boundary": "fact"},
                },
            ]
            write_jsonl(root / "l3_cards" / "synthetic.jsonl", l3_rows)
            l4 = {
                "record_id": "L4-SYN-001",
                "memberships": [
                    {"member_id": "L2-SYN-1", "member_type": "article_map", "score": 1},
                    {"member_id": "L2-SYN-2", "member_type": "article_map", "score": 1},
                    {"member_id": "L3-NOT-THERE", "member_type": "knowledge_card"},
                ],
            }
            write_jsonl(root / "l4_clusters" / "synthetic.jsonl", [l4])
            l5 = {
                "record_id": "L5-SYN-001",
                "candidate_cluster_ids": ["L4-NOT-THERE"],
                "authorization_id": "AUTH-SYN-UNKNOWN",
                "allowed_card_types": ["fact_evidence", "reported_statement"],
                "source_priority": ["reported_statement", "fact_evidence"],
                "evidence_requirement": "verified only",
            }
            write_jsonl(root / "l5_routes" / "synthetic.jsonl", [l5])
            l6 = {
                "record_id": "L6-SYN-001",
                "included_record_ids": ["L6-NOT-THERE"],
                "build_provenance": {
                    "snapshot_ids": ["SNAP-NOT-THERE"],
                    "route_ids": ["L5-NOT-THERE"],
                    "cluster_ids": ["L4-NOT-THERE"],
                    "card_ids": ["L3-NOT-THERE"],
                    "build_run_id": "",
                },
                "human_approval": {"required": True, "status": "pending", "approved_at": None},
                "evidence_status": "verified",
            }
            write_jsonl(root / "l6_prior_packs" / "synthetic.jsonl", [l6])
            self.update_counts(
                root,
                l2_article_maps=2,
                l3_cards=2,
                l4_clusters=1,
                l5_routes=1,
                l6_prior_packs=1,
            )

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stdout)
            codes = {item["code"] for item in report["issues"]}
            self.assertTrue(
                {
                    "L3_SOURCE_REF_LOCATOR_MISMATCH",
                    "L3_REPORTED_STATEMENT_PREMATURELY_VERIFIED",
                    "L3_REPORTED_STATEMENT_EVIDENCE_GATE_MISSING",
                    "L3_FACT_EVIDENCE_INDEPENDENT_SOURCE_REQUIRED",
                    "L4_MEMBERSHIP_TARGET_MISSING",
                    "L4_DUPLICATE_CONTENT_DOUBLE_WEIGHT",
                    "L5_CLUSTER_ID_MISSING",
                    "L5_AUTHORIZATION_ID_UNKNOWN",
                    "L5_REPORTED_SOURCE_PRIORITY_TOO_HIGH",
                    "L5_EVIDENCE_PRIORITY_BOUNDARY",
                    "L6_INCLUDED_RECORD_ID_MISSING",
                    "L6_BUILD_PROVENANCE_REFERENCE_MISSING",
                    "L6_PENDING_APPROVAL_NOT_PRIOR_ONLY",
                }.issubset(codes),
                codes,
            )
            for sensitive in ("SYNTHETIC_SECRET_SENTENCE", "L6-NOT-THERE", str(root)):
                self.assertNotIn(sensitive, result.stdout)

    def test_duplicate_clean_text_with_explicit_zero_weight_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, l0, _ = self.build_corpus(directory, duplicate_clean_text=True)
            l2_rows = [self.l2_record(f"L2-SYN-{index + 1}", row) for index, row in enumerate(l0)]
            write_jsonl(root / "l2_article_maps" / "synthetic.jsonl", l2_rows)
            write_jsonl(
                root / "l4_clusters" / "synthetic.jsonl",
                [
                    {
                        "record_id": "L4-SYN-DEDUP",
                        "memberships": [
                            {"member_id": "L2-SYN-1", "member_type": "article_map", "score": 1},
                            {"member_id": "L2-SYN-2", "member_type": "article_map", "score": 0},
                        ],
                    }
                ],
            )
            self.update_counts(root, l2_article_maps=2, l4_clusters=1)
            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "valid")
            self.assertNotIn("SYNTHETIC_SECRET_SENTENCE", result.stdout)

    def test_high_layer_manifest_count_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.build_corpus(directory)
            self.update_counts(root, l6_prior_packs=1)
            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertEqual(report["issues"], [{"code": "CORPUS_MANIFEST_COUNT_MISMATCH", "count": 1}])

    def test_two_frozen_snapshots_can_share_one_layered_corpus(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.build_corpus(directory)
            staging = root / "staging-second.jsonl"
            body = "Second synthetic title\nSecond synthetic body"
            write_jsonl(
                staging,
                [
                    {
                        "record_type": "staging_article_capture",
                        "snapshot_id": "SNAP-SYN-SECOND",
                        "article_id": "ART-SYN-SECOND",
                        "title": "Second synthetic title",
                        "publisher_name": "Synthetic publisher",
                        "published_date_local": "2099-08-31",
                        "document_text": body,
                        "document_text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "capture_status": "captured",
                        "captured_at": "2099-09-07T12:00:00Z",
                        "rights_ref": AUTHORIZATION_ID,
                        "instructions_treated_as_data": True,
                    }
                ],
            )
            built = subprocess.run(
                [
                    sys.executable,
                    str(BUILD),
                    "--staging",
                    str(staging),
                    "--corpus-root",
                    str(root),
                    "--snapshot-id",
                    "SNAP-SYN-SECOND",
                    "--corpus-id",
                    CORPUS_ID,
                    "--authorization-id",
                    AUTHORIZATION_ID,
                    "--merge-existing",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(built.returncode, 0, built.stdout)

            discovery = root / "snapshots" / "SNAP-SYN-SECOND" / "discovery-index.jsonl"
            write_jsonl(
                discovery,
                [
                    {
                        "record_id": "DISC-SYN-SECOND",
                        "article_id": "ART-SYN-SECOND",
                        "published_date_local": "2099-08-31",
                        "instructions_treated_as_data": True,
                    }
                ],
            )
            digest = hashlib.sha256(discovery.read_bytes()).hexdigest()
            (discovery.parent / "snapshot-manifest.json").write_text(
                json.dumps(
                    {
                        "status": "frozen",
                        "snapshot_id": "SNAP-SYN-SECOND",
                        "selection": {"target_count": 1},
                        "counts": {"selected": 1},
                        "input_manifest_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            manifest_path = root / "corpus-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["current_snapshot_id"] = "SNAP-SYN-SECOND"
            manifest["snapshot_ids"] = ["SNAP-SYN", "SNAP-SYN-SECOND"]
            manifest["record_counts"].update(
                {"snapshots": 2, "discovery_index": 2, "l0_raw": 2, "l1_index": 2}
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "valid")
            self.assertEqual(report["summary"]["snapshots"], 2)
            self.assertEqual(report["summary"]["discovery"], 2)

    def test_two_source_versions_of_one_article_form_a_valid_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, first_l0, _ = self.build_corpus(directory)
            body = "Synthetic title\nA revised synthetic body"
            staging = root / "staging-revision.jsonl"
            write_jsonl(
                staging,
                [
                    {
                        "record_type": "staging_article_capture",
                        "snapshot_id": "SNAP-SYN-REVISION",
                        "article_id": first_l0[0]["article_id"],
                        "title": "Synthetic title",
                        "publisher_name": "Synthetic publisher",
                        "published_date_local": "2099-09-01",
                        "document_text": body,
                        "document_text_sha256": hashlib.sha256(body.encode()).hexdigest(),
                        "capture_status": "captured",
                        "captured_at": "2099-09-07T12:00:00Z",
                        "rights_ref": AUTHORIZATION_ID,
                        "instructions_treated_as_data": True,
                    }
                ],
            )
            built = subprocess.run(
                [
                    sys.executable,
                    str(BUILD),
                    "--staging",
                    str(staging),
                    "--corpus-root",
                    str(root),
                    "--snapshot-id",
                    "SNAP-SYN-REVISION",
                    "--corpus-id",
                    CORPUS_ID,
                    "--authorization-id",
                    AUTHORIZATION_ID,
                    "--merge-existing",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(built.returncode, 0, built.stdout)

            discovery = root / "snapshots" / "SNAP-SYN-REVISION" / "discovery-index.jsonl"
            write_jsonl(
                discovery,
                [
                    {
                        "record_id": "DISC-SYN-REVISION",
                        "article_id": first_l0[0]["article_id"],
                        "published_date_local": "2099-09-01",
                        "instructions_treated_as_data": True,
                    }
                ],
            )
            digest = hashlib.sha256(discovery.read_bytes()).hexdigest()
            (discovery.parent / "snapshot-manifest.json").write_text(
                json.dumps(
                    {
                        "status": "frozen",
                        "snapshot_id": "SNAP-SYN-REVISION",
                        "selection": {"target_count": 1},
                        "counts": {"selected": 1},
                        "input_manifest_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            manifest_path = root / "corpus-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["current_snapshot_id"] = "SNAP-SYN-REVISION"
            manifest["snapshot_ids"] = ["SNAP-SYN", "SNAP-SYN-REVISION"]
            manifest["record_counts"].update(
                {"snapshots": 2, "discovery_index": 2, "l0_raw": 2, "l1_index": 2}
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(report["status"], "valid")

    def test_duplicate_article_versions_without_lineage_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.build_corpus(directory)
            l0_path = root / "l0_raw" / "records.jsonl"
            rows = [json.loads(line) for line in l0_path.read_text(encoding="utf-8").splitlines()]
            duplicate = dict(rows[0])
            duplicate["record_id"] = "L0-SYN-UNLINKED"
            duplicate["source_version_id"] = "SRCV-SYN-UNLINKED"
            duplicate["raw_object"] = dict(duplicate["raw_object"])
            duplicate["supersedes_source_version_id"] = None
            rows.append(duplicate)
            write_jsonl(l0_path, rows)
            self.update_counts(root, l0_raw=2)

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn(
                "L0_ARTICLE_VERSION_LINEAGE_INVALID",
                {item["code"] for item in report["issues"]},
            )

    def test_l0_rejects_unknown_snapshot_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root, _, _ = self.build_corpus(directory)
            l0_path = root / "l0_raw" / "records.jsonl"
            rows = [json.loads(line) for line in l0_path.read_text(encoding="utf-8").splitlines()]
            rows[0]["snapshot_ids"] = ["SNAP-UNKNOWN"]
            write_jsonl(l0_path, rows)

            result, report = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stdout)
            self.assertIn("L0_SNAPSHOT_LINK_MISSING", {item["code"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
