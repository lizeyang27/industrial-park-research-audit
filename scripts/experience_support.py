#!/usr/bin/env python3
"""Build and validate a private experience-provenance sidecar.

The sidecar is keyed to an existing PriorQuestionManifest.  It records where
each selected review prompt came from without treating that origin trace as
proof that the underlying proposition is true.  Standard CLI output is
metadata-only: card text, source identifiers, locators, filenames, and paths
are never printed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

import knowledge_base
import prior_trace


SUPPORT_BUNDLE_VERSION = "0.1"
SUPPORT_SELECTION_POLICY_VERSION = "prior-question-experience-support-v0.1"
SUPPORT_CHARACTER_METRIC = "canonical-json-unicode-code-points-v1"

DEFAULT_MAX_SOURCE_REFS_PER_ITEM = 4
DEFAULT_MAX_LOCATOR_REFS_PER_ITEM = 4
DEFAULT_MAX_TRANSFORMATIONS_PER_ITEM = 4
DEFAULT_MAX_ITEM_CHARS = 2048
DEFAULT_MAX_BUNDLE_CHARS = 16384

HARD_MAX_SOURCE_REFS_PER_ITEM = DEFAULT_MAX_SOURCE_REFS_PER_ITEM
HARD_MAX_LOCATOR_REFS_PER_ITEM = DEFAULT_MAX_LOCATOR_REFS_PER_ITEM
HARD_MAX_TRANSFORMATIONS_PER_ITEM = DEFAULT_MAX_TRANSFORMATIONS_PER_ITEM
HARD_MAX_ITEM_CHARS = DEFAULT_MAX_ITEM_CHARS
HARD_MAX_BUNDLE_CHARS = DEFAULT_MAX_BUNDLE_CHARS

SOURCE_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,127}$")
SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
BUNDLE_ID_RE = re.compile(r"^ESB-[A-F0-9]{20}$")
ITEM_ID_RE = re.compile(r"^ESI-[A-F0-9]{20}$")

SOURCE_SENSITIVITIES = {"public", "restricted", "private"}
SOURCE_ASSET_VERIFICATION_STATUSES = {
    "manifest_declared_not_verified",
    "byte_hash_verified",
}
SOURCE_ASSET_VERIFICATION_FIELDS = {
    "status",
    "declared_source_count",
    "byte_verified_source_count",
    "hash_algorithm",
}
SUPPORT_LEVELS = {
    "origin_trace_only",
    "origin_trace_with_declared_independent_leads",
}
INDEPENDENCE_STATUSES = {"none_declared", "declared_not_verified"}
BUDGET_CLOSURE_CODES = {
    "SUPPORT_BUDGET_WITHIN_LIMIT",
    "SUPPORT_BUDGET_TRUNCATED_TO_LIMIT",
}
BUDGET_ISSUE_CODES = {
    "SUPPORT_SOURCE_REF_LIMIT_OMITTED",
    "SUPPORT_LOCATOR_REF_LIMIT_OMITTED",
    "SUPPORT_TRANSFORMATION_LIMIT_OMITTED",
    "SUPPORT_ITEM_CHAR_LIMIT_OMITTED",
    "SUPPORT_BUNDLE_CHAR_LIMIT_OMITTED",
}

BUNDLE_FIELDS = {
    "schema_version",
    "bundle_id",
    "run_id",
    "prior_manifest_id",
    "prior_manifest_hash",
    "task_envelope_hash",
    "knowledge_snapshot_hash",
    "source_manifest_snapshot_hash",
    "authorization_snapshot_hash",
    "authorization_ref",
    "processor_class",
    "selection_policy_version",
    "created_at",
    "source_asset_verification",
    "support_items",
    "coverage",
    "character_budget",
    "budget_closure_code",
    "budget_issues",
    "privacy",
    "bundle_hash",
}

SUPPORT_ITEM_FIELDS = {
    "support_item_id",
    "prior_question_id",
    "knowledge_card_id",
    "knowledge_card_hash",
    "card_type",
    "sensitivity",
    "origin_type",
    "origin_trace_status",
    "source_asset_verification_status",
    "locator_verification_status",
    "origin_source_refs",
    "origin_locator_refs",
    "transformation_chain",
    "independent_source_refs",
    "support_level",
    "independence_status",
    "declared_epistemic",
    "epistemic_ceiling",
    "truth_status",
    "fact_use_requires_separate_verification",
    "human_review_required",
    "knowledge_role",
    "eligible_as_fact_evidence",
}

SOURCE_REF_FIELDS = {"source_id", "source_type", "declared_source_sha256"}
DECLARED_EPISTEMIC_FIELDS = {"status", "fact_inference_boundary", "confidence"}
PRIVACY_FIELDS = {
    "private_review_only",
    "source_bodies_not_included",
    "source_paths_omitted",
    "stdout_metadata_only",
    "origin_trace_is_not_truth_evidence",
}
FORBIDDEN_BUNDLE_KEYS = {
    "absolute_path",
    "relative_path",
    "source_path",
    "source_text",
    "body_text",
    "claim_or_guidance",
    "rationale",
    "recommended_action",
    "verbatim",
}


class ExperienceSupportError(RuntimeError):
    """A support bundle could not be built or validated safely."""


def _safe_identifier(value: Any, prefix: str, line: int) -> str:
    if isinstance(value, str) and SOURCE_ID_RE.fullmatch(value):
        return value
    return f"{prefix}-LINE-{line}"


def _is_nonempty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(
        isinstance(item, str) and bool(item.strip()) for item in value
    )


def _walk_keys(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _is_safe_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", normalized) or normalized.startswith("/"):
        return False
    parts = PurePosixPath(normalized).parts
    return bool(parts) and ".." not in parts


def load_source_manifest(path: Path) -> list[tuple[int, dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise ExperienceSupportError("SOURCE_MANIFEST_READ_ERROR") from exc

    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ExperienceSupportError(f"SOURCE_MANIFEST_JSON_LINE_{line_number}") from exc
        if not isinstance(value, dict):
            raise ExperienceSupportError(f"SOURCE_MANIFEST_NOT_OBJECT_LINE_{line_number}")
        records.append((line_number, value))
    return records


def validate_source_manifest(records: Iterable[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    seen: set[str] = set()
    required = {
        "source_id",
        "source_type",
        "relative_path",
        "sha256",
        "sensitivity",
        "export_allowed",
    }
    for line, record in records:
        source_id = _safe_identifier(record.get("source_id"), "SOURCE", line)
        codes: list[str] = []
        if set(record) != required:
            codes.append("SOURCE_MANIFEST_REQUIRED_FIELDS")
        raw_id = record.get("source_id")
        if not isinstance(raw_id, str) or not SOURCE_ID_RE.fullmatch(raw_id):
            codes.append("SOURCE_ID")
        elif raw_id in seen:
            codes.append("DUPLICATE_SOURCE_ID")
        else:
            seen.add(raw_id)
        if not isinstance(record.get("source_type"), str) or not record["source_type"].strip():
            codes.append("SOURCE_TYPE")
        if not _is_safe_relative_path(record.get("relative_path")):
            codes.append("SOURCE_RELATIVE_PATH")
        if not isinstance(record.get("sha256"), str) or not SHA256_RE.fullmatch(record["sha256"]):
            codes.append("SOURCE_SHA256")
        sensitivity = record.get("sensitivity")
        if sensitivity not in SOURCE_SENSITIVITIES:
            codes.append("SOURCE_SENSITIVITY")
        if not isinstance(record.get("export_allowed"), bool):
            codes.append("SOURCE_EXPORT_ALLOWED")
        if sensitivity in {"private", "restricted"} and record.get("export_allowed") is not False:
            codes.append("PRIVATE_SOURCE_EXPORT_MUST_BE_FALSE")
        for code in sorted(set(codes)):
            issues.append({"source_id": source_id, "line": line, "code": code})
    return sorted(issues, key=lambda issue: (issue["line"], issue["source_id"], issue["code"]))


def verify_source_assets(
    source_records: Iterable[tuple[int, dict[str, Any]]],
    source_root: Path | None,
) -> dict[str, Any]:
    """Optionally verify every catalogued source byte-for-byte under one root.

    The function returns only aggregate state. Errors are stable codes and do
    not contain the root, relative path, or source identifier.
    """

    records = list(source_records)
    if source_root is None:
        return {
            "status": "manifest_declared_not_verified",
            "declared_source_count": len(records),
            "byte_verified_source_count": 0,
            "hash_algorithm": "sha256",
        }

    try:
        resolved_root = source_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise ExperienceSupportError("SOURCE_ROOT_NOT_DIRECTORY") from exc
    if not resolved_root.is_dir():
        raise ExperienceSupportError("SOURCE_ROOT_NOT_DIRECTORY")

    verified_count = 0
    for _, record in records:
        relative_path = record.get("relative_path")
        if not _is_safe_relative_path(relative_path):
            raise ExperienceSupportError("SOURCE_ASSET_PATH_ESCAPE")
        relative_parts = PurePosixPath(relative_path.replace("\\", "/")).parts
        candidate = resolved_root.joinpath(*relative_parts)
        try:
            resolved_candidate = candidate.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ExperienceSupportError("SOURCE_ASSET_MISSING") from exc
        except OSError as exc:
            raise ExperienceSupportError("SOURCE_ASSET_READ_ERROR") from exc
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise ExperienceSupportError("SOURCE_ASSET_PATH_ESCAPE") from exc
        if not resolved_candidate.is_file():
            raise ExperienceSupportError("SOURCE_ASSET_NOT_FILE")
        try:
            actual_hash = knowledge_base.sha256_file(resolved_candidate)
        except knowledge_base.CardFileError as exc:
            raise ExperienceSupportError("SOURCE_ASSET_READ_ERROR") from exc
        if actual_hash.casefold() != record["sha256"].casefold():
            raise ExperienceSupportError("SOURCE_ASSET_HASH_MISMATCH")
        verified_count += 1

    return {
        "status": "byte_hash_verified",
        "declared_source_count": len(records),
        "byte_verified_source_count": verified_count,
        "hash_algorithm": "sha256",
    }


def validate_card_source_links(
    cards: Iterable[tuple[int, dict[str, Any]]],
    source_records: Iterable[tuple[int, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Validate every card-to-catalog edge, including cards not selected this run."""

    source_index = {record["source_id"]: record for _, record in source_records}
    issues: list[dict[str, Any]] = []
    for line, card in cards:
        card_id = knowledge_base.safe_card_id(card, line)
        provenance = card.get("provenance", {})
        origin_ids = provenance.get("source_ids", [])
        independent_ids = provenance.get("independent_source_ids", [])
        if not origin_ids:
            issues.append({"card_id": card_id, "line": line, "code": "CARD_ORIGIN_SOURCE_REQUIRED"})
            continue
        if set(origin_ids).intersection(independent_ids):
            issues.append({"card_id": card_id, "line": line, "code": "CARD_SOURCE_ROLE_OVERLAP"})
        for source_id in [*origin_ids, *independent_ids]:
            if source_id not in source_index:
                issues.append({"card_id": card_id, "line": line, "code": "CARD_SOURCE_NOT_IN_MANIFEST"})
        for source_id in origin_ids:
            record = source_index.get(source_id)
            if record is not None and record.get("source_type") != provenance.get("origin_type"):
                issues.append({"card_id": card_id, "line": line, "code": "CARD_ORIGIN_TYPE_MISMATCH"})
    return sorted(issues, key=lambda issue: (issue["line"], issue["card_id"], issue["code"]))


