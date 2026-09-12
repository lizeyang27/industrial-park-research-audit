#!/usr/bin/env python3
"""Bind a draft to a pre-draft question manifest and validate discovery origin.

The commands hash local files but never print draft text, filenames, paths,
knowledge-card content, source locations, or issue text.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from knowledge_base import (
    HARD_MAX_MANIFEST_CHARS,
    HARD_MAX_QUESTION_CHARS,
    HARD_MAX_QUESTIONS,
    HARD_MAX_TEXT_ARRAY_CHARS,
    HARD_MAX_TEXT_ARRAY_ITEMS,
    HARD_PER_FAMILY_LIMIT,
    LEGACY_PRIOR_MANIFEST_VERSION,
    LEGACY_PRIOR_SELECTION_POLICY_VERSION,
    PRIOR_BUDGET_CLOSURE_CODES,
    PRIOR_BUDGET_ISSUE_CODES,
    PRIOR_CHARACTER_METRIC,
    PRIOR_MANIFEST_VERSION,
    PRIOR_QUESTION_ARRAY_FIELDS,
    PRIOR_SELECTION_POLICY_VERSION,
    canonical_char_count,
    canonical_hash,
    compute_manifest_hash,
    ensure_private_output_path,
    parse_datetime,
    read_json_object,
    sha256_file,
    write_json_object,
)


RECEIPT_VERSION = "0.1"
ISSUE_REGISTER_VERSION = "0.1"
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MANIFEST_ID_RE = re.compile(r"^PM-[A-F0-9]{20}$")
QUESTION_ID_RE = re.compile(r"^PQ-[A-Z][A-Z0-9-]{2,63}$")
ISSUE_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,127}$")
NON_PRIOR_ORIGINS = {
    "draft_text",
    "document_set",
    "private_knowledge_post_draft",
    "web",
    "expert",
    "human_review",
}
LEGACY_MANIFEST_FIELDS = {
    "schema_version",
    "manifest_id",
    "run_id",
    "task_envelope_id",
    "task_envelope_hash",
    "knowledge_snapshot_hash",
    "authorization_snapshot_hash",
    "authorization_ref",
    "selection_policy_version",
    "created_at",
    "draft_seen",
    "questions",
    "coverage_by_issue_family",
    "truncation",
    "privacy",
    "manifest_hash",
}
BUDGETED_MANIFEST_FIELDS = LEGACY_MANIFEST_FIELDS | {
    "character_budget",
    "budget_closure_code",
    "budget_issues",
}
CHARACTER_BUDGET_FIELDS = {
    "metric",
    "max_text_array_items",
    "max_text_array_chars",
    "max_question_chars",
    "max_manifest_chars",
}
BUDGET_ISSUE_FIELDS = {"code", "count"}
QUESTION_FIELDS = {
    "prior_question_id",
    "knowledge_card_id",
    "issue_family",
    "risk_type",
    "atomic_question",
    "applicability",
    "exclusions",
    "counterhypothesis_prompts",
    "verification_action",
    "recommended_channels",
    "closure_condition",
    "knowledge_role",
    "eligible_as_evidence",
}
FORBIDDEN_MANIFEST_KEYS = {
    "source_ids",
    "locators",
    "source_path",
    "path",
    "draft_text",
    "draft_excerpt",
    "query_text",
    "reviewer_name",
    "author_name",
}


class TraceValidationError(RuntimeError):
    """A trace artifact is invalid; messages are safe machine codes."""


def _is_nonempty_string_list(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(
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


def validate_prior_manifest(manifest: Any) -> list[str]:
    codes: list[str] = []
    if not isinstance(manifest, dict):
        return ["MANIFEST_NOT_OBJECT"]
    manifest_version = manifest.get("schema_version")
    is_legacy = manifest_version == LEGACY_PRIOR_MANIFEST_VERSION
    is_budgeted = manifest_version == PRIOR_MANIFEST_VERSION
    expected_fields = BUDGETED_MANIFEST_FIELDS if is_budgeted else LEGACY_MANIFEST_FIELDS
    if set(manifest) != expected_fields:
        codes.append("MANIFEST_FIELDS")
    if not is_legacy and not is_budgeted:
        codes.append("MANIFEST_SCHEMA_VERSION")
    if not isinstance(manifest.get("manifest_id"), str) or not MANIFEST_ID_RE.fullmatch(
        manifest["manifest_id"]
    ):
        codes.append("MANIFEST_ID")
    for key in (
        "task_envelope_hash",
        "knowledge_snapshot_hash",
        "authorization_snapshot_hash",
        "manifest_hash",
    ):
        value = manifest.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            codes.append(f"MANIFEST_{key.upper()}")
    try:
        parse_datetime(manifest.get("created_at"))
    except (TypeError, ValueError):
        codes.append("MANIFEST_CREATED_AT")
    if manifest.get("draft_seen") is not False:
        codes.append("MANIFEST_DRAFT_SEEN")
    expected_policy = (
        PRIOR_SELECTION_POLICY_VERSION
        if is_budgeted
        else LEGACY_PRIOR_SELECTION_POLICY_VERSION
    )
    if manifest.get("selection_policy_version") != expected_policy:
        codes.append("MANIFEST_SELECTION_POLICY")
    if FORBIDDEN_MANIFEST_KEYS.intersection(_walk_keys(manifest)):
        codes.append("MANIFEST_FORBIDDEN_PRIVATE_FIELD")

    character_budget: dict[str, Any] | None = None
    budget_issue_records: list[dict[str, Any]] = []
    if is_budgeted:
        raw_budget = manifest.get("character_budget")
        if not isinstance(raw_budget, dict) or set(raw_budget) != CHARACTER_BUDGET_FIELDS:
            codes.append("MANIFEST_CHARACTER_BUDGET")
        else:
            budget_values_valid = True
            if raw_budget.get("metric") != PRIOR_CHARACTER_METRIC:
                codes.append("MANIFEST_CHARACTER_BUDGET_METRIC")
                budget_values_valid = False
            budget_limits = {
                "max_text_array_items": HARD_MAX_TEXT_ARRAY_ITEMS,
                "max_text_array_chars": HARD_MAX_TEXT_ARRAY_CHARS,
                "max_question_chars": HARD_MAX_QUESTION_CHARS,
                "max_manifest_chars": HARD_MAX_MANIFEST_CHARS,
            }
            for key, hard_limit in budget_limits.items():
                value = raw_budget.get(key)
                if type(value) is not int or value < 1:
                    codes.append("MANIFEST_CHARACTER_BUDGET_VALUES")
                    budget_values_valid = False
                elif value > hard_limit:
                    codes.append("MANIFEST_CHARACTER_BUDGET_HARD_LIMIT")
                    budget_values_valid = False
            if budget_values_valid:
                character_budget = raw_budget

        raw_budget_issues = manifest.get("budget_issues")
        if not isinstance(raw_budget_issues, list):
            codes.append("MANIFEST_BUDGET_ISSUES")
        else:
            previous_code: str | None = None
            seen_budget_codes: set[str] = set()
            for issue in raw_budget_issues:
                if not isinstance(issue, dict) or set(issue) != BUDGET_ISSUE_FIELDS:
                    codes.append("MANIFEST_BUDGET_ISSUES")
                    continue
                issue_code = issue.get("code")
                issue_count = issue.get("count")
                if issue_code not in PRIOR_BUDGET_ISSUE_CODES:
                    codes.append("MANIFEST_BUDGET_ISSUE_CODE")
                if type(issue_count) is not int or issue_count < 1:
                    codes.append("MANIFEST_BUDGET_ISSUE_COUNT")
                if not isinstance(issue_code, str) or issue_code in seen_budget_codes:
                    codes.append("MANIFEST_BUDGET_ISSUE_ORDER")
                elif previous_code is not None and issue_code < previous_code:
                    codes.append("MANIFEST_BUDGET_ISSUE_ORDER")
                else:
                    seen_budget_codes.add(issue_code)
                    previous_code = issue_code
                    budget_issue_records.append(issue)
        closure_code = manifest.get("budget_closure_code")
        if closure_code not in PRIOR_BUDGET_CLOSURE_CODES:
            codes.append("MANIFEST_BUDGET_CLOSURE_CODE")
        elif bool(budget_issue_records) != (closure_code == "PRIOR_BUDGET_TRUNCATED_TO_LIMIT"):
            codes.append("MANIFEST_BUDGET_CLOSURE_MISMATCH")

    questions = manifest.get("questions")
    question_ids: set[str] = set()
    family_counts: Counter[str] = Counter()
    if not isinstance(questions, list):
        codes.append("MANIFEST_QUESTIONS")
        questions = []
    for question in questions:
        if not isinstance(question, dict) or set(question) != QUESTION_FIELDS:
            codes.append("PRIOR_QUESTION_FIELDS")
            continue
        question_id = question.get("prior_question_id")
        if not isinstance(question_id, str) or not QUESTION_ID_RE.fullmatch(question_id):
            codes.append("PRIOR_QUESTION_ID")
        elif question_id in question_ids:
            codes.append("DUPLICATE_PRIOR_QUESTION_ID")
        else:
            question_ids.add(question_id)
        card_id = question.get("knowledge_card_id")
        if question_id != f"PQ-{card_id}":
            codes.append("PRIOR_QUESTION_CARD_BINDING")
        for key in (
            "issue_family",
            "risk_type",
            "atomic_question",
            "verification_action",
            "closure_condition",
        ):
            value = question.get(key)
            if not isinstance(value, str) or not value.strip():
                codes.append(f"PRIOR_QUESTION_{key.upper()}")
        for key in ("applicability", "exclusions", "counterhypothesis_prompts", "recommended_channels"):
            if not _is_nonempty_string_list(question.get(key)):
                codes.append(f"PRIOR_QUESTION_{key.upper()}")
        if character_budget is not None:
            for key in PRIOR_QUESTION_ARRAY_FIELDS:
                values = question.get(key)
                if not isinstance(values, list):
                    continue
                if len(values) > character_budget["max_text_array_items"]:
                    codes.append("PRIOR_TEXT_ARRAY_ITEM_LIMIT_EXCEEDED")
                if canonical_char_count(values) > character_budget["max_text_array_chars"]:
                    codes.append("PRIOR_TEXT_ARRAY_CHAR_LIMIT_EXCEEDED")
            if canonical_char_count(question) > character_budget["max_question_chars"]:
                codes.append("PRIOR_QUESTION_CHAR_LIMIT_EXCEEDED")
        if question.get("knowledge_role") != "review_prompt":
            codes.append("PRIOR_QUESTION_KNOWLEDGE_ROLE")
        if question.get("eligible_as_evidence") is not False:
            codes.append("PRIOR_QUESTION_EVIDENCE_ELIGIBILITY")
        family = question.get("issue_family")
        if isinstance(family, str) and family:
            family_counts[family] += 1

    coverage = manifest.get("coverage_by_issue_family")
    if coverage != dict(family_counts):
        codes.append("MANIFEST_COVERAGE_COUNTS")
    truncation = manifest.get("truncation")
    if not isinstance(truncation, dict):
        codes.append("MANIFEST_TRUNCATION")
    else:
        integer_fields = ("max_questions", "per_family_limit", "eligible_count", "selected_count", "omitted_count")
        if any(not isinstance(truncation.get(key), int) or truncation[key] < 0 for key in integer_fields):
            codes.append("MANIFEST_TRUNCATION_VALUES")
        else:
            if truncation["max_questions"] < 1 or truncation["per_family_limit"] < 1:
                codes.append("MANIFEST_TRUNCATION_LIMITS")
            if is_budgeted and (
                truncation["max_questions"] > HARD_MAX_QUESTIONS
                or truncation["per_family_limit"] > HARD_PER_FAMILY_LIMIT
            ):
                codes.append("MANIFEST_TRUNCATION_HARD_LIMIT")
            if truncation["selected_count"] != len(questions):
                codes.append("MANIFEST_SELECTED_COUNT")
            if truncation["eligible_count"] - truncation["selected_count"] != truncation["omitted_count"]:
                codes.append("MANIFEST_OMITTED_COUNT")
            if len(questions) > truncation["max_questions"]:
                codes.append("MANIFEST_MAX_QUESTIONS")
            if any(count > truncation["per_family_limit"] for count in family_counts.values()):
                codes.append("MANIFEST_PER_FAMILY_LIMIT")
            if is_budgeted:
                recorded_budget_omissions = sum(
                    issue["count"]
                    for issue in budget_issue_records
                    if type(issue.get("count")) is int
                )
                if recorded_budget_omissions > truncation["omitted_count"]:
                    codes.append("MANIFEST_BUDGET_ISSUE_COUNT_MISMATCH")
    privacy = manifest.get("privacy")
    expected_privacy = {
        "private_review_only": True,
        "card_bodies_not_logged": True,
        "source_locations_omitted": True,
        "questions_are_not_evidence": True,
    }
    if privacy != expected_privacy:
        codes.append("MANIFEST_PRIVACY_FLAGS")

    if character_budget is not None and canonical_char_count(manifest) > character_budget["max_manifest_chars"]:
        codes.append("PRIOR_MANIFEST_CHAR_LIMIT_EXCEEDED")

    if isinstance(manifest.get("manifest_hash"), str) and manifest["manifest_hash"] != compute_manifest_hash(manifest):
        codes.append("MANIFEST_HASH_MISMATCH")
    if all(isinstance(manifest.get(key), str) for key in (
        "run_id",
        "task_envelope_hash",
        "knowledge_snapshot_hash",
        "authorization_snapshot_hash",
        "created_at",
    )):
        seed: dict[str, Any] = {
            "run_id": manifest["run_id"],
            "task_envelope_hash": manifest["task_envelope_hash"],
            "knowledge_snapshot_hash": manifest["knowledge_snapshot_hash"],
            "authorization_snapshot_hash": manifest["authorization_snapshot_hash"],
            "created_at": manifest["created_at"],
        }
        if is_budgeted and character_budget is not None:
            seed["selection_policy_version"] = manifest["selection_policy_version"]
            seed["character_budget"] = character_budget
        expected_id = "PM-" + canonical_hash(seed)[:20].upper()
        if manifest.get("manifest_id") != expected_id:
            codes.append("MANIFEST_ID_MISMATCH")
    return sorted(set(codes))


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})


def build_draft_receipt(
    manifest: dict[str, Any],
    draft_path: Path,
    *,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    manifest_codes = validate_prior_manifest(manifest)
    if manifest_codes:
        raise TraceValidationError(";".join(manifest_codes))
    observed_at = ingested_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise TraceValidationError("DRAFT_INGESTED_AT_TIMEZONE_REQUIRED")
    manifest_created_at = parse_datetime(manifest["created_at"])
    if observed_at <= manifest_created_at:
        raise TraceValidationError("DRAFT_NOT_AFTER_PRIOR_MANIFEST")
    try:
        draft_bytes = draft_path.stat().st_size
    except OSError as exc:
        raise TraceValidationError("DRAFT_READ_ERROR") from exc
    draft_hash = sha256_file(draft_path)
    observed_iso = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    seed = {
        "run_id": manifest["run_id"],
        "manifest_hash": manifest["manifest_hash"],
        "draft_sha256": draft_hash,
        "draft_ingested_at": observed_iso,
    }
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_VERSION,
        "receipt_id": "DR-" + canonical_hash(seed)[:20].upper(),
        "run_id": manifest["run_id"],
        "prior_manifest_id": manifest["manifest_id"],
        "prior_manifest_hash": manifest["manifest_hash"],
        "prior_manifest_created_at": manifest["created_at"],
        "draft_ingested_at": observed_iso,
        "draft_sha256": draft_hash,
        "draft_bytes": draft_bytes,
        "draft_content_logged": False,
        "draft_path_logged": False,
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    return receipt


def validate_receipt(receipt: Any, manifest: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if not isinstance(receipt, dict):
        return ["RECEIPT_NOT_OBJECT"]
    expected_fields = {
        "schema_version",
        "receipt_id",
        "run_id",
        "prior_manifest_id",
        "prior_manifest_hash",
        "prior_manifest_created_at",
        "draft_ingested_at",
        "draft_sha256",
        "draft_bytes",
        "draft_content_logged",
        "draft_path_logged",
        "receipt_hash",
    }
    if set(receipt) != expected_fields:
        codes.append("RECEIPT_FIELDS")
    if receipt.get("schema_version") != RECEIPT_VERSION:
        codes.append("RECEIPT_SCHEMA_VERSION")
    if receipt.get("run_id") != manifest.get("run_id"):
        codes.append("RECEIPT_RUN_MISMATCH")
    if receipt.get("prior_manifest_id") != manifest.get("manifest_id"):
        codes.append("RECEIPT_MANIFEST_ID_MISMATCH")
    if receipt.get("prior_manifest_hash") != manifest.get("manifest_hash"):
        codes.append("RECEIPT_MANIFEST_HASH_MISMATCH")
    if receipt.get("prior_manifest_created_at") != manifest.get("created_at"):
        codes.append("RECEIPT_MANIFEST_TIME_MISMATCH")
    for key in ("draft_sha256", "receipt_hash"):
        value = receipt.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            codes.append(f"RECEIPT_{key.upper()}")
    if not isinstance(receipt.get("draft_bytes"), int) or receipt["draft_bytes"] < 0:
        codes.append("RECEIPT_DRAFT_BYTES")
    if receipt.get("draft_content_logged") is not False or receipt.get("draft_path_logged") is not False:
        codes.append("RECEIPT_PRIVACY_FLAGS")
    try:
        prior_time = parse_datetime(receipt.get("prior_manifest_created_at"))
        draft_time = parse_datetime(receipt.get("draft_ingested_at"))
        if draft_time <= prior_time:
            codes.append("RECEIPT_CHRONOLOGY")
    except (TypeError, ValueError):
        codes.append("RECEIPT_TIMESTAMPS")
    if isinstance(receipt.get("receipt_hash"), str) and receipt["receipt_hash"] != compute_receipt_hash(receipt):
        codes.append("RECEIPT_HASH_MISMATCH")
    return sorted(set(codes))


def validate_issue_register(
    register: Any,
    manifest: dict[str, Any],
    receipt: dict[str, Any],
) -> tuple[list[str], int, int]:
    codes: list[str] = []
    if not isinstance(register, dict):
        return ["ISSUE_REGISTER_NOT_OBJECT"], 0, 0
    if register.get("schema_version") != ISSUE_REGISTER_VERSION:
        codes.append("ISSUE_REGISTER_SCHEMA_VERSION")
    if register.get("run_id") != manifest.get("run_id"):
        codes.append("ISSUE_REGISTER_RUN_MISMATCH")
    issues = register.get("issues")
    if not isinstance(issues, list):
        return sorted(set(codes + ["ISSUE_REGISTER_ISSUES"])), 0, 0
    prior_ids = {
        question["prior_question_id"]
        for question in manifest.get("questions", [])
        if isinstance(question, dict) and isinstance(question.get("prior_question_id"), str)
    }
    seen_issues: set[str] = set()
    prior_count = 0
    draft_time = parse_datetime(receipt["draft_ingested_at"])
    for issue in issues:
        if not isinstance(issue, dict):
            codes.append("ISSUE_NOT_OBJECT")
            continue
        issue_id = issue.get("issue_id")
        if not isinstance(issue_id, str) or not ISSUE_ID_RE.fullmatch(issue_id):
            codes.append("ISSUE_ID")
        elif issue_id in seen_issues:
            codes.append("DUPLICATE_ISSUE_ID")
        else:
            seen_issues.add(issue_id)
        try:
            first_observed = parse_datetime(issue.get("first_observed_at"))
            if first_observed < draft_time:
                codes.append("ISSUE_OBSERVED_BEFORE_DRAFT")
        except (TypeError, ValueError):
            codes.append("ISSUE_FIRST_OBSERVED_AT")
        origin = issue.get("discovery_origin")
        if not isinstance(origin, dict):
            codes.append("ISSUE_DISCOVERY_ORIGIN")
            continue
        kind = origin.get("kind")
        question_id = origin.get("prior_question_id")
        if kind == "prior_knowledge":
            prior_count += 1
            if not isinstance(question_id, str) or question_id not in prior_ids:
                codes.append("PRIOR_ORIGIN_UNKNOWN_QUESTION")
        elif kind in NON_PRIOR_ORIGINS:
            if question_id is not None:
                codes.append("NON_PRIOR_ORIGIN_CLAIMS_QUESTION")
        else:
            codes.append("ISSUE_DISCOVERY_ORIGIN_KIND")
    return sorted(set(codes)), len(issues), prior_count


def parse_cli_datetime(value: str) -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a timezone-aware ISO date-time.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bind and validate pre-draft knowledge traces.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    bind = subparsers.add_parser("bind-draft", help="Hash a local draft after a valid prior manifest exists.")
    bind.add_argument("manifest", type=Path)
    bind.add_argument("draft", type=Path)
    bind.add_argument("--output", type=Path, required=True)
    bind.add_argument("--ingested-at", type=parse_cli_datetime)
    bind.add_argument("--force", action="store_true")

    validate = subparsers.add_parser("validate", help="Validate chronology and issue discovery origins.")
    validate.add_argument("manifest", type=Path)
    validate.add_argument("receipt", type=Path)
    validate.add_argument("issue_register", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        manifest = read_json_object(args.manifest, label="MANIFEST")
    except Exception:
        print(json.dumps({"status": "error", "error": "MANIFEST_READ_ERROR"}, sort_keys=True))
        return 2

    if args.command == "bind-draft":
        try:
            ensure_private_output_path(args.output)
        except Exception:
            print(
                json.dumps(
                    {"status": "error", "error": "OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION"},
                    sort_keys=True,
                )
            )
            return 2
        if args.output.exists() and not args.force:
            print(json.dumps({"status": "error", "error": "OUTPUT_EXISTS"}, sort_keys=True))
            return 2
        try:
            receipt = build_draft_receipt(manifest, args.draft, ingested_at=args.ingested_at)
            write_json_object(args.output, receipt)
        except Exception as exc:
            error = str(exc) if isinstance(exc, TraceValidationError) else "DRAFT_BIND_ERROR"
            print(json.dumps({"status": "error", "error": error}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "receipt_id": receipt["receipt_id"],
                    "receipt_hash": receipt["receipt_hash"],
                    "privacy": "draft_content_name_and_path_omitted_from_stdout",
                },
                sort_keys=True,
            )
        )
        return 0

    try:
        receipt = read_json_object(args.receipt, label="RECEIPT")
        issue_register = read_json_object(args.issue_register, label="ISSUE_REGISTER")
        codes = validate_prior_manifest(manifest)
        codes.extend(validate_receipt(receipt, manifest))
        if codes:
            issue_count = 0
            prior_count = 0
        else:
            issue_codes, issue_count, prior_count = validate_issue_register(issue_register, manifest, receipt)
            codes.extend(issue_codes)
    except Exception:
        print(json.dumps({"status": "error", "error": "TRACE_INPUT_READ_ERROR"}, sort_keys=True))
        return 2
    codes = sorted(set(codes))
    if codes:
        print(
            json.dumps(
                {"status": "invalid", "issue_count": len(codes), "issue_codes": codes},
                sort_keys=True,
            )
        )
        return 1
    print(
        json.dumps(
            {
                "status": "valid",
                "registered_issue_count": issue_count,
                "prior_origin_count": prior_count,
                "trace_hash": canonical_hash(
                    {
                        "manifest_hash": manifest["manifest_hash"],
                        "receipt_hash": receipt["receipt_hash"],
                        "issue_register_run_id": issue_register["run_id"],
                        "issue_origins": [issue.get("discovery_origin") for issue in issue_register["issues"]],
                    }
                ),
                "privacy": "content_names_and_paths_omitted_from_stdout",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
