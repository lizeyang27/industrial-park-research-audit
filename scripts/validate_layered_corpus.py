#!/usr/bin/env python3
"""Validate a private L0-L6 corpus lineage without disclosing source content.

Reports contain aggregate counts and issue codes only. Titles, text, URLs,
hashes, filenames, and absolute paths are intentionally omitted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("JSON root is not an object")
    return value


def read_jsonl(path: Path, *, optional: bool = False) -> list[dict[str, Any]]:
    if optional and not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"JSONL line {line_number} is not an object")
        rows.append(value)
    return rows


def read_jsonl_directory(path: Path) -> list[dict[str, Any]]:
    """Read a layer directory while treating a missing or empty layer as partial."""
    if not path.is_dir():
        return []
    return [row for child in sorted(path.glob("*.jsonl")) for row in read_jsonl(child)]


def string_set(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {item for item in value if isinstance(item, str) and item}


def record_index(rows: list[dict[str, Any]], key: str, invalid_code: str, issue) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in rows:
        record_id = row.get(key)
        if not isinstance(record_id, str) or not record_id or record_id in indexed:
            issue(invalid_code)
        else:
            indexed[record_id] = row
    return indexed


def safe_child(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    resolved_root = root.resolve()
    if candidate != resolved_root and resolved_root not in candidate.parents:
        raise ValueError("relative object path escapes corpus root")
    return candidate


class Validator:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.issues: Counter[str] = Counter()
        self.counts: dict[str, int] = {}

    def issue(self, code: str) -> None:
        self.issues[code] += 1

    def validate(self) -> dict[str, Any]:
        manifest = read_json(self.root / "corpus-manifest.json")
        current_snapshot_id = manifest.get("current_snapshot_id")
        if not isinstance(current_snapshot_id, str) or not current_snapshot_id:
            raise ValueError("corpus has no current frozen snapshot")
        declared_snapshot_ids = manifest.get("snapshot_ids")
        if declared_snapshot_ids is None:
            snapshot_ids = [current_snapshot_id]
        elif (
            not isinstance(declared_snapshot_ids, list)
            or not declared_snapshot_ids
            or any(not isinstance(value, str) or not value for value in declared_snapshot_ids)
            or len(declared_snapshot_ids) != len(set(declared_snapshot_ids))
            or current_snapshot_id not in declared_snapshot_ids
        ):
            raise ValueError("corpus has an invalid snapshot_ids declaration")
        else:
            snapshot_ids = declared_snapshot_ids

        discoveries_by_snapshot: dict[str, list[dict[str, Any]]] = {}
        for snapshot_id in snapshot_ids:
            snapshot_root = self.root / "snapshots" / snapshot_id
            snapshot = read_json(snapshot_root / "snapshot-manifest.json")
            if snapshot.get("snapshot_id") not in {None, snapshot_id}:
                self.issue("SNAPSHOT_ID_MISMATCH")
            discovery_path = snapshot_root / "discovery-index.jsonl"
            discovery = read_jsonl(discovery_path)
            discoveries_by_snapshot[snapshot_id] = discovery
            self._validate_snapshot(snapshot, discovery_path, discovery)
        l0 = read_jsonl(self.root / "l0_raw" / "records.jsonl", optional=True)
        l1 = read_jsonl(self.root / "l1_index" / "records.jsonl", optional=True)
        l2 = read_jsonl_directory(self.root / "l2_article_maps")
        l3 = read_jsonl_directory(self.root / "l3_cards")
        l4 = read_jsonl_directory(self.root / "l4_clusters")
        l5 = read_jsonl_directory(self.root / "l5_routes")
        l6 = read_jsonl_directory(self.root / "l6_prior_packs")
        audit = read_jsonl(self.root / "audit" / "ingest-events.jsonl", optional=True)
        self.counts = {
            "snapshots": len(snapshot_ids),
            "discovery": sum(len(rows) for rows in discoveries_by_snapshot.values()),
            "logical_articles": len(
                {
                    row.get("article_id")
                    for row in l0
                    if isinstance(row.get("article_id"), str) and row.get("article_id")
                }
            ),
            "l0": len(l0),
            "l1": len(l1),
            "l2": len(l2),
            "l3": len(l3),
            "l4": len(l4),
            "l5": len(l5),
            "l6": len(l6),
            "audit_events": len(audit),
        }

        l0_by_source, l0_article_ids = self._validate_l0(discoveries_by_snapshot, l0)
        l1_by_chunk = self._validate_l1(l0_by_source, l1)
        l2_by_record = self._validate_l2(l0_by_source, l0_article_ids, l1_by_chunk, l2)
        l3_by_record = self._validate_l3(l1_by_chunk, l3)
        l4_by_record = self._validate_l4(l1, l2_by_record, l3_by_record, l4)
        l5_by_record = self._validate_l5(l2_by_record, l3_by_record, l4_by_record, l5)
        self._validate_l6(
            set(snapshot_ids),
            l2_by_record,
            l3_by_record,
            l4_by_record,
            l5_by_record,
            l6,
        )
        self._validate_manifest_counts(
            manifest,
            snapshot_count=len(snapshot_ids),
            discovery_count=sum(len(rows) for rows in discoveries_by_snapshot.values()),
            logical_article_count=self.counts["logical_articles"],
            l0=l0,
            l1=l1,
            l2=l2,
            l3=l3,
            l4=l4,
            l5=l5,
            l6=l6,
        )

        issue_total = sum(self.issues.values())
        return {
            "schema_version": "0.1",
            "tool": "validate_layered_corpus",
            "status": "valid" if not issue_total else "invalid",
            "summary": {**self.counts, "issue_count": issue_total},
            "issues": [{"code": code, "count": count} for code, count in sorted(self.issues.items())],
            "privacy": "aggregate_counts_and_codes_only",
        }

    def _validate_snapshot(self, snapshot: dict[str, Any], path: Path, rows: list[dict[str, Any]]) -> None:
        if snapshot.get("status") != "frozen":
            self.issue("SNAPSHOT_NOT_FROZEN")
        selected = snapshot.get("counts", {}).get("selected")
        if selected != len(rows):
            self.issue("SNAPSHOT_SELECTED_COUNT_MISMATCH")
        if selected != snapshot.get("selection", {}).get("target_count"):
            self.issue("SNAPSHOT_TARGET_NOT_MET")
        expected_hash = snapshot.get("input_manifest_sha256")
        if expected_hash != sha256_bytes(path.read_bytes()):
            self.issue("SNAPSHOT_DISCOVERY_HASH_MISMATCH")
        record_ids = [row.get("record_id") for row in rows]
        if len(record_ids) != len(set(record_ids)):
            self.issue("DUPLICATE_DISCOVERY_RECORD_ID")
        article_ids = [row.get("article_id") for row in rows]
        if len(article_ids) != len(set(article_ids)):
            self.issue("DUPLICATE_DISCOVERY_ARTICLE_ID")
        dates = [row.get("published_date_local") or "" for row in rows]
        if dates != sorted(dates, reverse=True):
            self.issue("DISCOVERY_ORDER_MISMATCH")
        if any(row.get("instructions_treated_as_data") is not True for row in rows):
            self.issue("DISCOVERY_INSTRUCTION_BOUNDARY_MISSING")

    def _validate_l0(
        self,
        discoveries_by_snapshot: dict[str, list[dict[str, Any]]],
        rows: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], set[str]]:
        valid_snapshot_ids = set(discoveries_by_snapshot)
        discovery_ids_by_snapshot = {
            snapshot_id: {row.get("article_id") for row in discovery}
            for snapshot_id, discovery in discoveries_by_snapshot.items()
        }
        discovery_ids = set().union(*discovery_ids_by_snapshot.values())
        by_source: dict[str, dict[str, Any]] = {}
        versions_by_article: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            source_id = row.get("source_version_id")
            article_id = row.get("article_id")
            if not isinstance(source_id, str) or source_id in by_source:
                self.issue("DUPLICATE_OR_INVALID_L0_SOURCE_VERSION")
                continue
            by_source[source_id] = row
            if not isinstance(article_id, str) or not article_id:
                self.issue("INVALID_L0_ARTICLE_ID")
            else:
                versions_by_article.setdefault(article_id, []).append(row)
            if article_id not in discovery_ids:
                self.issue("L0_ARTICLE_NOT_IN_DISCOVERY")
            linked_snapshot_ids = string_set(row.get("snapshot_ids"))
            known_links = linked_snapshot_ids & valid_snapshot_ids
            if not known_links or linked_snapshot_ids - valid_snapshot_ids:
                self.issue("L0_SNAPSHOT_LINK_MISSING")
            elif article_id not in set().union(
                *(discovery_ids_by_snapshot[snapshot_id] for snapshot_id in known_links)
            ):
                self.issue("L0_SNAPSHOT_DISCOVERY_LINK_MISMATCH")
            raw = row.get("raw_object", {})
            relative = raw.get("relative_path")
            if not isinstance(relative, str):
                self.issue("L0_RAW_PATH_MISSING")
                continue
            try:
                object_path = safe_child(self.root, relative)
            except ValueError:
                self.issue("L0_RAW_PATH_ESCAPE")
                continue
            if not object_path.is_file():
                self.issue("L0_RAW_OBJECT_MISSING")
                continue
            payload = object_path.read_bytes()
            if sha256_bytes(payload) != raw.get("sha256"):
                self.issue("L0_RAW_HASH_MISMATCH")
            if len(payload) != raw.get("bytes"):
                self.issue("L0_RAW_BYTE_COUNT_MISMATCH")
        self._validate_l0_version_lineage(by_source, versions_by_article)
        return by_source, set(versions_by_article)

    def _validate_l0_version_lineage(
        self,
        by_source: dict[str, dict[str, Any]],
        versions_by_article: dict[str, list[dict[str, Any]]],
    ) -> None:
        """Require each logical article's immutable source versions to form one chain."""
        for article_id, versions in versions_by_article.items():
            version_ids = {
                row.get("source_version_id")
                for row in versions
                if isinstance(row.get("source_version_id"), str)
            }
            roots = 0
            predecessor_counts: Counter[str] = Counter()
            parent_by_version: dict[str, str | None] = {}
            lineage_invalid = False
            for row in versions:
                source_id = row.get("source_version_id")
                if not isinstance(source_id, str):
                    continue
                predecessor = row.get("supersedes_source_version_id")
                parent_by_version[source_id] = predecessor if isinstance(predecessor, str) else None
                if predecessor is None:
                    roots += 1
                    continue
                predecessor_counts[predecessor] += 1
                parent = by_source.get(predecessor)
                if (
                    predecessor == source_id
                    or predecessor not in version_ids
                    or parent is None
                    or parent.get("article_id") != article_id
                ):
                    lineage_invalid = True

            if roots != 1 or any(count > 1 for count in predecessor_counts.values()):
                lineage_invalid = True

            # Following predecessor links from every node must always reach the one root.
            for start in parent_by_version:
                seen: set[str] = set()
                cursor: str | None = start
                while cursor is not None:
                    if cursor in seen or cursor not in parent_by_version:
                        lineage_invalid = True
                        break
                    seen.add(cursor)
                    cursor = parent_by_version[cursor]

            if lineage_invalid:
                self.issue("L0_ARTICLE_VERSION_LINEAGE_INVALID")

    def _validate_l1(
        self, l0_by_source: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        by_chunk: dict[str, dict[str, Any]] = {}
        text_cache: dict[str, tuple[str, int]] = {}
        for row in rows:
            chunk_id = row.get("chunk_id")
            source_id = row.get("source_version_id")
            if not isinstance(chunk_id, str) or chunk_id in by_chunk:
                self.issue("DUPLICATE_OR_INVALID_L1_CHUNK")
                continue
            by_chunk[chunk_id] = row
            if source_id not in l0_by_source:
                self.issue("L1_SOURCE_VERSION_MISSING")
                continue
            locator = row.get("locator", {})
            if locator.get("source_version_id") != source_id or locator.get("chunk_id") != chunk_id:
                self.issue("L1_LOCATOR_ID_MISMATCH")
            text_object = row.get("text_object", {})
            relative = text_object.get("relative_path")
            if not isinstance(relative, str):
                self.issue("L1_TEXT_PATH_MISSING")
                continue
            try:
                text_path = safe_child(self.root, relative)
            except ValueError:
                self.issue("L1_TEXT_PATH_ESCAPE")
                continue
            if not text_path.is_file():
                self.issue("L1_TEXT_OBJECT_MISSING")
                continue
            if relative not in text_cache:
                text = text_path.read_text(encoding="utf-8")
                text_cache[relative] = (sha256_bytes(text.encode("utf-8")), len(text))
            digest, chars = text_cache[relative]
            if digest != text_object.get("sha256"):
                self.issue("L1_TEXT_HASH_MISMATCH")
            if chars != text_object.get("character_count"):
                self.issue("L1_TEXT_CHAR_COUNT_MISMATCH")
            start, end = locator.get("char_start"), locator.get("char_end")
            if not isinstance(start, int) or not isinstance(end, int) or start < 0 or end < start or end > chars:
                self.issue("L1_LOCATOR_RANGE_INVALID")
        return by_chunk

    def _validate_l2(
        self,
        l0_by_source: dict[str, dict[str, Any]],
        l0_article_ids: set[str],
        l1_by_chunk: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_record: dict[str, dict[str, Any]] = {}
        for row in rows:
            record_id = row.get("record_id")
            if not isinstance(record_id, str) or not record_id or record_id in by_record:
                self.issue("DUPLICATE_OR_INVALID_L2_RECORD")
            else:
                by_record[record_id] = row
            source_id, article_id = row.get("source_version_id"), row.get("article_id")
            if source_id not in l0_by_source or article_id not in l0_article_ids:
                self.issue("L2_PARENT_L0_MISSING")
            elif l0_by_source[source_id].get("article_id") != article_id:
                self.issue("L2_ARTICLE_SOURCE_MISMATCH")
            for claim in row.get("claim_nodes", []):
                if claim.get("evidence_status") not in {"reported", "unsourced"}:
                    self.issue("L2_EVIDENCE_PREMATURELY_PROMOTED")
                for locator in claim.get("locator_refs", []):
                    chunk_id = locator.get("chunk_id")
                    indexed = l1_by_chunk.get(chunk_id)
                    if not indexed:
                        self.issue("L2_LOCATOR_CHUNK_MISSING")
                    elif indexed.get("source_version_id") != source_id:
                        self.issue("L2_LOCATOR_SOURCE_MISMATCH")
        return by_record

    @staticmethod
    def _source_ref_locators(source_ref: Any) -> list[dict[str, Any]]:
        if not isinstance(source_ref, dict):
            return []
        locators: list[dict[str, Any]] = []
        if isinstance(source_ref.get("chunk_id"), str):
            locators.append(source_ref)
        nested = source_ref.get("locator")
        if isinstance(nested, dict):
            locators.append(nested)
        nested_many = source_ref.get("locator_refs")
        if isinstance(nested_many, list):
            locators.extend(item for item in nested_many if isinstance(item, dict))
        return locators

    def _validate_l3(
        self, l1_by_chunk: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        by_record = record_index(rows, "record_id", "DUPLICATE_OR_INVALID_L3_RECORD", self.issue)
        for row in rows:
            source_refs = row.get("source_refs")
            if not isinstance(source_refs, list) or not source_refs:
                self.issue("L3_SOURCE_REF_LOCATOR_MISSING")
            else:
                for source_ref in source_refs:
                    locators = self._source_ref_locators(source_ref)
                    if not locators:
                        self.issue("L3_SOURCE_REF_LOCATOR_MISSING")
                    for locator in locators:
                        chunk_id = locator.get("chunk_id")
                        indexed = l1_by_chunk.get(chunk_id)
                        if indexed is None:
                            self.issue("L3_SOURCE_REF_LOCATOR_MISSING")
                            continue
                        expected = indexed.get("locator")
                        if not isinstance(expected, dict):
                            self.issue("L3_SOURCE_REF_LOCATOR_MISMATCH")
                            continue
                        required = (
                            "source_version_id",
                            "chunk_id",
                            "section_path",
                            "paragraph_index",
                            "char_start",
                            "char_end",
                        )
                        if any(locator.get(key) != expected.get(key) for key in required):
                            self.issue("L3_SOURCE_REF_LOCATOR_MISMATCH")
                        if isinstance(source_ref, dict):
                            if source_ref.get("source_version_id") not in {None, indexed.get("source_version_id")}:
                                self.issue("L3_SOURCE_REF_LOCATOR_MISMATCH")
                            if source_ref.get("article_id") not in {None, indexed.get("article_id")}:
                                self.issue("L3_SOURCE_REF_LOCATOR_MISMATCH")

            epistemic = row.get("epistemic")
            epistemic = epistemic if isinstance(epistemic, dict) else {}
            is_reported = (
                row.get("card_type") == "reported_statement"
                or epistemic.get("fact_inference_boundary") == "reported_statement"
            )
            if is_reported and epistemic.get("status") not in {"reported", "disputed"}:
                self.issue("L3_REPORTED_STATEMENT_PREMATURELY_VERIFIED")
            verification = row.get("verification")
            verification = verification if isinstance(verification, dict) else {}
            gate = verification.get("required_before_evidence_use", row.get("required_before_evidence_use"))
            if row.get("card_type") == "reported_statement" and gate is not True:
                self.issue("L3_REPORTED_STATEMENT_EVIDENCE_GATE_MISSING")

            if row.get("card_type") == "fact_evidence":
                independent = row.get("independent_source_ids")
                if independent is None and isinstance(row.get("provenance"), dict):
                    independent = row["provenance"].get("independent_source_ids")
                if not string_set(independent):
                    self.issue("L3_FACT_EVIDENCE_INDEPENDENT_SOURCE_REQUIRED")
        return by_record

    def _validate_l4(
        self,
        l1: list[dict[str, Any]],
        l2_by_record: dict[str, dict[str, Any]],
        l3_by_record: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_record = record_index(rows, "record_id", "DUPLICATE_OR_INVALID_L4_RECORD", self.issue)
        source_text_hashes: dict[str, set[str]] = {}
        for row in l1:
            source_id = row.get("source_version_id")
            digest = row.get("text_object", {}).get("sha256") if isinstance(row.get("text_object"), dict) else None
            if isinstance(source_id, str) and isinstance(digest, str):
                source_text_hashes.setdefault(source_id, set()).add(digest)

        for row in rows:
            duplicate_buckets: dict[str, int] = {}
            memberships = row.get("memberships")
            if not isinstance(memberships, list):
                memberships = []
            for membership in memberships:
                if not isinstance(membership, dict):
                    self.issue("L4_MEMBERSHIP_TARGET_MISSING")
                    continue
                member_id = membership.get("member_id")
                member_type = membership.get("member_type") or membership.get("type")
                if member_type == "article_map":
                    target = l2_by_record.get(member_id)
                    if target is None:
                        self.issue("L4_MEMBERSHIP_TARGET_MISSING")
                        continue
                    hashes = source_text_hashes.get(target.get("source_version_id"), set())
                    if len(hashes) == 1:
                        digest = next(iter(hashes))
                        score = membership.get("score")
                        if score is None:
                            continue
                        if (
                            not isinstance(score, (int, float))
                            or isinstance(score, bool)
                            or score < 0
                            or score > 1
                        ):
                            self.issue("L4_MEMBERSHIP_SCORE_INVALID")
                        elif score > 0:
                            duplicate_buckets[digest] = duplicate_buckets.get(digest, 0) + 1
                elif member_type == "knowledge_card":
                    if member_id not in l3_by_record:
                        self.issue("L4_MEMBERSHIP_TARGET_MISSING")
                else:
                    self.issue("L4_MEMBERSHIP_TYPE_INVALID")
            if any(count > 1 for count in duplicate_buckets.values()):
                # Membership score is the effective weight. A duplicate can retain
                # lineage with score=0, but only one publication of identical clean
                # text may carry positive weight inside a cluster.
                self.issue("L4_DUPLICATE_CONTENT_DOUBLE_WEIGHT")
        return by_record

    def _validate_l5(
        self,
        l2_by_record: dict[str, dict[str, Any]],
        l3_by_record: dict[str, dict[str, Any]],
        l4_by_record: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_record = record_index(rows, "record_id", "DUPLICATE_OR_INVALID_L5_RECORD", self.issue)
        known_authorizations = {
            value
            for layer in (l2_by_record.values(), l3_by_record.values())
            for row in layer
            for value in (row.get("rights_ref"),)
            if isinstance(value, str) and value
        }
        for row in rows:
            cluster_ids = row.get("candidate_cluster_ids")
            if not isinstance(cluster_ids, list):
                cluster_ids = []
            for cluster_id in cluster_ids:
                if cluster_id not in l4_by_record:
                    self.issue("L5_CLUSTER_ID_MISSING")

            authorization_id = row.get("authorization_id")
            if not isinstance(authorization_id, str) or not authorization_id:
                self.issue("L5_AUTHORIZATION_ID_MISSING")
            elif authorization_id not in known_authorizations:
                self.issue("L5_AUTHORIZATION_ID_UNKNOWN")
            else:
                for cluster_id in cluster_ids:
                    cluster = l4_by_record.get(cluster_id)
                    if cluster is None:
                        continue
                    rights: set[str] = set()
                    for membership in cluster.get("memberships", []):
                        if not isinstance(membership, dict):
                            continue
                        member_id = membership.get("member_id")
                        member_type = membership.get("member_type") or membership.get("type")
                        target = l2_by_record.get(member_id) if member_type == "article_map" else l3_by_record.get(member_id)
                        if target and isinstance(target.get("rights_ref"), str):
                            rights.add(target["rights_ref"])
                    if rights and rights != {authorization_id}:
                        self.issue("L5_AUTHORIZATION_SCOPE_MISMATCH")

            priority = row.get("source_priority")
            if not isinstance(priority, list) or not all(isinstance(item, str) and item for item in priority):
                self.issue("L5_SOURCE_PRIORITY_INVALID")
                continue
            if len(priority) != len(set(priority)):
                self.issue("L5_SOURCE_PRIORITY_INVALID")
            if "reported_statement" in priority and "fact_evidence" in priority:
                if priority.index("reported_statement") < priority.index("fact_evidence"):
                    self.issue("L5_REPORTED_SOURCE_PRIORITY_TOO_HIGH")
            evidence_markers = (
                "authoritative_primary",
                "project_record",
                "entity_disclosure",
                "independent_corroboration",
                "fact_evidence",
                "verified",
            )
            lead_markers = ("private_knowledge_lead", "reported_statement", "discovery_hit")
            evidence_indexes = [
                index
                for index, label in enumerate(priority)
                if any(marker in label.casefold() for marker in evidence_markers)
            ]
            lead_indexes = [
                index
                for index, label in enumerate(priority)
                if any(marker in label.casefold() for marker in lead_markers)
            ]
            if evidence_indexes and lead_indexes and min(lead_indexes) < max(evidence_indexes):
                self.issue("L5_EVIDENCE_PRIORITY_BOUNDARY")
            allowed = string_set(row.get("allowed_card_types"))
            requirement = str(row.get("evidence_requirement") or "").casefold()
            if "verified" in requirement and "reported_statement" in allowed:
                self.issue("L5_EVIDENCE_PRIORITY_BOUNDARY")
        return by_record

    def _validate_l6(
        self,
        snapshot_ids: set[str],
        l2_by_record: dict[str, dict[str, Any]],
        l3_by_record: dict[str, dict[str, Any]],
        l4_by_record: dict[str, dict[str, Any]],
        l5_by_record: dict[str, dict[str, Any]],
        rows: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        by_record = record_index(rows, "record_id", "DUPLICATE_OR_INVALID_L6_RECORD", self.issue)
        includable = set(l2_by_record) | set(l3_by_record) | set(l4_by_record) | set(l5_by_record)
        provenance_targets = {
            "snapshot_ids": snapshot_ids,
            "route_ids": set(l5_by_record),
            "cluster_ids": set(l4_by_record),
            "card_ids": set(l3_by_record),
        }
        for row in rows:
            included = row.get("included_record_ids")
            if not isinstance(included, list):
                included = []
            if any(record_id not in includable for record_id in included):
                self.issue("L6_INCLUDED_RECORD_ID_MISSING")

            provenance = row.get("build_provenance")
            if not isinstance(provenance, dict):
                self.issue("L6_BUILD_PROVENANCE_REFERENCE_MISSING")
            else:
                for key, targets in provenance_targets.items():
                    values = provenance.get(key)
                    if not isinstance(values, list) or any(value not in targets for value in values):
                        self.issue("L6_BUILD_PROVENANCE_REFERENCE_MISSING")
                if not isinstance(provenance.get("build_run_id"), str) or not provenance.get("build_run_id"):
                    self.issue("L6_BUILD_PROVENANCE_REFERENCE_MISSING")

            approval = row.get("human_approval")
            approval = approval if isinstance(approval, dict) else {}
            if approval.get("status") == "pending" and row.get("evidence_status") != "prior_only":
                self.issue("L6_PENDING_APPROVAL_NOT_PRIOR_ONLY")
        return by_record

    def _validate_manifest_counts(
        self,
        manifest: dict[str, Any],
        snapshot_count: int,
        discovery_count: int,
        logical_article_count: int,
        l0: list[dict[str, Any]],
        l1: list[dict[str, Any]],
        l2: list[dict[str, Any]],
        l3: list[dict[str, Any]],
        l4: list[dict[str, Any]],
        l5: list[dict[str, Any]],
        l6: list[dict[str, Any]],
    ) -> None:
        expected = manifest.get("record_counts", {})
        layers = (
            ("snapshots", snapshot_count),
            ("discovery_index", discovery_count),
            ("logical_articles", logical_article_count),
            ("l0_raw", len(l0)),
            ("l1_index", len(l1)),
            ("l2_article_maps", len(l2)),
            ("l3_cards", len(l3)),
            ("l4_clusters", len(l4)),
            ("l5_routes", len(l5)),
            ("l6_prior_packs", len(l6)),
        )
        for key, actual in layers:
            declared = expected.get(key)
            if declared is None:
                continue
            if declared != actual:
                self.issue("CORPUS_MANIFEST_COUNT_MISMATCH")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus_root", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = Validator(args.corpus_root).validate()
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 0 if report["status"] == "valid" else 1
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print(json.dumps({"status": "error", "error_code": "INPUT_OR_FORMAT_ERROR"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