def compute_support_item_id(item: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in item.items() if key != "support_item_id"}
    return "ESI-" + knowledge_base.canonical_hash(unsigned)[:20].upper()


def compute_bundle_hash(bundle: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in bundle.items() if key != "bundle_hash"}
    return knowledge_base.canonical_hash(unsigned)


def _bundle_id_seed(bundle: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": bundle["run_id"],
        "prior_manifest_id": bundle["prior_manifest_id"],
        "prior_manifest_hash": bundle["prior_manifest_hash"],
        "task_envelope_hash": bundle["task_envelope_hash"],
        "knowledge_snapshot_hash": bundle["knowledge_snapshot_hash"],
        "source_manifest_snapshot_hash": bundle["source_manifest_snapshot_hash"],
        "authorization_snapshot_hash": bundle["authorization_snapshot_hash"],
        "authorization_ref": bundle["authorization_ref"],
        "processor_class": bundle["processor_class"],
        "selection_policy_version": bundle["selection_policy_version"],
        "created_at": bundle["created_at"],
        "source_asset_verification": bundle["source_asset_verification"],
        "character_budget": bundle["character_budget"],
    }


def compute_bundle_id(bundle: dict[str, Any]) -> str:
    return "ESB-" + knowledge_base.canonical_hash(_bundle_id_seed(bundle))[:20].upper()


