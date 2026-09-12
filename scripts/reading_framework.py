#!/usr/bin/env python3
"""Freeze an E/F reading framework before a draft is visible.

The framework is a private review aid, never article evidence. Standard CLI
output contains only IDs, hashes, counts, and privacy status; it never prints
semantic plan text, draft content, filenames, or paths.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from knowledge_base import (
    AuthorizationError,
    PRIOR_CHARACTER_METRIC,
    authorize_context,
    canonical_char_count,
    canonical_hash,
    ensure_private_output_path,
    load_authorizations,
    parse_datetime,
    read_json_object,
    sha256_file,
    write_json_object,
)
from prior_trace import validate_prior_manifest


PLAN_VERSION = "0.1"
TOPIC_BRIEF_VERSION = "0.1"
FRAMEWORK_VERSION = "0.1"
RECEIPT_VERSION = "0.1"
SELECTION_POLICY_VERSION = "reading-framework-strict-budget-v0.1"
DEFAULT_MAX_PLAN_CHARS = 32768
DEFAULT_MAX_FRAMEWORK_CHARS = 49152
HARD_MAX_PLAN_CHARS = DEFAULT_MAX_PLAN_CHARS
HARD_MAX_FRAMEWORK_CHARS = DEFAULT_MAX_FRAMEWORK_CHARS
FRAMEWORK_BUDGET_CLOSURE_CODE = "FRAMEWORK_BUDGET_WITHIN_LIMIT"

ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,127}$")
PLAN_ID_RE = re.compile(r"^RFP-[A-Z0-9-]{3,64}$")
TOPIC_BRIEF_ID_RE = re.compile(r"^TB-[A-Z0-9-]{3,64}$")
NODE_ID_RE = re.compile(r"^RFN-[A-Z0-9-]{3,64}$")
SUPPORT_ITEM_ID_RE = re.compile(r"^ESI-[A-F0-9]{20}$")
FRAMEWORK_ID_RE = re.compile(r"^RFM-[A-F0-9]{20}$")
RECEIPT_ID_RE = re.compile(r"^RFR-[A-F0-9]{20}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
LOCAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|\\\\[^\\/\s]+[\\/]|[A-Za-z][A-Za-z0-9+.-]*://|/(?:Users|home)/)"
)

E_INPUT_ROLES = ["task_envelope", "topic_brief", "prior_question_manifest"]
F_INPUT_ROLES = E_INPUT_ROLES + ["experience_support_bundle"]
NODE_TYPES = {
    "baseline_hypothesis",
    "definition_boundary",
    "variable",
    "actor_decision",
    "mechanism_chain",
    "alternative_hypothesis",
    "expected_evidence",
    "falsifier",
    "must_check",
    "blind_spot",
}
PLAN_FIELDS = {
    "schema_version",
    "plan_id",
    "run_id",
    "task_envelope_id",
    "topic_brief_id",
    "topic_brief_hash",
    "created_at",
    "draft_seen",
    "input_roles",
    "research_question",
    "nodes",
    "framework_limits",
    "eligible_as_evidence",
}
TOPIC_BRIEF_FIELDS = {
    "schema_version",
    "brief_id",
    "run_id",
    "task_envelope_id",
    "created_at",
    "draft_seen",
    "routing_source",
    "research_question",
    "scope_codes",
    "known_boundaries",
    "eligible_as_evidence",
    "brief_hash",
}
NODE_FIELDS = {
    "node_id",
    "node_type",
    "prompt",
    "prior_question_refs",
    "support_item_refs",
}
FRAMEWORK_FIELDS = {
    "schema_version",
    "framework_id",
    "run_id",
    "evaluation_group",
    "task_envelope_id",
    "task_envelope_hash",
    "topic_brief_id",
    "topic_brief_hash",
    "prior_manifest_id",
    "prior_manifest_hash",
    "support_bundle_id",
    "support_bundle_hash",
    "framework_plan_id",
    "framework_plan_hash",
    "authorization_ref",
    "authorization_snapshot_hash",
    "authorization_checked_at",
    "authorization_purpose",
    "authorization_processor_class",
    "authorization_output_audience",
    "selection_policy_version",
    "created_at",
    "draft_seen",
    "routing_source",
    "input_roles",
    "semantic_framework",
    "linkage",
    "character_budget",
    "budget_closure_code",
    "budget_issues",
    "privacy",
    "framework_hash",
}
SEMANTIC_FIELDS = {
    "research_question",
    "nodes",
    "framework_limits",
    "eligible_as_evidence",
}
LINKAGE_FIELDS = {"prior_question_ids", "support_item_ids_used"}
BUDGET_FIELDS = {
    "metric",
    "max_plan_chars",
    "max_framework_chars",
    "actual_plan_chars",
    "actual_framework_chars",
}
PRIVACY_FIELDS = {
    "private_review_only",
    "draft_content_logged",
    "draft_path_logged",
    "source_locations_omitted",
    "framework_is_not_evidence",
}
RECEIPT_FIELDS = {
    "schema_version",
    "receipt_id",
    "run_id",
    "evaluation_group",
    "framework_id",
    "framework_hash",
    "framework_created_at",
    "prior_manifest_id",
    "prior_manifest_hash",
    "support_bundle_id",
    "support_bundle_hash",
    "draft_ingested_at",
    "draft_sha256",
    "draft_bytes",
    "draft_content_logged",
    "draft_path_logged",
    "receipt_hash",
}
FORBIDDEN_PLAN_KEYS = {
    "draft_text",
    "draft_excerpt",
    "draft_anchor",
    "paragraph_anchor",
    "sentence_anchor",
    "gold_id",
    "expected_verdict",
    "verdict",
    "source_path",
    "local_path",
    "file_path",
    "filename",
    "locator",
    "locators",
}


class FrameworkError(RuntimeError):
    """A framework artifact failed closed; messages are safe codes."""


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _walk_strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _is_unique_string_list(value: Any, *, nonempty: bool = False) -> bool:
    if not isinstance(value, list) or (nonempty and not value):
        return False
    if not all(isinstance(item, str) and bool(item.strip()) for item in value):
        return False
    return len(value) == len(set(value))


def _support_item_ids(bundle: dict[str, Any] | None) -> set[str]:
    if bundle is None:
        return set()
    items = bundle.get("support_items")
    if not isinstance(items, list):
        return set()
    return {
        item["support_item_id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("support_item_id"), str)
    }


def _validate_support_bundle(
    bundle: dict[str, Any] | None,
    prior_manifest: dict[str, Any],
) -> list[str]:
    if bundle is None:
        return []
    try:
        from experience_support import validate_experience_support_bundle
    except (ImportError, AttributeError):
        return ["SUPPORT_VALIDATOR_UNAVAILABLE"]
    try:
        return list(validate_experience_support_bundle(bundle, prior_manifest=prior_manifest))
    except TypeError:
        return ["SUPPORT_VALIDATOR_INTERFACE"]
    except Exception:
        return ["SUPPORT_VALIDATION_ERROR"]


def validate_current_authorization(
    authorizations: dict[str, dict[str, Any]],
    authorization_snapshot_hash: str,
    prior_manifest: dict[str, Any],
    *,
    checked_at: datetime,
    support_bundle: dict[str, Any] | None = None,
) -> list[str]:
    """Recheck the current authorization before abstract model-context use."""

    codes: list[str] = []
    if not isinstance(authorization_snapshot_hash, str) or not SHA256_RE.fullmatch(
        authorization_snapshot_hash
    ):
        codes.append("CURRENT_AUTHORIZATION_SNAPSHOT_HASH")
    if checked_at.tzinfo is None or checked_at.utcoffset() is None:
        return sorted(set(codes + ["AUTHORIZATION_CHECK_TIMEZONE_REQUIRED"]))
    authorization_ref = prior_manifest.get("authorization_ref")
    if not isinstance(authorization_ref, str) or not ID_RE.fullmatch(authorization_ref):
        return sorted(set(codes + ["CURRENT_AUTHORIZATION_REF"]))
    if support_bundle is not None and support_bundle.get("authorization_ref") != authorization_ref:
        codes.append("SUPPORT_AUTHORIZATION_REF_MISMATCH")
    authorization = authorizations.get(authorization_ref)
    if authorization is None:
        codes.append("AUTHORIZATION_NOT_FOUND")
        return sorted(set(codes))
    context = {
        "authorization_id": authorization_ref,
        "purpose": "local-review",
        "processor_class": "model_context_abstract",
        "output_audience": "private_review",
    }
    try:
        authorize_context(
            authorization,
            context,
            at_date=checked_at.astimezone(timezone.utc).date(),
        )
    except AuthorizationError as exc:
        codes.append(str(exc))
    except Exception:
        codes.append("AUTHORIZATION_CHECK_ERROR")
    return sorted(set(codes))


def _expected_roles(group: str) -> list[str]:
    return E_INPUT_ROLES if group == "E" else F_INPUT_ROLES


def _semantic_from_plan(plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "research_question": copy.deepcopy(plan["research_question"]),
        "nodes": copy.deepcopy(plan["nodes"]),
        "framework_limits": copy.deepcopy(plan["framework_limits"]),
        "eligible_as_evidence": False,
    }


def compute_topic_brief_hash(topic_brief: dict[str, Any]) -> str:
    return canonical_hash(
        {key: value for key, value in topic_brief.items() if key != "brief_hash"}
    )


def validate_topic_brief(
    topic_brief: Any,
    prior_manifest: dict[str, Any],
) -> list[str]:
    codes: list[str] = []
    if not isinstance(topic_brief, dict):
        return ["TOPIC_BRIEF_NOT_OBJECT"]
    if set(topic_brief) != TOPIC_BRIEF_FIELDS:
        codes.append("TOPIC_BRIEF_FIELDS")
    if topic_brief.get("schema_version") != TOPIC_BRIEF_VERSION:
        codes.append("TOPIC_BRIEF_SCHEMA_VERSION")
    brief_id = topic_brief.get("brief_id")
    if not isinstance(brief_id, str) or not TOPIC_BRIEF_ID_RE.fullmatch(brief_id):
        codes.append("TOPIC_BRIEF_ID")
    if topic_brief.get("run_id") != prior_manifest.get("run_id"):
        codes.append("TOPIC_BRIEF_RUN_MISMATCH")
    if topic_brief.get("task_envelope_id") != prior_manifest.get("task_envelope_id"):
        codes.append("TOPIC_BRIEF_TASK_ENVELOPE_MISMATCH")
    try:
        brief_time = parse_datetime(topic_brief.get("created_at"))
        prior_time = parse_datetime(prior_manifest.get("created_at"))
        if brief_time >= prior_time:
            codes.append("TOPIC_BRIEF_NOT_BEFORE_PRIOR_MANIFEST")
    except (TypeError, ValueError):
        codes.append("TOPIC_BRIEF_CREATED_AT")
    if topic_brief.get("draft_seen") is not False:
        codes.append("TOPIC_BRIEF_DRAFT_SEEN")
    if topic_brief.get("routing_source") != "user_supplied_or_preapproved_context_only":
        codes.append("TOPIC_BRIEF_ROUTING_SOURCE")
    if (
        not isinstance(topic_brief.get("research_question"), str)
        or not topic_brief["research_question"].strip()
        or len(topic_brief["research_question"]) > 1024
    ):
        codes.append("TOPIC_BRIEF_RESEARCH_QUESTION")
    if (
        not _is_unique_string_list(topic_brief.get("scope_codes"), nonempty=True)
        or len(topic_brief.get("scope_codes", [])) > 12
        or any(len(value) > 128 for value in topic_brief.get("scope_codes", []) if isinstance(value, str))
    ):
        codes.append("TOPIC_BRIEF_SCOPE_CODES")
    if (
        not _is_unique_string_list(topic_brief.get("known_boundaries"), nonempty=True)
        or len(topic_brief.get("known_boundaries", [])) > 12
        or any(
            len(value) > 512
            for value in topic_brief.get("known_boundaries", [])
            if isinstance(value, str)
        )
    ):
        codes.append("TOPIC_BRIEF_KNOWN_BOUNDARIES")
    if topic_brief.get("eligible_as_evidence") is not False:
        codes.append("TOPIC_BRIEF_EVIDENCE_ELIGIBILITY")
    if FORBIDDEN_PLAN_KEYS.intersection(_walk_keys(topic_brief)):
        codes.append("TOPIC_BRIEF_FORBIDDEN_FIELD")
    if any(LOCAL_PATH_RE.search(value) for value in _walk_strings(topic_brief)):
        codes.append("TOPIC_BRIEF_LOCAL_PATH")
    brief_hash = topic_brief.get("brief_hash")
    if not isinstance(brief_hash, str) or not SHA256_RE.fullmatch(brief_hash):
        codes.append("TOPIC_BRIEF_HASH")
    elif brief_hash != compute_topic_brief_hash(topic_brief):
        codes.append("TOPIC_BRIEF_HASH_MISMATCH")
    return sorted(set(codes))


def _prior_question_ids(prior_manifest: dict[str, Any]) -> set[str]:
    questions = prior_manifest.get("questions")
    if not isinstance(questions, list):
        return set()
    return {
        question["prior_question_id"]
        for question in questions
        if isinstance(question, dict) and isinstance(question.get("prior_question_id"), str)
    }


def _used_refs(plan: dict[str, Any], key: str) -> set[str]:
    refs: set[str] = set()
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        return refs
    for node in nodes:
        if not isinstance(node, dict) or not isinstance(node.get(key), list):
            continue
        refs.update(ref for ref in node[key] if isinstance(ref, str))
    return refs


def validate_framework_plan(
    plan: Any,
    prior_manifest: dict[str, Any],
    topic_brief: dict[str, Any],
    *,
    evaluation_group: str,
    support_bundle: dict[str, Any] | None = None,
) -> list[str]:
    codes: list[str] = []
    if not isinstance(plan, dict):
        return ["PLAN_NOT_OBJECT"]
    if set(plan) != PLAN_FIELDS:
        codes.append("PLAN_FIELDS")
    if plan.get("schema_version") != PLAN_VERSION:
        codes.append("PLAN_SCHEMA_VERSION")
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not PLAN_ID_RE.fullmatch(plan_id):
        codes.append("PLAN_ID")
    if evaluation_group not in {"E", "F"}:
        codes.append("PLAN_EVALUATION_GROUP")
    if plan.get("run_id") != prior_manifest.get("run_id"):
        codes.append("PLAN_RUN_MISMATCH")
    if plan.get("task_envelope_id") != prior_manifest.get("task_envelope_id"):
        codes.append("PLAN_TASK_ENVELOPE_MISMATCH")
    topic_codes = validate_topic_brief(topic_brief, prior_manifest)
    codes.extend(topic_codes)
    if plan.get("topic_brief_id") != topic_brief.get("brief_id"):
        codes.append("PLAN_TOPIC_BRIEF_ID_MISMATCH")
    declared_topic_hash = plan.get("topic_brief_hash")
    if not isinstance(declared_topic_hash, str) or not SHA256_RE.fullmatch(declared_topic_hash):
        codes.append("PLAN_TOPIC_BRIEF_HASH")
    elif declared_topic_hash != topic_brief.get("brief_hash"):
        codes.append("PLAN_TOPIC_BRIEF_HASH_MISMATCH")
    try:
        plan_time = parse_datetime(plan.get("created_at"))
        prior_time = parse_datetime(prior_manifest.get("created_at"))
        if plan_time <= prior_time:
            codes.append("PLAN_NOT_AFTER_PRIOR_MANIFEST")
    except (TypeError, ValueError):
        codes.append("PLAN_CREATED_AT")
    if plan.get("draft_seen") is not False:
        codes.append("PLAN_DRAFT_SEEN")
    expected_roles = _expected_roles(evaluation_group) if evaluation_group in {"E", "F"} else []
    if plan.get("input_roles") != expected_roles:
        codes.append("PLAN_INPUT_ROLES")
    if (
        not isinstance(plan.get("research_question"), str)
        or not plan["research_question"].strip()
        or len(plan["research_question"]) > 1024
    ):
        codes.append("PLAN_RESEARCH_QUESTION")
    if (
        not _is_unique_string_list(plan.get("framework_limits"), nonempty=True)
        or len(plan.get("framework_limits", [])) > 12
        or any(
            len(value) > 512
            for value in plan.get("framework_limits", [])
            if isinstance(value, str)
        )
    ):
        codes.append("PLAN_FRAMEWORK_LIMITS")
    if plan.get("eligible_as_evidence") is not False:
        codes.append("PLAN_EVIDENCE_ELIGIBILITY")
    if FORBIDDEN_PLAN_KEYS.intersection(_walk_keys(plan)):
        codes.append("PLAN_FORBIDDEN_FIELD")
    if any(LOCAL_PATH_RE.search(value) for value in _walk_strings(plan)):
        codes.append("PLAN_LOCAL_PATH")

    known_prior_ids = _prior_question_ids(prior_manifest)
    if not known_prior_ids:
        codes.append("PLAN_PRIOR_MANIFEST_EMPTY")
    known_support_ids = _support_item_ids(support_bundle)
    node_ids: set[str] = set()
    node_types: set[str] = set()
    used_prior_ids: set[str] = set()
    used_support_ids: set[str] = set()
    nodes = plan.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        codes.append("PLAN_NODES")
        nodes = []
    elif len(nodes) > 96:
        codes.append("PLAN_NODE_LIMIT")
    for node in nodes:
        if not isinstance(node, dict) or set(node) != NODE_FIELDS:
            codes.append("PLAN_NODE_FIELDS")
            continue
        node_id = node.get("node_id")
        if not isinstance(node_id, str) or not NODE_ID_RE.fullmatch(node_id):
            codes.append("PLAN_NODE_ID")
        elif node_id in node_ids:
            codes.append("PLAN_DUPLICATE_NODE_ID")
        else:
            node_ids.add(node_id)
        node_type = node.get("node_type")
        if node_type not in NODE_TYPES:
            codes.append("PLAN_NODE_TYPE")
        else:
            node_types.add(node_type)
        if (
            not isinstance(node.get("prompt"), str)
            or not node["prompt"].strip()
            or len(node["prompt"]) > 2048
        ):
            codes.append("PLAN_NODE_PROMPT")
        prior_refs = node.get("prior_question_refs")
        support_refs = node.get("support_item_refs")
        if not _is_unique_string_list(prior_refs):
            codes.append("PLAN_NODE_PRIOR_REFS")
        else:
            used_prior_ids.update(prior_refs)
            if any(ref not in known_prior_ids for ref in prior_refs):
                codes.append("PLAN_UNKNOWN_PRIOR_REF")
        if not _is_unique_string_list(support_refs):
            codes.append("PLAN_NODE_SUPPORT_REFS")
        else:
            used_support_ids.update(support_refs)
            if any(not SUPPORT_ITEM_ID_RE.fullmatch(ref) for ref in support_refs):
                codes.append("PLAN_SUPPORT_REF_ID")
            if any(ref not in known_support_ids for ref in support_refs):
                codes.append("PLAN_UNKNOWN_SUPPORT_REF")
    if not NODE_TYPES.issubset(node_types):
        codes.append("PLAN_REQUIRED_NODE_TYPES")
    if used_prior_ids != known_prior_ids:
        codes.append("PLAN_PRIOR_COVERAGE")

    if evaluation_group == "E":
        if support_bundle is not None:
            codes.append("GROUP_E_SUPPORT_FORBIDDEN")
        if used_support_ids:
            codes.append("GROUP_E_SUPPORT_REF_FORBIDDEN")
    elif evaluation_group == "F":
        if support_bundle is None:
            codes.append("GROUP_F_SUPPORT_REQUIRED")
        else:
            support_codes = _validate_support_bundle(support_bundle, prior_manifest)
            codes.extend(support_codes)
            try:
                support_time = parse_datetime(support_bundle.get("created_at"))
                plan_time = parse_datetime(plan.get("created_at"))
                if plan_time <= support_time:
                    codes.append("PLAN_NOT_AFTER_SUPPORT_BUNDLE")
            except (TypeError, ValueError):
                codes.append("SUPPORT_CREATED_AT")
            if not known_support_ids:
                codes.append("GROUP_F_SUPPORT_ITEMS_REQUIRED")
            if not used_support_ids:
                codes.append("GROUP_F_SUPPORT_USE_REQUIRED")
    return sorted(set(codes))


def _validate_budget_limits(max_plan_chars: int, max_framework_chars: int) -> None:
    if type(max_plan_chars) is not int or type(max_framework_chars) is not int:
        raise FrameworkError("FRAMEWORK_BUDGET_NOT_POSITIVE")
    if max_plan_chars < 1 or max_framework_chars < 1:
        raise FrameworkError("FRAMEWORK_BUDGET_NOT_POSITIVE")
    if max_plan_chars > HARD_MAX_PLAN_CHARS or max_framework_chars > HARD_MAX_FRAMEWORK_CHARS:
        raise FrameworkError("FRAMEWORK_BUDGET_ABOVE_HARD_LIMIT")


def _framework_char_count(framework: dict[str, Any]) -> int:
    unsigned = copy.deepcopy({key: value for key, value in framework.items() if key != "framework_hash"})
    budget = unsigned.get("character_budget")
    if isinstance(budget, dict):
        budget.pop("actual_framework_chars", None)
    return canonical_char_count(unsigned)


def compute_framework_hash(framework: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in framework.items() if key != "framework_hash"})


def build_reading_framework(
    prior_manifest: dict[str, Any],
    plan: dict[str, Any],
    topic_brief: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    evaluation_group: str,
    authorization_snapshot_hash: str,
    support_bundle: dict[str, Any] | None = None,
    created_at: datetime | None = None,
    max_plan_chars: int = DEFAULT_MAX_PLAN_CHARS,
    max_framework_chars: int = DEFAULT_MAX_FRAMEWORK_CHARS,
) -> dict[str, Any]:
    prior_codes = validate_prior_manifest(prior_manifest)
    if prior_codes:
        raise FrameworkError(";".join(prior_codes))
    plan_codes = validate_framework_plan(
        plan,
        prior_manifest,
        topic_brief,
        evaluation_group=evaluation_group,
        support_bundle=support_bundle,
    )
    if plan_codes:
        raise FrameworkError(";".join(plan_codes))
    _validate_budget_limits(max_plan_chars, max_framework_chars)

    frozen_at = created_at or datetime.now(timezone.utc)
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise FrameworkError("FRAMEWORK_CREATED_AT_TIMEZONE_REQUIRED")
    latest_input_time = parse_datetime(plan["created_at"])
    if support_bundle is not None:
        latest_input_time = max(latest_input_time, parse_datetime(support_bundle["created_at"]))
    if frozen_at <= latest_input_time:
        raise FrameworkError("FRAMEWORK_NOT_AFTER_INPUTS")
    authorization_codes = validate_current_authorization(
        authorizations,
        authorization_snapshot_hash,
        prior_manifest,
        checked_at=frozen_at,
        support_bundle=support_bundle,
    )
    if authorization_codes:
        raise FrameworkError(";".join(authorization_codes))

    plan_chars = canonical_char_count(plan)
    if plan_chars > max_plan_chars:
        raise FrameworkError("FRAMEWORK_PLAN_CHAR_LIMIT_EXCEEDED")
    created_iso = frozen_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    support_id = support_bundle["bundle_id"] if support_bundle is not None else None
    support_hash = support_bundle["bundle_hash"] if support_bundle is not None else None
    plan_hash = canonical_hash(plan)
    budget_limits = {
        "metric": PRIOR_CHARACTER_METRIC,
        "max_plan_chars": max_plan_chars,
        "max_framework_chars": max_framework_chars,
        "actual_plan_chars": plan_chars,
        "actual_framework_chars": 0,
    }
    seed = {
        "run_id": prior_manifest["run_id"],
        "evaluation_group": evaluation_group,
        "prior_manifest_hash": prior_manifest["manifest_hash"],
        "support_bundle_hash": support_hash,
        "framework_plan_hash": plan_hash,
        "authorization_ref": prior_manifest["authorization_ref"],
        "authorization_snapshot_hash": authorization_snapshot_hash,
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "created_at": created_iso,
        "character_budget_limits": {
            "max_plan_chars": max_plan_chars,
            "max_framework_chars": max_framework_chars,
        },
    }
    framework: dict[str, Any] = {
        "schema_version": FRAMEWORK_VERSION,
        "framework_id": "RFM-" + canonical_hash(seed)[:20].upper(),
        "run_id": prior_manifest["run_id"],
        "evaluation_group": evaluation_group,
        "task_envelope_id": prior_manifest["task_envelope_id"],
        "task_envelope_hash": prior_manifest["task_envelope_hash"],
        "topic_brief_id": topic_brief["brief_id"],
        "topic_brief_hash": topic_brief["brief_hash"],
        "prior_manifest_id": prior_manifest["manifest_id"],
        "prior_manifest_hash": prior_manifest["manifest_hash"],
        "support_bundle_id": support_id,
        "support_bundle_hash": support_hash,
        "framework_plan_id": plan["plan_id"],
        "framework_plan_hash": plan_hash,
        "authorization_ref": prior_manifest["authorization_ref"],
        "authorization_snapshot_hash": authorization_snapshot_hash,
        "authorization_checked_at": created_iso,
        "authorization_purpose": "local-review",
        "authorization_processor_class": "model_context_abstract",
        "authorization_output_audience": "private_review",
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "created_at": created_iso,
        "draft_seen": False,
        "routing_source": (
            "task_metadata_topic_and_prior_only"
            if evaluation_group == "E"
            else "task_metadata_topic_prior_and_support"
        ),
        "input_roles": list(plan["input_roles"]),
        "semantic_framework": _semantic_from_plan(plan),
        "linkage": {
            "prior_question_ids": sorted(_prior_question_ids(prior_manifest)),
            "support_item_ids_used": sorted(_used_refs(plan, "support_item_refs")),
        },
        "character_budget": budget_limits,
        "budget_closure_code": FRAMEWORK_BUDGET_CLOSURE_CODE,
        "budget_issues": [],
        "privacy": {
            "private_review_only": True,
            "draft_content_logged": False,
            "draft_path_logged": False,
            "source_locations_omitted": True,
            "framework_is_not_evidence": True,
        },
    }
    framework["character_budget"]["actual_framework_chars"] = _framework_char_count(framework)
    if framework["character_budget"]["actual_framework_chars"] > max_framework_chars:
        raise FrameworkError("FRAMEWORK_CHAR_LIMIT_EXCEEDED")
    framework["framework_hash"] = compute_framework_hash(framework)
    return framework


def validate_reading_framework(
    framework: Any,
    prior_manifest: dict[str, Any],
    plan: dict[str, Any],
    topic_brief: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    authorization_snapshot_hash: str,
    authorization_checked_at: datetime | None = None,
    support_bundle: dict[str, Any] | None = None,
) -> list[str]:
    codes: list[str] = []
    if not isinstance(framework, dict):
        return ["FRAMEWORK_NOT_OBJECT"]
    if set(framework) != FRAMEWORK_FIELDS:
        codes.append("FRAMEWORK_FIELDS")
    if framework.get("schema_version") != FRAMEWORK_VERSION:
        codes.append("FRAMEWORK_SCHEMA_VERSION")
    framework_id = framework.get("framework_id")
    if not isinstance(framework_id, str) or not FRAMEWORK_ID_RE.fullmatch(framework_id):
        codes.append("FRAMEWORK_ID")
    group = framework.get("evaluation_group")
    if group not in {"E", "F"}:
        codes.append("FRAMEWORK_EVALUATION_GROUP")
        group = "E"

    prior_codes = validate_prior_manifest(prior_manifest)
    codes.extend(f"PRIOR_{code}" for code in prior_codes)
    plan_codes = validate_framework_plan(
        plan,
        prior_manifest,
        topic_brief,
        evaluation_group=group,
        support_bundle=support_bundle,
    )
    codes.extend(plan_codes)
    checked_at = authorization_checked_at or datetime.now(timezone.utc)
    authorization_codes = validate_current_authorization(
        authorizations,
        authorization_snapshot_hash,
        prior_manifest,
        checked_at=checked_at,
        support_bundle=support_bundle,
    )
    codes.extend(authorization_codes)
    expected_scalars = {
        "run_id": prior_manifest.get("run_id"),
        "task_envelope_id": prior_manifest.get("task_envelope_id"),
        "task_envelope_hash": prior_manifest.get("task_envelope_hash"),
        "topic_brief_id": topic_brief.get("brief_id"),
        "topic_brief_hash": topic_brief.get("brief_hash"),
        "prior_manifest_id": prior_manifest.get("manifest_id"),
        "prior_manifest_hash": prior_manifest.get("manifest_hash"),
        "framework_plan_id": plan.get("plan_id"),
        "framework_plan_hash": canonical_hash(plan),
        "authorization_ref": prior_manifest.get("authorization_ref"),
        "authorization_snapshot_hash": authorization_snapshot_hash,
        "authorization_checked_at": framework.get("created_at"),
        "authorization_purpose": "local-review",
        "authorization_processor_class": "model_context_abstract",
        "authorization_output_audience": "private_review",
        "selection_policy_version": SELECTION_POLICY_VERSION,
        "draft_seen": False,
    }
    for key, value in expected_scalars.items():
        if framework.get(key) != value:
            codes.append(f"FRAMEWORK_{key.upper()}_MISMATCH")
    expected_support_id = support_bundle.get("bundle_id") if support_bundle is not None else None
    expected_support_hash = support_bundle.get("bundle_hash") if support_bundle is not None else None
    if framework.get("support_bundle_id") != expected_support_id:
        codes.append("FRAMEWORK_SUPPORT_BUNDLE_ID_MISMATCH")
    if framework.get("support_bundle_hash") != expected_support_hash:
        codes.append("FRAMEWORK_SUPPORT_BUNDLE_HASH_MISMATCH")
    expected_roles = _expected_roles(group)
    if framework.get("input_roles") != expected_roles:
        codes.append("FRAMEWORK_INPUT_ROLES")
    expected_routing = (
        "task_metadata_topic_and_prior_only"
        if group == "E"
        else "task_metadata_topic_prior_and_support"
    )
    if framework.get("routing_source") != expected_routing:
        codes.append("FRAMEWORK_ROUTING_SOURCE")
    try:
        framework_time = parse_datetime(framework.get("created_at"))
        input_times = [parse_datetime(plan.get("created_at"))]
        if support_bundle is not None:
            input_times.append(parse_datetime(support_bundle.get("created_at")))
        if framework_time <= max(input_times):
            codes.append("FRAMEWORK_NOT_AFTER_INPUTS")
    except (TypeError, ValueError):
        codes.append("FRAMEWORK_CREATED_AT")
    if framework.get("semantic_framework") != _semantic_from_plan(plan):
        codes.append("FRAMEWORK_SEMANTIC_MISMATCH")
    linkage = framework.get("linkage")
    expected_linkage = {
        "prior_question_ids": sorted(_prior_question_ids(prior_manifest)),
        "support_item_ids_used": sorted(_used_refs(plan, "support_item_refs")),
    }
    if not isinstance(linkage, dict) or set(linkage) != LINKAGE_FIELDS or linkage != expected_linkage:
        codes.append("FRAMEWORK_LINKAGE")
    budget = framework.get("character_budget")
    if not isinstance(budget, dict) or set(budget) != BUDGET_FIELDS:
        codes.append("FRAMEWORK_CHARACTER_BUDGET")
    else:
        if budget.get("metric") != PRIOR_CHARACTER_METRIC:
            codes.append("FRAMEWORK_CHARACTER_BUDGET_METRIC")
        max_plan = budget.get("max_plan_chars")
        max_frame = budget.get("max_framework_chars")
        actual_plan = budget.get("actual_plan_chars")
        actual_frame = budget.get("actual_framework_chars")
        if any(type(value) is not int or value < 1 for value in (max_plan, max_frame, actual_plan, actual_frame)):
            codes.append("FRAMEWORK_CHARACTER_BUDGET_VALUES")
        else:
            if max_plan > HARD_MAX_PLAN_CHARS or max_frame > HARD_MAX_FRAMEWORK_CHARS:
                codes.append("FRAMEWORK_CHARACTER_BUDGET_HARD_LIMIT")
            if actual_plan != canonical_char_count(plan):
                codes.append("FRAMEWORK_PLAN_CHAR_COUNT")
            if actual_frame != _framework_char_count(framework):
                codes.append("FRAMEWORK_CHAR_COUNT")
            if actual_plan > max_plan:
                codes.append("FRAMEWORK_PLAN_CHAR_LIMIT_EXCEEDED")
            if actual_frame > max_frame:
                codes.append("FRAMEWORK_CHAR_LIMIT_EXCEEDED")
    if framework.get("budget_closure_code") != FRAMEWORK_BUDGET_CLOSURE_CODE:
        codes.append("FRAMEWORK_BUDGET_CLOSURE_CODE")
    if framework.get("budget_issues") != []:
        codes.append("FRAMEWORK_BUDGET_ISSUES")
    expected_privacy = {
        "private_review_only": True,
        "draft_content_logged": False,
        "draft_path_logged": False,
        "source_locations_omitted": True,
        "framework_is_not_evidence": True,
    }
    privacy = framework.get("privacy")
    if not isinstance(privacy, dict) or set(privacy) != PRIVACY_FIELDS or privacy != expected_privacy:
        codes.append("FRAMEWORK_PRIVACY")

    frame_hash = framework.get("framework_hash")
    if not isinstance(frame_hash, str) or not SHA256_RE.fullmatch(frame_hash):
        codes.append("FRAMEWORK_HASH")
    elif frame_hash != compute_framework_hash(framework):
        codes.append("FRAMEWORK_HASH_MISMATCH")
    if all(
        isinstance(framework.get(key), str)
        for key in ("run_id", "prior_manifest_hash", "framework_plan_hash", "created_at")
    ) and isinstance(budget, dict):
        seed = {
            "run_id": framework["run_id"],
            "evaluation_group": group,
            "prior_manifest_hash": framework["prior_manifest_hash"],
            "support_bundle_hash": framework.get("support_bundle_hash"),
            "framework_plan_hash": framework["framework_plan_hash"],
            "authorization_ref": framework.get("authorization_ref"),
            "authorization_snapshot_hash": framework.get("authorization_snapshot_hash"),
            "selection_policy_version": framework.get("selection_policy_version"),
            "created_at": framework["created_at"],
            "character_budget_limits": {
                "max_plan_chars": budget.get("max_plan_chars"),
                "max_framework_chars": budget.get("max_framework_chars"),
            },
        }
        expected_id = "RFM-" + canonical_hash(seed)[:20].upper()
        if framework.get("framework_id") != expected_id:
            codes.append("FRAMEWORK_ID_MISMATCH")
    return sorted(set(codes))


def compute_receipt_hash(receipt: dict[str, Any]) -> str:
    return canonical_hash({key: value for key, value in receipt.items() if key != "receipt_hash"})


def build_framework_draft_receipt(
    framework: dict[str, Any],
    prior_manifest: dict[str, Any],
    plan: dict[str, Any],
    topic_brief: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    draft_path: Path,
    *,
    authorization_snapshot_hash: str,
    support_bundle: dict[str, Any] | None = None,
    ingested_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = ingested_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise FrameworkError("DRAFT_INGESTED_AT_TIMEZONE_REQUIRED")
    framework_codes = validate_reading_framework(
        framework,
        prior_manifest,
        plan,
        topic_brief,
        authorizations,
        authorization_snapshot_hash=authorization_snapshot_hash,
        authorization_checked_at=observed_at,
        support_bundle=support_bundle,
    )
    if framework_codes:
        raise FrameworkError(";".join(framework_codes))
    if observed_at <= parse_datetime(framework["created_at"]):
        raise FrameworkError("DRAFT_NOT_AFTER_READING_FRAMEWORK")
    try:
        draft_bytes = draft_path.stat().st_size
    except OSError as exc:
        raise FrameworkError("DRAFT_READ_ERROR") from exc
    draft_hash = sha256_file(draft_path)
    observed_iso = observed_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    seed = {
        "run_id": framework["run_id"],
        "framework_hash": framework["framework_hash"],
        "draft_sha256": draft_hash,
        "draft_ingested_at": observed_iso,
    }
    receipt: dict[str, Any] = {
        "schema_version": RECEIPT_VERSION,
        "receipt_id": "RFR-" + canonical_hash(seed)[:20].upper(),
        "run_id": framework["run_id"],
        "evaluation_group": framework["evaluation_group"],
        "framework_id": framework["framework_id"],
        "framework_hash": framework["framework_hash"],
        "framework_created_at": framework["created_at"],
        "prior_manifest_id": framework["prior_manifest_id"],
        "prior_manifest_hash": framework["prior_manifest_hash"],
        "support_bundle_id": framework["support_bundle_id"],
        "support_bundle_hash": framework["support_bundle_hash"],
        "draft_ingested_at": observed_iso,
        "draft_sha256": draft_hash,
        "draft_bytes": draft_bytes,
        "draft_content_logged": False,
        "draft_path_logged": False,
    }
    receipt["receipt_hash"] = compute_receipt_hash(receipt)
    return receipt


def validate_framework_receipt(receipt: Any, framework: dict[str, Any]) -> list[str]:
    codes: list[str] = []
    if not isinstance(receipt, dict):
        return ["RECEIPT_NOT_OBJECT"]
    if set(receipt) != RECEIPT_FIELDS:
        codes.append("RECEIPT_FIELDS")
    if receipt.get("schema_version") != RECEIPT_VERSION:
        codes.append("RECEIPT_SCHEMA_VERSION")
    receipt_id = receipt.get("receipt_id")
    if not isinstance(receipt_id, str) or not RECEIPT_ID_RE.fullmatch(receipt_id):
        codes.append("RECEIPT_ID")
    expected = {
        "run_id": framework.get("run_id"),
        "evaluation_group": framework.get("evaluation_group"),
        "framework_id": framework.get("framework_id"),
        "framework_hash": framework.get("framework_hash"),
        "framework_created_at": framework.get("created_at"),
        "prior_manifest_id": framework.get("prior_manifest_id"),
        "prior_manifest_hash": framework.get("prior_manifest_hash"),
        "support_bundle_id": framework.get("support_bundle_id"),
        "support_bundle_hash": framework.get("support_bundle_hash"),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            codes.append(f"RECEIPT_{key.upper()}_MISMATCH")
    for key in ("framework_hash", "prior_manifest_hash", "draft_sha256", "receipt_hash"):
        value = receipt.get(key)
        if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
            codes.append(f"RECEIPT_{key.upper()}")
    if receipt.get("support_bundle_hash") is not None and (
        not isinstance(receipt["support_bundle_hash"], str)
        or not SHA256_RE.fullmatch(receipt["support_bundle_hash"])
    ):
        codes.append("RECEIPT_SUPPORT_BUNDLE_HASH")
    if type(receipt.get("draft_bytes")) is not int or receipt["draft_bytes"] < 0:
        codes.append("RECEIPT_DRAFT_BYTES")
    if receipt.get("draft_content_logged") is not False or receipt.get("draft_path_logged") is not False:
        codes.append("RECEIPT_PRIVACY")
    try:
        framework_time = parse_datetime(receipt.get("framework_created_at"))
        draft_time = parse_datetime(receipt.get("draft_ingested_at"))
        if draft_time <= framework_time:
            codes.append("RECEIPT_CHRONOLOGY")
    except (TypeError, ValueError):
        codes.append("RECEIPT_TIMESTAMPS")
    receipt_hash = receipt.get("receipt_hash")
    if isinstance(receipt_hash, str) and receipt_hash != compute_receipt_hash(receipt):
        codes.append("RECEIPT_HASH_MISMATCH")
    if all(
        isinstance(receipt.get(key), str)
        for key in ("run_id", "framework_hash", "draft_sha256", "draft_ingested_at")
    ):
        seed = {
            "run_id": receipt["run_id"],
            "framework_hash": receipt["framework_hash"],
            "draft_sha256": receipt["draft_sha256"],
            "draft_ingested_at": receipt["draft_ingested_at"],
        }
        expected_id = "RFR-" + canonical_hash(seed)[:20].upper()
        if receipt.get("receipt_id") != expected_id:
            codes.append("RECEIPT_ID_MISMATCH")
    return sorted(set(codes))


def parse_cli_datetime(value: str) -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a timezone-aware ISO date-time.") from exc


def _add_common_inputs(parser: argparse.ArgumentParser, *, framework: bool) -> None:
    parser.add_argument("prior_manifest", type=Path)
    parser.add_argument("plan", type=Path)
    parser.add_argument("topic_brief", type=Path)
    if framework:
        parser.add_argument("framework", type=Path)
    parser.add_argument("--authorizations", type=Path, required=True)
    parser.add_argument("--support-bundle", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze and bind pre-draft E/F reading frameworks.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze", help="Freeze a semantic plan before any draft is read.")
    _add_common_inputs(freeze, framework=False)
    freeze.add_argument("--group", choices=("E", "F"), required=True)
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--created-at", type=parse_cli_datetime)
    freeze.add_argument("--max-plan-chars", type=int, default=DEFAULT_MAX_PLAN_CHARS)
    freeze.add_argument("--max-framework-chars", type=int, default=DEFAULT_MAX_FRAMEWORK_CHARS)

    bind = subparsers.add_parser("bind-draft", help="Hash a draft after a valid framework is frozen.")
    _add_common_inputs(bind, framework=True)
    bind.add_argument("draft", type=Path)
    bind.add_argument("--output", type=Path, required=True)
    bind.add_argument("--ingested-at", type=parse_cli_datetime)

    validate = subparsers.add_parser("validate", help="Validate a framework and optional draft receipt.")
    _add_common_inputs(validate, framework=True)
    validate.add_argument("--receipt", type=Path)
    validate.add_argument("--authorization-checked-at", type=parse_cli_datetime)
    return parser


def _read_inputs(
    args: argparse.Namespace,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, dict[str, Any]],
    str,
    dict[str, Any] | None,
]:
    prior = read_json_object(args.prior_manifest, label="PRIOR_MANIFEST")
    plan = read_json_object(args.plan, label="FRAMEWORK_PLAN")
    topic_brief = read_json_object(args.topic_brief, label="TOPIC_BRIEF")
    authorizations = load_authorizations(args.authorizations)
    authorization_snapshot_hash = sha256_file(args.authorizations)
    support = (
        read_json_object(args.support_bundle, label="SUPPORT_BUNDLE")
        if args.support_bundle is not None
        else None
    )
    return prior, plan, topic_brief, authorizations, authorization_snapshot_hash, support


def _safe_error(error: Exception, fallback: str) -> str:
    return str(error) if isinstance(error, FrameworkError) else fallback


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        (
            prior,
            plan,
            topic_brief,
            authorizations,
            authorization_snapshot_hash,
            support,
        ) = _read_inputs(args)
    except Exception:
        print(json.dumps({"status": "error", "error": "FRAMEWORK_INPUT_READ_ERROR"}, sort_keys=True))
        return 2

    if args.command == "freeze":
        try:
            ensure_private_output_path(args.output)
            if args.output.exists():
                raise FrameworkError("OUTPUT_EXISTS")
            framework = build_reading_framework(
                prior,
                plan,
                topic_brief,
                authorizations,
                evaluation_group=args.group,
                authorization_snapshot_hash=authorization_snapshot_hash,
                support_bundle=support,
                created_at=args.created_at,
                max_plan_chars=args.max_plan_chars,
                max_framework_chars=args.max_framework_chars,
            )
            write_json_object(args.output, framework)
        except Exception as exc:
            print(json.dumps({"status": "error", "error": _safe_error(exc, "FRAMEWORK_FREEZE_ERROR")}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "evaluation_group": framework["evaluation_group"],
                    "framework_id": framework["framework_id"],
                    "framework_hash": framework["framework_hash"],
                    "node_count": len(framework["semantic_framework"]["nodes"]),
                    "privacy": "semantic_text_names_and_paths_omitted_from_stdout",
                },
                sort_keys=True,
            )
        )
        return 0

    try:
        framework = read_json_object(args.framework, label="READING_FRAMEWORK")
    except Exception:
        print(json.dumps({"status": "error", "error": "FRAMEWORK_READ_ERROR"}, sort_keys=True))
        return 2

    if args.command == "bind-draft":
        try:
            ensure_private_output_path(args.output)
            if args.output.exists():
                raise FrameworkError("OUTPUT_EXISTS")
            receipt = build_framework_draft_receipt(
                framework,
                prior,
                plan,
                topic_brief,
                authorizations,
                args.draft,
                authorization_snapshot_hash=authorization_snapshot_hash,
                support_bundle=support,
                ingested_at=args.ingested_at,
            )
            write_json_object(args.output, receipt)
        except Exception as exc:
            print(json.dumps({"status": "error", "error": _safe_error(exc, "DRAFT_BIND_ERROR")}, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "evaluation_group": receipt["evaluation_group"],
                    "receipt_id": receipt["receipt_id"],
                    "receipt_hash": receipt["receipt_hash"],
                    "privacy": "draft_content_name_and_path_omitted_from_stdout",
                },
                sort_keys=True,
            )
        )
        return 0

    codes = validate_reading_framework(
        framework,
        prior,
        plan,
        topic_brief,
        authorizations,
        authorization_snapshot_hash=authorization_snapshot_hash,
        authorization_checked_at=args.authorization_checked_at,
        support_bundle=support,
    )
    receipt = None
    if args.receipt is not None:
        try:
            receipt = read_json_object(args.receipt, label="FRAMEWORK_RECEIPT")
            codes.extend(validate_framework_receipt(receipt, framework))
        except Exception:
            codes.append("RECEIPT_READ_ERROR")
    codes = sorted(set(codes))
    if codes:
        print(json.dumps({"status": "invalid", "issue_count": len(codes), "issue_codes": codes}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "status": "valid",
                "evaluation_group": framework["evaluation_group"],
                "framework_id": framework["framework_id"],
                "node_count": len(framework["semantic_framework"]["nodes"]),
                "receipt_bound": receipt is not None,
                "privacy": "semantic_and_draft_content_names_and_paths_omitted_from_stdout",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