def make_character_budget(
    *,
    max_source_refs_per_item: int = DEFAULT_MAX_SOURCE_REFS_PER_ITEM,
    max_locator_refs_per_item: int = DEFAULT_MAX_LOCATOR_REFS_PER_ITEM,
    max_transformations_per_item: int = DEFAULT_MAX_TRANSFORMATIONS_PER_ITEM,
    max_item_chars: int = DEFAULT_MAX_ITEM_CHARS,
    max_bundle_chars: int = DEFAULT_MAX_BUNDLE_CHARS,
) -> dict[str, Any]:
    values = {
        "max_source_refs_per_item": max_source_refs_per_item,
        "max_locator_refs_per_item": max_locator_refs_per_item,
        "max_transformations_per_item": max_transformations_per_item,
        "max_item_chars": max_item_chars,
        "max_bundle_chars": max_bundle_chars,
    }
    hard_limits = {
        "max_source_refs_per_item": HARD_MAX_SOURCE_REFS_PER_ITEM,
        "max_locator_refs_per_item": HARD_MAX_LOCATOR_REFS_PER_ITEM,
        "max_transformations_per_item": HARD_MAX_TRANSFORMATIONS_PER_ITEM,
        "max_item_chars": HARD_MAX_ITEM_CHARS,
        "max_bundle_chars": HARD_MAX_BUNDLE_CHARS,
    }
    if any(type(value) is not int or value < 1 for value in values.values()):
        raise ExperienceSupportError("SUPPORT_BUDGET_NOT_POSITIVE")
    if any(values[key] > hard_limits[key] for key in values):
        raise ExperienceSupportError("SUPPORT_BUDGET_ABOVE_HARD_LIMIT")
    return {"metric": SUPPORT_CHARACTER_METRIC, **values}


def _safe_source_ref(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": record["source_id"],
        "source_type": record["source_type"],
        "declared_source_sha256": record["sha256"].lower(),
    }


def support_item_from_card(
    card: dict[str, Any],
    question: dict[str, Any],
    source_index: dict[str, dict[str, Any]],
    *,
    source_asset_verification_status: str,
) -> dict[str, Any]:
    provenance = card["provenance"]
    origin_ids = provenance["source_ids"]
    if not origin_ids:
        raise ExperienceSupportError("SUPPORT_ORIGIN_SOURCE_REQUIRED")
    missing = [source_id for source_id in origin_ids if source_id not in source_index]
    missing += [
        source_id
        for source_id in provenance["independent_source_ids"]
        if source_id not in source_index
    ]
    if missing:
        raise ExperienceSupportError("SUPPORT_SOURCE_NOT_IN_MANIFEST")

    origin_records = [source_index[source_id] for source_id in origin_ids]
    if any(record["source_type"] != provenance["origin_type"] for record in origin_records):
        raise ExperienceSupportError("SUPPORT_ORIGIN_TYPE_MISMATCH")

    independent_records = [
        source_index[source_id] for source_id in provenance["independent_source_ids"]
    ]
    independent_ids = set(provenance["independent_source_ids"])
    if independent_ids.intersection(origin_ids):
        raise ExperienceSupportError("SUPPORT_SOURCE_ROLE_OVERLAP")

    has_independent_leads = bool(independent_records)
    item: dict[str, Any] = {
        "prior_question_id": question["prior_question_id"],
        "knowledge_card_id": card["card_id"],
        "knowledge_card_hash": knowledge_base.canonical_hash(card),
        "card_type": card["card_type"],
        "sensitivity": card["rights_and_access"]["sensitivity"],
        "origin_type": provenance["origin_type"],
        "origin_trace_status": source_asset_verification_status,
        "source_asset_verification_status": source_asset_verification_status,
        "locator_verification_status": "declared_not_reproduced",
        "origin_source_refs": [_safe_source_ref(record) for record in origin_records],
        "origin_locator_refs": list(provenance["locators"]),
        "transformation_chain": list(provenance["transformation"]),
        "independent_source_refs": [
            _safe_source_ref(record) for record in independent_records
        ],
        "support_level": (
            "origin_trace_with_declared_independent_leads"
            if has_independent_leads
            else "origin_trace_only"
        ),
        "independence_status": (
            "declared_not_verified" if has_independent_leads else "none_declared"
        ),
        "declared_epistemic": {
            "status": card["epistemic"]["status"],
            "fact_inference_boundary": card["epistemic"]["fact_inference_boundary"],
            "confidence": card["epistemic"]["confidence"],
        },
        "epistemic_ceiling": "review_prompt_only",
        "truth_status": "not_established_by_origin_trace",
        "fact_use_requires_separate_verification": True,
        "human_review_required": True,
        "knowledge_role": "experience_support_metadata",
        "eligible_as_fact_evidence": False,
    }
    item["support_item_id"] = compute_support_item_id(item)
    return item


def _item_budget_issue(item: dict[str, Any], budget: dict[str, Any]) -> str | None:
    source_count = len(item["origin_source_refs"]) + len(item["independent_source_refs"])
    if source_count > budget["max_source_refs_per_item"]:
        return "SUPPORT_SOURCE_REF_LIMIT_OMITTED"
    if len(item["origin_locator_refs"]) > budget["max_locator_refs_per_item"]:
        return "SUPPORT_LOCATOR_REF_LIMIT_OMITTED"
    if len(item["transformation_chain"]) > budget["max_transformations_per_item"]:
        return "SUPPORT_TRANSFORMATION_LIMIT_OMITTED"
    if knowledge_base.canonical_char_count(item) > budget["max_item_chars"]:
        return "SUPPORT_ITEM_CHAR_LIMIT_OMITTED"
    return None


def build_experience_support_bundle(
    cards: list[tuple[int, dict[str, Any]]],
    source_records: list[tuple[int, dict[str, Any]]],
    prior_manifest: dict[str, Any],
    envelope: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    knowledge_snapshot_hash: str,
    source_manifest_snapshot_hash: str,
    authorization_snapshot_hash: str,
    created_at: datetime | None = None,
    source_root: Path | None = None,
    max_source_refs_per_item: int = DEFAULT_MAX_SOURCE_REFS_PER_ITEM,
    max_locator_refs_per_item: int = DEFAULT_MAX_LOCATOR_REFS_PER_ITEM,
    max_transformations_per_item: int = DEFAULT_MAX_TRANSFORMATIONS_PER_ITEM,
    max_item_chars: int = DEFAULT_MAX_ITEM_CHARS,
    max_bundle_chars: int = DEFAULT_MAX_BUNDLE_CHARS,
) -> dict[str, Any]:
    for digest in (
        knowledge_snapshot_hash,
        source_manifest_snapshot_hash,
        authorization_snapshot_hash,
    ):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ExperienceSupportError("SUPPORT_SNAPSHOT_HASH_INVALID")
    card_issues = knowledge_base.validate_cards(cards)
    if card_issues:
        raise ExperienceSupportError("KNOWLEDGE_CARDS_INVALID")
    source_issues = validate_source_manifest(source_records)
    if source_issues:
        if source_root is not None and any(
            issue["code"] == "SOURCE_RELATIVE_PATH" for issue in source_issues
        ):
            raise ExperienceSupportError("SOURCE_ASSET_PATH_ESCAPE")
        raise ExperienceSupportError("SOURCE_MANIFEST_INVALID")
    if validate_card_source_links(cards, source_records):
        raise ExperienceSupportError("KNOWLEDGE_SOURCE_LINKS_INVALID")
    prior_codes = prior_trace.validate_prior_manifest(prior_manifest)
    if prior_codes:
        raise ExperienceSupportError("PRIOR_MANIFEST_INVALID")
    envelope_codes = knowledge_base.validate_task_envelope(envelope)
    if envelope_codes:
        raise ExperienceSupportError("TASK_ENVELOPE_INVALID")

    if prior_manifest.get("schema_version") != knowledge_base.PRIOR_MANIFEST_VERSION:
        raise ExperienceSupportError("PRIOR_MANIFEST_VERSION_UNSUPPORTED")
    if prior_manifest["task_envelope_hash"] != knowledge_base.canonical_hash(envelope):
        raise ExperienceSupportError("TASK_ENVELOPE_HASH_MISMATCH")
    if prior_manifest["knowledge_snapshot_hash"] != knowledge_snapshot_hash:
        raise ExperienceSupportError("KNOWLEDGE_SNAPSHOT_MISMATCH")
    if prior_manifest["authorization_snapshot_hash"] != authorization_snapshot_hash:
        raise ExperienceSupportError("AUTHORIZATION_SNAPSHOT_MISMATCH")
    if prior_manifest["run_id"] != envelope["run_id"]:
        raise ExperienceSupportError("RUN_ID_MISMATCH")

    budget = make_character_budget(
        max_source_refs_per_item=max_source_refs_per_item,
        max_locator_refs_per_item=max_locator_refs_per_item,
        max_transformations_per_item=max_transformations_per_item,
        max_item_chars=max_item_chars,
        max_bundle_chars=max_bundle_chars,
    )
    bundle_created_at = created_at or datetime.now(timezone.utc)
    if bundle_created_at.tzinfo is None or bundle_created_at.utcoffset() is None:
        raise ExperienceSupportError("SUPPORT_CREATED_AT_TIMEZONE_REQUIRED")
    if bundle_created_at < knowledge_base.parse_datetime(prior_manifest["created_at"]):
        raise ExperienceSupportError("SUPPORT_CREATED_BEFORE_PRIOR_MANIFEST")

    context = envelope["authorization_context"]
    if prior_manifest["authorization_ref"] != context["authorization_id"]:
        raise ExperienceSupportError("AUTHORIZATION_REF_MISMATCH")
    if context["processor_class"] not in {"local_deterministic", "model_context_abstract"}:
        raise ExperienceSupportError("RAW_OR_EXTERNAL_SUPPORT_PROCESSING_FORBIDDEN")
    if context["output_audience"] != "private_review":
        raise ExperienceSupportError("SUPPORT_OUTPUT_MUST_BE_PRIVATE")
    authorization = authorizations.get(context["authorization_id"])
    if authorization is None:
        raise knowledge_base.AuthorizationError("AUTHORIZATION_NOT_FOUND")
    processing_date = bundle_created_at.date()
    knowledge_base.authorize_context(authorization, context, at_date=processing_date)
    source_asset_verification = verify_source_assets(source_records, source_root)

    card_index = {card["card_id"]: card for _, card in cards}
    source_index = {record["source_id"]: record for _, record in source_records}
    eligible: list[dict[str, Any]] = []
    budget_issue_counts: Counter[str] = Counter()
    omitted: list[dict[str, str]] = []

    for question in prior_manifest["questions"]:
        card_id = question["knowledge_card_id"]
        card = card_index.get(card_id)
        if card is None:
            raise ExperienceSupportError("PRIOR_CARD_NOT_IN_KNOWLEDGE_SNAPSHOT")
        if question["prior_question_id"] != f"PQ-{card_id}":
            raise ExperienceSupportError("PRIOR_QUESTION_CARD_BINDING_INVALID")
        rights = card["rights_and_access"]
        if rights["sensitivity"] != "public":
            knowledge_base.authorize_card_for_prior(
                card,
                authorization,
                context,
                at_date=processing_date,
            )
        item = support_item_from_card(
            card,
            question,
            source_index,
            source_asset_verification_status=source_asset_verification["status"],
        )
        issue_code = _item_budget_issue(item, budget)
        if issue_code is not None:
            budget_issue_counts[issue_code] += 1
            omitted.append({"prior_question_id": question["prior_question_id"], "code": issue_code})
            continue
        eligible.append(item)

    created_iso = bundle_created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    def assemble_bundle() -> dict[str, Any]:
        bundle: dict[str, Any] = {
            "schema_version": SUPPORT_BUNDLE_VERSION,
            "bundle_id": "",
            "run_id": prior_manifest["run_id"],
            "prior_manifest_id": prior_manifest["manifest_id"],
            "prior_manifest_hash": prior_manifest["manifest_hash"],
            "task_envelope_hash": prior_manifest["task_envelope_hash"],
            "knowledge_snapshot_hash": knowledge_snapshot_hash,
            "source_manifest_snapshot_hash": source_manifest_snapshot_hash,
            "authorization_snapshot_hash": authorization_snapshot_hash,
            "authorization_ref": prior_manifest["authorization_ref"],
            "processor_class": context["processor_class"],
            "selection_policy_version": SUPPORT_SELECTION_POLICY_VERSION,
            "created_at": created_iso,
            "source_asset_verification": source_asset_verification,
            "support_items": list(eligible),
            "coverage": {
                "prior_question_count": len(prior_manifest["questions"]),
                "eligible_item_count": len(prior_manifest["questions"]),
                "selected_item_count": len(eligible),
                "omitted_item_count": len(omitted),
                "omissions": list(omitted),
            },
            "character_budget": budget,
            "budget_closure_code": (
                "SUPPORT_BUDGET_TRUNCATED_TO_LIMIT"
                if budget_issue_counts
                else "SUPPORT_BUDGET_WITHIN_LIMIT"
            ),
            "budget_issues": [
                {"code": code, "count": budget_issue_counts[code]}
                for code in sorted(budget_issue_counts)
                if budget_issue_counts[code]
            ],
            "privacy": {
                "private_review_only": True,
                "source_bodies_not_included": True,
                "source_paths_omitted": True,
                "stdout_metadata_only": True,
                "origin_trace_is_not_truth_evidence": True,
            },
        }
        bundle["bundle_id"] = compute_bundle_id(bundle)
        bundle["bundle_hash"] = compute_bundle_hash(bundle)
        return bundle

    bundle = assemble_bundle()
    while knowledge_base.canonical_char_count(bundle) > budget["max_bundle_chars"]:
        if not eligible:
            raise ExperienceSupportError("SUPPORT_BUNDLE_BASE_OVER_BUDGET")
        removed = eligible.pop()
        code = "SUPPORT_BUNDLE_CHAR_LIMIT_OMITTED"
        budget_issue_counts[code] += 1
        omitted.append({"prior_question_id": removed["prior_question_id"], "code": code})
        bundle = assemble_bundle()
    return bundle


def _validate_source_ref(value: Any) -> list[str]:
    codes: list[str] = []
    if not isinstance(value, dict):
        return ["SUPPORT_SOURCE_REF_NOT_OBJECT"]
    if set(value) != SOURCE_REF_FIELDS:
        codes.append("SUPPORT_SOURCE_REF_FIELDS")
    if not isinstance(value.get("source_id"), str) or not SOURCE_ID_RE.fullmatch(value["source_id"]):
        codes.append("SUPPORT_SOURCE_REF_ID")
    if not isinstance(value.get("source_type"), str) or not value["source_type"].strip():
        codes.append("SUPPORT_SOURCE_REF_TYPE")
    digest = value.get("declared_source_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        codes.append("SUPPORT_SOURCE_REF_HASH")
    return codes


def validate_experience_support_bundle(
    bundle: Any,
    prior_manifest: dict[str, Any] | None = None,
) -> list[str]:
    codes: list[str] = []
    if not isinstance(bundle, dict):
        return ["SUPPORT_BUNDLE_NOT_OBJECT"]
    if set(bundle) != BUNDLE_FIELDS:
        codes.append("SUPPORT_BUNDLE_FIELDS")
    if bundle.get("schema_version") != SUPPORT_BUNDLE_VERSION:
        codes.append("SUPPORT_BUNDLE_SCHEMA_VERSION")
    if not isinstance(bundle.get("bundle_id"), str) or not BUNDLE_ID_RE.fullmatch(bundle["bundle_id"]):
        codes.append("SUPPORT_BUNDLE_ID")
    for key in (
        "prior_manifest_hash",
        "task_envelope_hash",
        "knowledge_snapshot_hash",
        "source_manifest_snapshot_hash",
        "authorization_snapshot_hash",
        "bundle_hash",
    ):
        value = bundle.get(key)
        if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
            codes.append(f"SUPPORT_{key.upper()}")
    for key in ("run_id", "authorization_ref"):
        value = bundle.get(key)
        if not isinstance(value, str) or not value.strip():
            codes.append(f"SUPPORT_{key.upper()}")
    prior_manifest_id = bundle.get("prior_manifest_id")
    if (
        not isinstance(prior_manifest_id, str)
        or not re.fullmatch(r"PM-[A-F0-9]{20}", prior_manifest_id)
    ):
        codes.append("SUPPORT_PRIOR_MANIFEST_ID")
    if bundle.get("processor_class") not in {"local_deterministic", "model_context_abstract"}:
        codes.append("SUPPORT_PROCESSOR_CLASS")
    if bundle.get("selection_policy_version") != SUPPORT_SELECTION_POLICY_VERSION:
        codes.append("SUPPORT_SELECTION_POLICY_VERSION")
    try:
        knowledge_base.parse_datetime(bundle.get("created_at"))
    except (TypeError, ValueError):
        codes.append("SUPPORT_CREATED_AT")
    if FORBIDDEN_BUNDLE_KEYS.intersection(_walk_keys(bundle)):
        codes.append("SUPPORT_FORBIDDEN_CONTENT_KEY")

    source_verification = bundle.get("source_asset_verification")
    bundle_source_status: str | None = None
    if not isinstance(source_verification, dict):
        codes.append("SUPPORT_SOURCE_ASSET_VERIFICATION")
    else:
        if set(source_verification) != SOURCE_ASSET_VERIFICATION_FIELDS:
            codes.append("SUPPORT_SOURCE_ASSET_VERIFICATION_FIELDS")
        status = source_verification.get("status")
        if status not in SOURCE_ASSET_VERIFICATION_STATUSES:
            codes.append("SUPPORT_SOURCE_ASSET_VERIFICATION_STATUS")
        else:
            bundle_source_status = status
        declared_count = source_verification.get("declared_source_count")
        verified_count = source_verification.get("byte_verified_source_count")
        if type(declared_count) is not int or declared_count < 0:
            codes.append("SUPPORT_DECLARED_SOURCE_COUNT")
        if type(verified_count) is not int or verified_count < 0:
            codes.append("SUPPORT_BYTE_VERIFIED_SOURCE_COUNT")
        if source_verification.get("hash_algorithm") != "sha256":
            codes.append("SUPPORT_SOURCE_HASH_ALGORITHM")
        if status == "manifest_declared_not_verified" and verified_count != 0:
            codes.append("SUPPORT_SOURCE_VERIFICATION_COUNT_CONTRADICTION")
        if status == "byte_hash_verified" and verified_count != declared_count:
            codes.append("SUPPORT_SOURCE_VERIFICATION_COUNT_CONTRADICTION")

    items = bundle.get("support_items")
    seen_item_ids: set[str] = set()
    seen_question_ids: set[str] = set()
    if not isinstance(items, list):
        codes.append("SUPPORT_ITEMS")
        items = []
    for item in items:
        if not isinstance(item, dict):
            codes.append("SUPPORT_ITEM_NOT_OBJECT")
            continue
        if set(item) != SUPPORT_ITEM_FIELDS:
            codes.append("SUPPORT_ITEM_FIELDS")
        item_id = item.get("support_item_id")
        if not isinstance(item_id, str) or not ITEM_ID_RE.fullmatch(item_id):
            codes.append("SUPPORT_ITEM_ID")
        elif item_id in seen_item_ids:
            codes.append("SUPPORT_ITEM_ID_DUPLICATE")
        else:
            seen_item_ids.add(item_id)
        question_id = item.get("prior_question_id")
        if not isinstance(question_id, str) or not question_id.startswith("PQ-"):
            codes.append("SUPPORT_PRIOR_QUESTION_ID")
        elif question_id in seen_question_ids:
            codes.append("SUPPORT_PRIOR_QUESTION_DUPLICATE")
        else:
            seen_question_ids.add(question_id)
        card_id = item.get("knowledge_card_id")
        if not isinstance(card_id, str) or not knowledge_base.CARD_ID_RE.fullmatch(card_id):
            codes.append("SUPPORT_KNOWLEDGE_CARD_ID")
        elif question_id != f"PQ-{card_id}":
            codes.append("SUPPORT_QUESTION_CARD_BINDING")
        card_hash = item.get("knowledge_card_hash")
        if not isinstance(card_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", card_hash):
            codes.append("SUPPORT_KNOWLEDGE_CARD_HASH")
        if item.get("card_type") not in knowledge_base.PRIOR_CARD_TYPES:
            codes.append("SUPPORT_CARD_TYPE")
        if item.get("sensitivity") not in knowledge_base.SENSITIVITIES:
            codes.append("SUPPORT_SENSITIVITY")
        if not isinstance(item.get("origin_type"), str) or not item["origin_type"].strip():
            codes.append("SUPPORT_ORIGIN_TYPE")
        if item.get("origin_trace_status") not in SOURCE_ASSET_VERIFICATION_STATUSES:
            codes.append("SUPPORT_ORIGIN_TRACE_STATUS")
        if item.get("source_asset_verification_status") not in SOURCE_ASSET_VERIFICATION_STATUSES:
            codes.append("SUPPORT_ITEM_SOURCE_ASSET_VERIFICATION_STATUS")
        if item.get("source_asset_verification_status") != item.get("origin_trace_status"):
            codes.append("SUPPORT_ITEM_SOURCE_STATUS_CONTRADICTION")
        if (
            bundle_source_status is not None
            and item.get("source_asset_verification_status") != bundle_source_status
        ):
            codes.append("SUPPORT_ITEM_BUNDLE_SOURCE_STATUS_MISMATCH")
        if item.get("locator_verification_status") != "declared_not_reproduced":
            codes.append("SUPPORT_LOCATOR_VERIFICATION_STATUS")
        for key in ("origin_source_refs", "independent_source_refs"):
            refs = item.get(key)
            if not isinstance(refs, list):
                codes.append(f"SUPPORT_{key.upper()}")
                continue
            if key == "origin_source_refs" and not refs:
                codes.append("SUPPORT_ORIGIN_SOURCE_REQUIRED")
            for ref in refs:
                codes.extend(_validate_source_ref(ref))
        origin_refs = item.get("origin_source_refs")
        independent_refs = item.get("independent_source_refs")
        if isinstance(origin_refs, list) and isinstance(independent_refs, list):
            origin_ids = [
                ref.get("source_id") for ref in origin_refs if isinstance(ref, dict)
            ]
            independent_ids = [
                ref.get("source_id") for ref in independent_refs if isinstance(ref, dict)
            ]
            if len(origin_ids) != len(set(origin_ids)) or len(independent_ids) != len(set(independent_ids)):
                codes.append("SUPPORT_SOURCE_REF_DUPLICATE")
            if set(origin_ids).intersection(independent_ids):
                codes.append("SUPPORT_SOURCE_ROLE_OVERLAP")
        for key in ("origin_locator_refs", "transformation_chain"):
            if not _is_nonempty_string_list(item.get(key)):
                codes.append(f"SUPPORT_{key.upper()}")
        if item.get("support_level") not in SUPPORT_LEVELS:
            codes.append("SUPPORT_LEVEL")
        if item.get("independence_status") not in INDEPENDENCE_STATUSES:
            codes.append("SUPPORT_INDEPENDENCE_STATUS")
        independent_refs = item.get("independent_source_refs")
        if isinstance(independent_refs, list):
            expected_independence = "declared_not_verified" if independent_refs else "none_declared"
            if item.get("independence_status") != expected_independence:
                codes.append("SUPPORT_INDEPENDENCE_CONTRADICTION")
            expected_level = (
                "origin_trace_with_declared_independent_leads"
                if independent_refs
                else "origin_trace_only"
            )
            if item.get("support_level") != expected_level:
                codes.append("SUPPORT_LEVEL_CONTRADICTION")
        declared = item.get("declared_epistemic")
        if not isinstance(declared, dict) or set(declared) != DECLARED_EPISTEMIC_FIELDS:
            codes.append("SUPPORT_DECLARED_EPISTEMIC")
        else:
            if declared.get("status") not in knowledge_base.EPISTEMIC_STATUSES:
                codes.append("SUPPORT_DECLARED_EPISTEMIC_STATUS")
            if declared.get("fact_inference_boundary") not in knowledge_base.BOUNDARIES:
                codes.append("SUPPORT_DECLARED_EPISTEMIC_BOUNDARY")
            if declared.get("confidence") not in {"low", "medium", "high"}:
                codes.append("SUPPORT_DECLARED_EPISTEMIC_CONFIDENCE")
        if item.get("epistemic_ceiling") != "review_prompt_only":
            codes.append("SUPPORT_EPISTEMIC_CEILING")
        if item.get("truth_status") != "not_established_by_origin_trace":
            codes.append("SUPPORT_TRUTH_STATUS")
        for key in ("fact_use_requires_separate_verification", "human_review_required"):
            if item.get(key) is not True:
                codes.append(f"SUPPORT_{key.upper()}")
        if item.get("knowledge_role") != "experience_support_metadata":
            codes.append("SUPPORT_KNOWLEDGE_ROLE")
        if item.get("eligible_as_fact_evidence") is not False:
            codes.append("SUPPORT_FACT_EVIDENCE_FORBIDDEN")
        if isinstance(item_id, str) and item_id != compute_support_item_id(item):
            codes.append("SUPPORT_ITEM_HASH_MISMATCH")

    coverage = bundle.get("coverage")
    if not isinstance(coverage, dict):
        codes.append("SUPPORT_COVERAGE")
        coverage = {}
    else:
        expected_coverage_fields = {
            "prior_question_count",
            "eligible_item_count",
            "selected_item_count",
            "omitted_item_count",
            "omissions",
        }
        if set(coverage) != expected_coverage_fields:
            codes.append("SUPPORT_COVERAGE_FIELDS")
        for key in (
            "prior_question_count",
            "eligible_item_count",
            "selected_item_count",
            "omitted_item_count",
        ):
            if type(coverage.get(key)) is not int or coverage[key] < 0:
                codes.append(f"SUPPORT_COVERAGE_{key.upper()}")
        omissions = coverage.get("omissions")
        if not isinstance(omissions, list):
            codes.append("SUPPORT_COVERAGE_OMISSIONS")
            omissions = []
        else:
            for omission in omissions:
                if (
                    not isinstance(omission, dict)
                    or set(omission) != {"prior_question_id", "code"}
                    or omission.get("code") not in BUDGET_ISSUE_CODES
                    or not isinstance(omission.get("prior_question_id"), str)
                ):
                    codes.append("SUPPORT_COVERAGE_OMISSION")
        if coverage.get("selected_item_count") != len(items):
            codes.append("SUPPORT_COVERAGE_SELECTED_COUNT")
        if coverage.get("omitted_item_count") != len(omissions):
            codes.append("SUPPORT_COVERAGE_OMITTED_COUNT")
        if coverage.get("eligible_item_count") != coverage.get("prior_question_count"):
            codes.append("SUPPORT_COVERAGE_ELIGIBLE_COUNT")
        if (
            isinstance(coverage.get("eligible_item_count"), int)
            and coverage.get("eligible_item_count") != len(items) + len(omissions)
        ):
            codes.append("SUPPORT_COVERAGE_TOTAL")
        omission_ids = [
            omission.get("prior_question_id")
            for omission in omissions
            if isinstance(omission, dict)
        ]
        if len(omission_ids) != len(set(omission_ids)):
            codes.append("SUPPORT_COVERAGE_OMISSION_DUPLICATE")
        if seen_question_ids.intersection(omission_ids):
            codes.append("SUPPORT_COVERAGE_ROLE_OVERLAP")

    raw_budget = bundle.get("character_budget")
    if not isinstance(raw_budget, dict):
        codes.append("SUPPORT_CHARACTER_BUDGET")
        budget = None
    else:
        try:
            budget = make_character_budget(
                max_source_refs_per_item=raw_budget.get("max_source_refs_per_item"),
                max_locator_refs_per_item=raw_budget.get("max_locator_refs_per_item"),
                max_transformations_per_item=raw_budget.get("max_transformations_per_item"),
                max_item_chars=raw_budget.get("max_item_chars"),
                max_bundle_chars=raw_budget.get("max_bundle_chars"),
            )
            if raw_budget != budget:
                codes.append("SUPPORT_CHARACTER_BUDGET_FIELDS")
        except ExperienceSupportError:
            codes.append("SUPPORT_CHARACTER_BUDGET_INVALID")
            budget = None
    if budget is not None:
        for item in items:
            if isinstance(item, dict) and _item_budget_issue(item, budget) is not None:
                codes.append("SUPPORT_ITEM_OVER_BUDGET")
        if knowledge_base.canonical_char_count(bundle) > budget["max_bundle_chars"]:
            codes.append("SUPPORT_BUNDLE_OVER_BUDGET")

    budget_issues = bundle.get("budget_issues")
    if not isinstance(budget_issues, list):
        codes.append("SUPPORT_BUDGET_ISSUES")
        budget_issues = []
    else:
        for issue in budget_issues:
            if (
                not isinstance(issue, dict)
                or set(issue) != {"code", "count"}
                or issue.get("code") not in BUDGET_ISSUE_CODES
                or type(issue.get("count")) is not int
                or issue["count"] < 1
            ):
                codes.append("SUPPORT_BUDGET_ISSUE")
    closure = bundle.get("budget_closure_code")
    if closure not in BUDGET_CLOSURE_CODES:
        codes.append("SUPPORT_BUDGET_CLOSURE_CODE")
    elif bool(budget_issues) != (closure == "SUPPORT_BUDGET_TRUNCATED_TO_LIMIT"):
        codes.append("SUPPORT_BUDGET_CLOSURE_CONTRADICTION")

    privacy = bundle.get("privacy")
    if not isinstance(privacy, dict) or set(privacy) != PRIVACY_FIELDS:
        codes.append("SUPPORT_PRIVACY_FIELDS")
    elif any(value is not True for value in privacy.values()):
        codes.append("SUPPORT_PRIVACY_GUARDS")

    if isinstance(bundle.get("bundle_id"), str):
        try:
            if bundle["bundle_id"] != compute_bundle_id(bundle):
                codes.append("SUPPORT_BUNDLE_ID_MISMATCH")
        except (KeyError, TypeError):
            codes.append("SUPPORT_BUNDLE_ID_SEED")
    if isinstance(bundle.get("bundle_hash"), str) and bundle["bundle_hash"] != compute_bundle_hash(bundle):
        codes.append("SUPPORT_BUNDLE_HASH_MISMATCH")

    if prior_manifest is not None:
        prior_codes = prior_trace.validate_prior_manifest(prior_manifest)
        if prior_codes:
            codes.append("SUPPORT_BOUND_PRIOR_INVALID")
        else:
            expected = {
                "run_id": prior_manifest["run_id"],
                "prior_manifest_id": prior_manifest["manifest_id"],
                "prior_manifest_hash": prior_manifest["manifest_hash"],
                "task_envelope_hash": prior_manifest["task_envelope_hash"],
                "knowledge_snapshot_hash": prior_manifest["knowledge_snapshot_hash"],
                "authorization_snapshot_hash": prior_manifest["authorization_snapshot_hash"],
                "authorization_ref": prior_manifest["authorization_ref"],
            }
            if any(bundle.get(key) != value for key, value in expected.items()):
                codes.append("SUPPORT_BOUND_PRIOR_MISMATCH")
            try:
                if knowledge_base.parse_datetime(bundle.get("created_at")) < knowledge_base.parse_datetime(
                    prior_manifest["created_at"]
                ):
                    codes.append("SUPPORT_CREATED_BEFORE_PRIOR_MANIFEST")
            except (TypeError, ValueError):
                pass
            prior_ids = [question["prior_question_id"] for question in prior_manifest["questions"]]
            omission_ids = [
                omission.get("prior_question_id")
                for omission in coverage.get("omissions", [])
                if isinstance(omission, dict)
            ]
            if sorted(prior_ids) != sorted([*seen_question_ids, *omission_ids]):
                codes.append("SUPPORT_BOUND_PRIOR_COVERAGE_MISMATCH")

    return sorted(set(codes))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build or validate a private experience-provenance sidecar."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser(
        "build",
        help="Build a hash-bound ExperienceSupportBundle for an existing prior manifest.",
    )
    build.add_argument("cards", type=Path)
    build.add_argument("source_manifest", type=Path)
    build.add_argument("prior_manifest", type=Path)
    build.add_argument("task_envelope", type=Path)
    build.add_argument("--authorizations", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--created-at", type=knowledge_base.parse_cli_datetime)
    build.add_argument(
        "--source-root",
        type=Path,
        help="Optional workspace root used to verify every source asset byte hash.",
    )
    build.add_argument(
        "--max-source-refs-per-item",
        type=int,
        default=DEFAULT_MAX_SOURCE_REFS_PER_ITEM,
    )
    build.add_argument(
        "--max-locator-refs-per-item",
        type=int,
        default=DEFAULT_MAX_LOCATOR_REFS_PER_ITEM,
    )
    build.add_argument(
        "--max-transformations-per-item",
        type=int,
        default=DEFAULT_MAX_TRANSFORMATIONS_PER_ITEM,
    )
    build.add_argument("--max-item-chars", type=int, default=DEFAULT_MAX_ITEM_CHARS)
    build.add_argument("--max-bundle-chars", type=int, default=DEFAULT_MAX_BUNDLE_CHARS)
    build.add_argument("--force", action="store_true")

    validate = subparsers.add_parser("validate", help="Validate a support bundle.")
    validate.add_argument("bundle", type=Path)
    validate.add_argument("--prior-manifest", type=Path)
    return parser


def _safe_error_payload(error: Exception) -> dict[str, str]:
    value = str(error)
    if not re.fullmatch(r"[A-Z0-9_;-]+", value):
        value = "SUPPORT_OPERATION_FAILED"
    return {"status": "error", "error": value}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(sys.argv[1:] if argv is None else argv)

    if args.command == "validate":
        try:
            bundle = knowledge_base.read_json_object(args.bundle, label="SUPPORT_BUNDLE")
            prior = (
                knowledge_base.read_json_object(args.prior_manifest, label="PRIOR_MANIFEST")
                if args.prior_manifest
                else None
            )
            codes = validate_experience_support_bundle(bundle, prior)
        except (ExperienceSupportError, knowledge_base.PriorManifestError) as exc:
            print(json.dumps(_safe_error_payload(exc), sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "valid" if not codes else "invalid",
                    "issue_count": len(codes),
                    "issue_codes": codes,
                    "support_item_count": len(bundle.get("support_items", [])),
                    "privacy": "content_sources_and_paths_omitted_from_stdout",
                },
                sort_keys=True,
            )
        )
        return 0 if not codes else 1

    try:
        knowledge_base.ensure_private_output_path(args.output)
        if args.output.exists() and not args.force:
            raise ExperienceSupportError("OUTPUT_EXISTS")
        cards = knowledge_base.load_cards(args.cards)
        source_records = load_source_manifest(args.source_manifest)
        prior_manifest = knowledge_base.read_json_object(
            args.prior_manifest,
            label="PRIOR_MANIFEST",
        )
        envelope = knowledge_base.read_json_object(
            args.task_envelope,
            label="TASK_ENVELOPE",
        )
        authorizations = knowledge_base.load_authorizations(args.authorizations)
        bundle = build_experience_support_bundle(
            cards,
            source_records,
            prior_manifest,
            envelope,
            authorizations,
            knowledge_snapshot_hash=knowledge_base.sha256_file(args.cards),
            source_manifest_snapshot_hash=knowledge_base.sha256_file(args.source_manifest),
            authorization_snapshot_hash=knowledge_base.sha256_file(args.authorizations),
            created_at=args.created_at,
            source_root=args.source_root,
            max_source_refs_per_item=args.max_source_refs_per_item,
            max_locator_refs_per_item=args.max_locator_refs_per_item,
            max_transformations_per_item=args.max_transformations_per_item,
            max_item_chars=args.max_item_chars,
            max_bundle_chars=args.max_bundle_chars,
        )
        codes = validate_experience_support_bundle(bundle, prior_manifest)
        if codes:
            raise ExperienceSupportError("SUPPORT_BUNDLE_SELF_VALIDATION_FAILED")
        knowledge_base.write_json_object(args.output, bundle)
    except (
        ExperienceSupportError,
        knowledge_base.AuthorizationError,
        knowledge_base.CardFileError,
        knowledge_base.PriorManifestError,
        ValueError,
    ) as exc:
        print(json.dumps(_safe_error_payload(exc), sort_keys=True))
        return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "support_item_count": len(bundle["support_items"]),
                "declared_source_count": bundle["source_asset_verification"]["declared_source_count"],
                "byte_verified_source_count": bundle["source_asset_verification"]["byte_verified_source_count"],
                "source_asset_verification_status": bundle["source_asset_verification"]["status"],
                "canonical_char_count": knowledge_base.canonical_char_count(bundle),
                "budget_closure_code": bundle["budget_closure_code"],
                "budget_issue_count": len(bundle["budget_issues"]),
                "privacy": "content_sources_and_paths_omitted_from_stdout",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
