#!/usr/bin/env python3
"""Validate the binding and closure rules of a review bundle.

The command accepts three files in this order: the exact draft bytes, a UTF-8
JSON technical ledger, and the exact reader-report bytes.  The ledger uses this
small bundle envelope::

    {
      "draft_sha256": "...",
      "report_sha256": "...",
      "runtime": {
        "skill_version": "1.2.0",
        "commit": "full or abbreviated git commit, or null with provenance",
        "commit_unavailable_reason": "required when commit is null",
        "source_ref": "required tag, URL, or ref when commit is null",
        "mode": "public_full_evidence",
        "precommit_possible": false,
        "loaded_resources": [{"path": "references/...", "sha256": "..."}]
      },
      "framework_timing": {
        "status": "retrospective_framework_first_pass",
        "draft_seen_before_framework": true,
        "causal_claim_allowed": false,
        "framework_artifact_ref": null
      },
      "web_trace": {
        "network_available": true,
        "search_count": 3,
        "opened_pages": ["https://example.invalid/source"],
        "stop_reason": "core claims reached a decision",
        "skip_reason": "required only when network_available is false"
      },
      "publication_status": "ready|revise|hold",
      "claim_register": [
        {
          "claim_id": "CLM-001",
          "source_anchor": "exact draft text",
          "importance": "core|important|supporting",
          "requires_external_evidence": true,
          "evidence_status": "supported",
          "evidence_refs": ["EV-001"]
        }
      ],
      "discovery_hits": [],
      "verification_actions": [
        {"action_id": "ACT-001", "issue_refs": ["ISS-001"], "evidence_refs": ["EV-001"]}
      ],
      "closure_decisions": [
        {"closure_id": "CLS-001", "issue_refs": ["ISS-001"], "evidence_refs": ["EV-001"]}
      ],
      "evidence_records": [
        {
          "evidence_id": "EV-001",
          "acquisition": "opened_page",
          "url": "https://example.invalid/source",
          "locator": "section/table/page locator",
          "fit": {"entity": true, "time": true, "unit": "not_applicable", "scope": true},
          "fit_explanation": {"unit": "the claim has no numeric unit"}
        }
      ],
      "issues": [
        {
          "issue_id": "ISS-001",
          "risk_level": "R1|R2|R3",
          "source_anchor": "exact text copied from the draft",
          "workflow_state": "closed|...",
          "verification_status": "supported|...",
          "closure_evidence_refs": ["EV-001"],
          "verification_action_refs": ["ACT-001"],
          "closure_decision_ref": "CLS-001"
        }
      ],
      "new_viewpoints": [
        {
          "actor": "...", "decision": "...", "mechanism": "...",
          "evidence_refs": ["EV-001"], "boundary": "...",
          "falsifier": "..."
        }
      ]
    }

Hashes are calculated over the raw file bytes.  Draft anchors deliberately use
exact substring matching: normalization or a paraphrase is not accepted.  CLI
output contains only aggregate counts and stable error codes, never source text,
object identifiers, URLs, or local paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import urlparse

import validate_user_report


ROOT = Path(__file__).resolve().parents[1]
PUBLICATION_STATUSES = {"ready", "revise", "hold"}
PUBLICATION_STATUS_TEXT = {
    "ready": "可以发布",
    "revise": "修改后发布",
    "hold": "暂缓发布",
}
RISK_LEVELS = {"R1", "R2", "R3"}
WORKFLOW_STATES = {
    "candidate",
    "atomized",
    "triaged",
    "routed",
    "discovering",
    "verifying",
    "decision_ready",
    "human_review",
    "closed",
    "blocked",
    "reopened",
}
VERIFICATION_STATUSES = {
    "pending",
    "unverified",
    "verified",
    "supported",
    "partially_supported",
    "derived",
    "contradicted",
    "conflicting",
    "stale",
    "missing",
    "not_verifiable",
}
RESOLVED_VERIFICATION_STATUSES = {
    "verified",
    "supported",
    "partially_supported",
    "derived",
    "contradicted",
    "conflicting",
    "stale",
}
CLOSABLE_VERIFICATION_STATUSES = {
    "verified",
    "supported",
    "partially_supported",
    "derived",
    "contradicted",
    "stale",
}
CLAIM_EVIDENCE_STATUSES = {
    "supported",
    "partially_supported",
    "derived",
    "contradicted",
    "conflicting",
    "stale",
    "missing",
    "not_verifiable",
}
CLAIM_IMPORTANCE_LEVELS = {"core", "important", "supporting"}
SEARCH_ONLY_ACQUISITIONS = {
    "search_snippet",
    "search_summary",
    "search_result",
    "snippet",
}
ADMISSIBLE_ACQUISITIONS = {
    "opened_page",
    "supplied_source",
    "reproducible_calculation",
    "draft_internal",
}
LOCAL_INPUT_ACQUISITIONS = ADMISSIBLE_ACQUISITIONS - {"opened_page"}
FIT_DIMENSIONS = ("entity", "time", "unit", "scope")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
REQUIRED_RUNTIME_RESOURCES = {
    "SKILL.md",
    "VERSION",
    "references/detection-resolution-pipeline.md",
    "references/public-full-evidence-workflow.md",
    "references/public-industrial-park-priors.md",
    "references/industrial-park-lens.md",
    "references/output-contract.md",
    "references/user-facing-report.md",
    "references/verification-routing.md",
}
REQUIRED_LEDGER_LISTS = (
    "claim_register",
    "discovery_hits",
    "verification_actions",
    "closure_decisions",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(_nonempty_string(item) for item in value):
        return None
    return value


def _http_url(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _resource_path(value: Any) -> Path | None:
    """Resolve a portable repository-relative POSIX path without allowing escape."""

    if not _nonempty_string(value):
        return None
    portable = PurePosixPath(value)
    if portable.is_absolute() or ".." in portable.parts or portable.as_posix() != value:
        return None
    candidate = (ROOT / Path(*portable.parts)).resolve()
    try:
        candidate.relative_to(ROOT.resolve())
    except ValueError:
        return None
    return candidate


def _validate_runtime_receipt(ledger: dict[str, Any]) -> list[str]:
    """Verify the public full-evidence runtime receipt against installed files."""

    errors: list[str] = []
    runtime = ledger.get("runtime")
    if not isinstance(runtime, dict):
        return ["RUNTIME_RECEIPT_MISSING"]

    skill_version = runtime.get("skill_version")
    if not _nonempty_string(skill_version) or not SEMVER_RE.fullmatch(skill_version.strip()):
        errors.append("RUNTIME_SKILL_VERSION_INVALID")
    else:
        version_path = ROOT / "VERSION"
        try:
            installed_version = version_path.read_text(encoding="utf-8-sig").strip()
        except (OSError, UnicodeError):
            errors.append("RUNTIME_VERSION_FILE_UNREADABLE")
        else:
            if skill_version.strip() != installed_version:
                errors.append("RUNTIME_SKILL_VERSION_MISMATCH")

    commit = runtime.get("commit")
    if commit is None:
        if not _nonempty_string(runtime.get("commit_unavailable_reason")):
            errors.append("RUNTIME_COMMIT_UNAVAILABLE_REASON_MISSING")
        if not _nonempty_string(runtime.get("source_ref")):
            errors.append("RUNTIME_SOURCE_REF_MISSING")
    elif not _nonempty_string(commit) or not COMMIT_RE.fullmatch(commit.strip()):
        errors.append("RUNTIME_COMMIT_INVALID")
    if runtime.get("mode") != "public_full_evidence":
        errors.append("FULL_EVIDENCE_MODE_NOT_RECORDED")
    if not isinstance(runtime.get("precommit_possible"), bool):
        errors.append("PRECOMMIT_POSSIBLE_NOT_BOOLEAN")

    loaded_resources = runtime.get("loaded_resources")
    if not isinstance(loaded_resources, list):
        return errors + ["LOADED_RESOURCES_NOT_LIST", "REQUIRED_RUNTIME_RESOURCE_NOT_LOADED"]

    seen_paths: set[str] = set()
    valid_loaded_paths: set[str] = set()
    for item in loaded_resources:
        if not isinstance(item, dict):
            errors.append("LOADED_RESOURCE_NOT_OBJECT")
            continue
        portable_path = item.get("path")
        target = _resource_path(portable_path)
        if target is None:
            errors.append("LOADED_RESOURCE_PATH_INVALID")
            continue
        if portable_path in seen_paths:
            errors.append("DUPLICATE_LOADED_RESOURCE_PATH")
            continue
        seen_paths.add(portable_path)

        claimed_hash = item.get("sha256")
        if not _nonempty_string(claimed_hash) or not re.fullmatch(r"[0-9a-fA-F]{64}", claimed_hash.strip()):
            errors.append("LOADED_RESOURCE_SHA256_INVALID")
            continue
        try:
            actual_hash = _sha256(target.read_bytes())
        except OSError:
            errors.append("LOADED_RESOURCE_UNREADABLE")
            continue
        if claimed_hash.casefold() != actual_hash:
            errors.append("LOADED_RESOURCE_SHA256_MISMATCH")
            continue
        valid_loaded_paths.add(portable_path)

    if not REQUIRED_RUNTIME_RESOURCES.issubset(valid_loaded_paths):
        errors.append("REQUIRED_RUNTIME_RESOURCE_NOT_LOADED")
    return errors


def _validate_framework_timing(ledger: dict[str, Any]) -> list[str]:
    """Cross-check framework chronology with the runtime precommit receipt."""

    errors: list[str] = []
    timing = ledger.get("framework_timing")
    if not isinstance(timing, dict):
        return ["FRAMEWORK_TIMING_MISSING"]

    status = timing.get("status")
    if status not in {"strict_precommit", "retrospective_framework_first_pass"}:
        errors.append("FRAMEWORK_TIMING_STATUS_INVALID")
    draft_seen = timing.get("draft_seen_before_framework")
    causal_allowed = timing.get("causal_claim_allowed")
    if not isinstance(draft_seen, bool):
        errors.append("DRAFT_SEEN_BEFORE_FRAMEWORK_NOT_BOOLEAN")
    if not isinstance(causal_allowed, bool):
        errors.append("CAUSAL_CLAIM_ALLOWED_NOT_BOOLEAN")

    artifact_ref = timing.get("framework_artifact_ref")
    if artifact_ref is not None and not _nonempty_string(artifact_ref):
        errors.append("FRAMEWORK_ARTIFACT_REF_INVALID")

    runtime = ledger.get("runtime")
    precommit_possible = runtime.get("precommit_possible") if isinstance(runtime, dict) else None
    if isinstance(precommit_possible, bool):
        if precommit_possible:
            if status != "strict_precommit" or draft_seen is not False or causal_allowed is not True:
                errors.append("STRICT_PRECOMMIT_FLAGS_INVALID")
            if not _nonempty_string(artifact_ref):
                errors.append("STRICT_PRECOMMIT_ARTIFACT_REF_MISSING")
        else:
            if (
                status != "retrospective_framework_first_pass"
                or draft_seen is not True
                or causal_allowed is not False
            ):
                errors.append("RETROSPECTIVE_FRAMEWORK_FLAGS_INVALID")
    return errors


def _validate_web_trace(ledger: dict[str, Any]) -> tuple[list[str], bool | None, set[str]]:
    """Validate the web-use receipt and return its opened-page URL set."""

    errors: list[str] = []
    web_trace = ledger.get("web_trace")
    if not isinstance(web_trace, dict):
        return ["WEB_TRACE_MISSING"], None, set()

    network_available = web_trace.get("network_available")
    if not isinstance(network_available, bool):
        errors.append("NETWORK_AVAILABLE_NOT_BOOLEAN")
        network_available = None

    search_count = web_trace.get("search_count")
    if isinstance(search_count, bool) or not isinstance(search_count, int) or search_count < 0:
        errors.append("SEARCH_COUNT_INVALID")

    opened_pages = web_trace.get("opened_pages")
    opened_urls: set[str] = set()
    if not isinstance(opened_pages, list):
        errors.append("OPENED_PAGES_NOT_LIST")
        opened_pages = []
    for url in opened_pages:
        if not _http_url(url):
            errors.append("OPENED_PAGE_URL_INVALID")
            continue
        if url in opened_urls:
            errors.append("DUPLICATE_OPENED_PAGE_URL")
            continue
        opened_urls.add(url)

    if not _nonempty_string(web_trace.get("stop_reason")):
        errors.append("WEB_STOP_REASON_MISSING")
    if network_available is False:
        if opened_pages:
            errors.append("OFFLINE_OPENED_PAGES_NOT_EMPTY")
        if not _nonempty_string(web_trace.get("skip_reason")):
            errors.append("OFFLINE_SKIP_REASON_MISSING")
    return errors, network_available, opened_urls


def _evidence_is_admissible(
    record: dict[str, Any], network_available: bool | None, opened_page_urls: set[str]
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    acquisition = str(record.get("acquisition", "")).strip().casefold()
    if acquisition in SEARCH_ONLY_ACQUISITIONS:
        errors.append("SEARCH_SUMMARY_NOT_ADMISSIBLE_AS_EVIDENCE")
    elif acquisition not in ADMISSIBLE_ACQUISITIONS:
        errors.append("EVIDENCE_ACQUISITION_INVALID")

    if not _nonempty_string(record.get("locator")):
        errors.append("EVIDENCE_LOCATOR_MISSING")

    if acquisition == "opened_page":
        evidence_url = record.get("url")
        if not _http_url(evidence_url):
            errors.append("OPENED_PAGE_EVIDENCE_URL_INVALID")
        elif network_available is True and evidence_url not in opened_page_urls:
            errors.append("OPENED_PAGE_EVIDENCE_NOT_IN_WEB_TRACE")
        elif network_available is False:
            errors.append("OPENED_PAGE_EVIDENCE_RECORDED_OFFLINE")
    elif acquisition in LOCAL_INPUT_ACQUISITIONS and not _string_list(record.get("input_refs")):
        errors.append("EVIDENCE_INPUT_REFS_MISSING")

    fit = record.get("fit")
    if not isinstance(fit, dict):
        errors.append("EVIDENCE_FIT_INVALID")
    else:
        explanations = record.get("fit_explanation")
        for dimension in FIT_DIMENSIONS:
            value = fit.get(dimension)
            if value is True:
                continue
            if value == "not_applicable":
                if not isinstance(explanations, dict) or not _nonempty_string(explanations.get(dimension)):
                    errors.append("EVIDENCE_FIT_NA_EXPLANATION_MISSING")
            else:
                errors.append("EVIDENCE_FIT_INVALID")
    return not errors, errors


def validate_bundle(draft_bytes: bytes, ledger: Any, report_bytes: bytes) -> dict[str, object]:
    """Return privacy-minimized validation results for one review bundle."""

    errors: list[str] = []
    try:
        draft_text = draft_bytes.decode("utf-8-sig")
    except UnicodeError:
        draft_text = ""
        errors.append("DRAFT_UTF8_ERROR")
    try:
        report_text = report_bytes.decode("utf-8-sig")
    except UnicodeError:
        report_text = ""
        errors.append("REPORT_UTF8_ERROR")

    if not isinstance(ledger, dict):
        return {
            "ok": False,
            "error_count": 1,
            "error_codes": ["LEDGER_ROOT_NOT_OBJECT"],
            "issue_count": 0,
            "evidence_count": 0,
            "new_viewpoint_count": 0,
            "report_error_count": 0,
        }

    if ledger.get("draft_sha256") != _sha256(draft_bytes):
        errors.append("DRAFT_SHA256_MISMATCH")
    if ledger.get("report_sha256") != _sha256(report_bytes):
        errors.append("REPORT_SHA256_MISMATCH")

    errors.extend(_validate_runtime_receipt(ledger))
    errors.extend(_validate_framework_timing(ledger))
    web_errors, network_available, opened_page_urls = _validate_web_trace(ledger)
    errors.extend(web_errors)

    collection_counts: dict[str, int] = {}
    collections: dict[str, list[Any]] = {}
    for field in REQUIRED_LEDGER_LISTS:
        value = ledger.get(field)
        if field not in ledger:
            errors.append(f"{field.upper()}_MISSING")
            collection_counts[field] = 0
            collections[field] = []
        elif not isinstance(value, list):
            errors.append(f"{field.upper()}_NOT_LIST")
            collection_counts[field] = 0
            collections[field] = []
        else:
            collection_counts[field] = len(value)
            collections[field] = value

    publication_status = ledger.get("publication_status")
    if publication_status not in PUBLICATION_STATUSES:
        errors.append("INVALID_PUBLICATION_STATUS")

    if "evidence_records" not in ledger:
        errors.append("EVIDENCE_RECORDS_MISSING")
    evidence_records = ledger.get("evidence_records", [])
    if not isinstance(evidence_records, list):
        errors.append("EVIDENCE_RECORDS_NOT_LIST")
        evidence_records = []
    evidence_by_id: dict[str, dict[str, Any]] = {}
    evidence_validity: dict[str, bool] = {}
    for record in evidence_records:
        if not isinstance(record, dict):
            errors.append("EVIDENCE_RECORD_NOT_OBJECT")
            continue
        evidence_id = record.get("evidence_id")
        if not _nonempty_string(evidence_id):
            errors.append("MISSING_EVIDENCE_ID")
            continue
        if evidence_id in evidence_by_id:
            errors.append("DUPLICATE_EVIDENCE_ID")
            continue
        evidence_by_id[evidence_id] = record
        admissible, evidence_errors = _evidence_is_admissible(
            record, network_available, opened_page_urls
        )
        evidence_validity[evidence_id] = admissible
        errors.extend(evidence_errors)

    claims = collections["claim_register"]
    if draft_text.strip() and not claims:
        errors.append("NONEMPTY_DRAFT_WITHOUT_CLAIM_REGISTER")
    seen_claim_ids: set[str] = set()
    unresolved_material_claim_count = 0
    for claim in claims:
        if not isinstance(claim, dict):
            errors.append("CLAIM_NOT_OBJECT")
            continue
        claim_id = claim.get("claim_id")
        if not _nonempty_string(claim_id):
            errors.append("MISSING_CLAIM_ID")
        elif claim_id in seen_claim_ids:
            errors.append("DUPLICATE_CLAIM_ID")
        else:
            seen_claim_ids.add(claim_id)

        anchor = claim.get("source_anchor")
        if not _nonempty_string(anchor):
            errors.append("CLAIM_SOURCE_ANCHOR_MISSING")
        elif anchor not in draft_text:
            errors.append("CLAIM_SOURCE_ANCHOR_NOT_FOUND")

        importance = claim.get("importance")
        if importance not in CLAIM_IMPORTANCE_LEVELS:
            errors.append("CLAIM_IMPORTANCE_INVALID")
        requires_external = claim.get("requires_external_evidence")
        if not isinstance(requires_external, bool):
            errors.append("CLAIM_EXTERNAL_EVIDENCE_FLAG_INVALID")

        evidence_status = claim.get("evidence_status")
        if evidence_status is not None and evidence_status not in CLAIM_EVIDENCE_STATUSES:
            errors.append("CLAIM_EVIDENCE_STATUS_INVALID")
        evidence_refs: list[str] = []
        if "evidence_refs" in claim:
            parsed_refs = _string_list(claim.get("evidence_refs"))
            if not parsed_refs:
                errors.append("CLAIM_EVIDENCE_REFS_INVALID")
            else:
                evidence_refs = parsed_refs
                for evidence_id in evidence_refs:
                    if evidence_id not in evidence_by_id:
                        errors.append("UNKNOWN_CLAIM_EVIDENCE_REF")
                    elif not evidence_validity.get(evidence_id, False):
                        errors.append("CLAIM_EVIDENCE_NOT_ADMISSIBLE")
        if evidence_status is not None and not evidence_refs:
            errors.append("CLAIM_EVIDENCE_STATUS_WITHOUT_REFS")
        if evidence_refs and evidence_status is None:
            errors.append("CLAIM_EVIDENCE_REFS_WITHOUT_STATUS")

        unresolved_reason = claim.get("unresolved_reason")
        next_action = claim.get("next_action")
        has_unresolved_reason = _nonempty_string(unresolved_reason)
        has_next_action = _nonempty_string(next_action)
        if has_unresolved_reason != has_next_action:
            errors.append("CLAIM_UNRESOLVED_PLAN_INCOMPLETE")
        has_evidence_route = (
            evidence_status in CLAIM_EVIDENCE_STATUSES
            and bool(evidence_refs)
            and all(
                evidence_id in evidence_by_id
                and evidence_validity.get(evidence_id, False)
                for evidence_id in evidence_refs
            )
        )
        has_unresolved_route = has_unresolved_reason and has_next_action
        if importance in {"core", "important"} and requires_external is True:
            if not has_evidence_route and not has_unresolved_route:
                errors.append("IMPORTANT_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN")
            elif not has_evidence_route and has_unresolved_route:
                unresolved_material_claim_count += 1

    if publication_status == "ready" and unresolved_material_claim_count:
        errors.append("MATERIAL_CLAIM_UNRESOLVED_WHILE_READY")

    if "issues" not in ledger:
        errors.append("ISSUES_MISSING")
    issues = ledger.get("issues", [])
    if not isinstance(issues, list):
        errors.append("ISSUES_NOT_LIST")
        issues = []
    issue_by_id: dict[str, dict[str, Any]] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            errors.append("ISSUE_NOT_OBJECT")
            continue

        issue_id = issue.get("issue_id")
        if not _nonempty_string(issue_id):
            errors.append("MISSING_ISSUE_ID")
        elif issue_id in issue_by_id:
            errors.append("DUPLICATE_ISSUE_ID")
        else:
            issue_by_id[issue_id] = issue

        risk_level = issue.get("risk_level")
        if risk_level not in RISK_LEVELS:
            errors.append("INVALID_RISK_LEVEL")

        anchor = issue.get("source_anchor")
        if not _nonempty_string(anchor):
            errors.append("SOURCE_ANCHOR_MISSING")
        elif anchor not in draft_text:
            errors.append("SOURCE_ANCHOR_NOT_FOUND")

        if issue.get("workflow_state") not in WORKFLOW_STATES:
            errors.append("INVALID_WORKFLOW_STATE")
        if issue.get("verification_status") not in VERIFICATION_STATUSES:
            errors.append("INVALID_VERIFICATION_STATUS")

    def index_trace_objects(
        records: list[Any], id_field: str, prefix: str
    ) -> tuple[dict[str, dict[str, Any]], dict[str, bool]]:
        indexed: dict[str, dict[str, Any]] = {}
        validity: dict[str, bool] = {}
        for record in records:
            valid = True
            if not isinstance(record, dict):
                errors.append(f"{prefix}_NOT_OBJECT")
                continue
            object_id = record.get(id_field)
            if not _nonempty_string(object_id):
                errors.append(f"MISSING_{prefix}_ID")
                continue
            if object_id in indexed:
                errors.append(f"DUPLICATE_{prefix}_ID")
                continue
            indexed[object_id] = record

            issue_refs = _string_list(record.get("issue_refs"))
            if not issue_refs:
                errors.append(f"{prefix}_ISSUE_REFS_INVALID")
                valid = False
            else:
                for referenced_issue_id in issue_refs:
                    if referenced_issue_id not in issue_by_id:
                        errors.append(f"UNKNOWN_{prefix}_ISSUE_REF")
                        valid = False

            evidence_refs = _string_list(record.get("evidence_refs"))
            if not evidence_refs:
                errors.append(f"{prefix}_EVIDENCE_REFS_INVALID")
                valid = False
            else:
                for evidence_id in evidence_refs:
                    if evidence_id not in evidence_by_id:
                        errors.append(f"UNKNOWN_{prefix}_EVIDENCE_REF")
                        valid = False
                    elif not evidence_validity.get(evidence_id, False):
                        errors.append(f"{prefix}_EVIDENCE_NOT_ADMISSIBLE")
                        valid = False
            validity[object_id] = valid
        return indexed, validity

    verification_actions, verification_action_validity = index_trace_objects(
        collections["verification_actions"], "action_id", "VERIFICATION_ACTION"
    )
    closure_decisions, closure_decision_validity = index_trace_objects(
        collections["closure_decisions"], "closure_id", "CLOSURE_DECISION"
    )

    unresolved_high_risk_count = 0
    unresolved_material_risk_count = 0
    resolved_issue_count = 0
    for issue in issues:
        if not isinstance(issue, dict):
            continue

        issue_id = issue.get("issue_id")
        risk_level = issue.get("risk_level")
        workflow_state = issue.get("workflow_state")
        verification_status = issue.get("verification_status")
        needs_evidence = (
            workflow_state == "closed"
            or verification_status in RESOLVED_VERIFICATION_STATUSES
        )

        closure_refs: list[str] = []
        closure_evidence_valid = not needs_evidence
        if needs_evidence:
            parsed_refs = _string_list(issue.get("closure_evidence_refs"))
            if not parsed_refs:
                errors.append("RESOLVED_ISSUE_WITHOUT_CLOSURE_EVIDENCE")
                closure_evidence_valid = False
            else:
                closure_refs = parsed_refs
                closure_evidence_valid = True
                for evidence_id in closure_refs:
                    record = evidence_by_id.get(evidence_id)
                    if record is None:
                        errors.append("UNKNOWN_CLOSURE_EVIDENCE_REF")
                        closure_evidence_valid = False
                        continue
                    if not evidence_validity.get(evidence_id, False):
                        errors.append("CLOSURE_EVIDENCE_NOT_ADMISSIBLE")
                        closure_evidence_valid = False
                    if str(record.get("acquisition", "")).strip().casefold() in SEARCH_ONLY_ACQUISITIONS:
                        errors.append("SEARCH_SUMMARY_USED_FOR_CLOSURE")
                    if (
                        workflow_state == "closed"
                        and network_available is False
                        and str(record.get("acquisition", "")).strip().casefold() == "opened_page"
                    ):
                        errors.append("EXTERNAL_ISSUE_CLOSED_OFFLINE")
                        closure_evidence_valid = False

        trace_valid = True
        if workflow_state == "closed":
            if verification_status not in CLOSABLE_VERIFICATION_STATUSES:
                errors.append("CLOSED_ISSUE_VERIFICATION_STATUS_UNRESOLVED")
                trace_valid = False

            action_refs = _string_list(issue.get("verification_action_refs"))
            if not action_refs:
                errors.append("CLOSED_ISSUE_WITHOUT_VERIFICATION_ACTION")
                trace_valid = False
            else:
                for action_id in action_refs:
                    action = verification_actions.get(action_id)
                    if action is None:
                        errors.append("UNKNOWN_ISSUE_VERIFICATION_ACTION_REF")
                        trace_valid = False
                        continue
                    if not verification_action_validity.get(action_id, False):
                        errors.append("ISSUE_VERIFICATION_ACTION_INVALID")
                        trace_valid = False
                    if issue_id not in (action.get("issue_refs") or []):
                        errors.append("VERIFICATION_ACTION_DOES_NOT_REFERENCE_ISSUE")
                        trace_valid = False
                    if not set(action.get("evidence_refs") or []).issubset(set(closure_refs)):
                        errors.append("VERIFICATION_ACTION_EVIDENCE_NOT_IN_CLOSURE")
                        trace_valid = False

            decision_id = issue.get("closure_decision_ref")
            if not _nonempty_string(decision_id):
                errors.append("CLOSED_ISSUE_WITHOUT_CLOSURE_DECISION")
                trace_valid = False
            else:
                decision = closure_decisions.get(decision_id)
                if decision is None:
                    errors.append("UNKNOWN_ISSUE_CLOSURE_DECISION_REF")
                    trace_valid = False
                else:
                    if not closure_decision_validity.get(decision_id, False):
                        errors.append("ISSUE_CLOSURE_DECISION_INVALID")
                        trace_valid = False
                    if issue_id not in (decision.get("issue_refs") or []):
                        errors.append("CLOSURE_DECISION_DOES_NOT_REFERENCE_ISSUE")
                        trace_valid = False
                    if not set(decision.get("evidence_refs") or []).issubset(set(closure_refs)):
                        errors.append("CLOSURE_DECISION_EVIDENCE_NOT_IN_CLOSURE")
                        trace_valid = False

        closure_valid = (
            workflow_state == "closed"
            and verification_status in CLOSABLE_VERIFICATION_STATUSES
            and closure_evidence_valid
            and trace_valid
        )
        if closure_valid:
            resolved_issue_count += 1
        if risk_level == "R3" and not closure_valid:
            unresolved_high_risk_count += 1
        if risk_level in {"R2", "R3"} and not closure_valid:
            unresolved_material_risk_count += 1

    if publication_status == "ready" and unresolved_material_risk_count:
        errors.append("R2_R3_UNRESOLVED_WHILE_READY")

    if "new_viewpoints" not in ledger:
        errors.append("NEW_VIEWPOINTS_MISSING")
    viewpoints = ledger.get("new_viewpoints", [])
    if not isinstance(viewpoints, list):
        errors.append("NEW_VIEWPOINTS_NOT_LIST")
        viewpoints = []
    if len(viewpoints) > 2:
        errors.append("TOO_MANY_NEW_VIEWPOINTS")
    for viewpoint in viewpoints:
        if not isinstance(viewpoint, dict):
            errors.append("NEW_VIEWPOINT_NOT_OBJECT")
            continue
        for field in ("actor", "decision", "mechanism", "boundary", "falsifier"):
            if not _nonempty_string(viewpoint.get(field)):
                errors.append(f"NEW_VIEWPOINT_{field.upper()}_MISSING")
        refs = _string_list(viewpoint.get("evidence_refs"))
        if not refs:
            errors.append("NEW_VIEWPOINT_EVIDENCE_REFS_MISSING")
        else:
            for evidence_id in refs:
                record = evidence_by_id.get(evidence_id)
                if record is None:
                    errors.append("UNKNOWN_NEW_VIEWPOINT_EVIDENCE_REF")
                    continue
                admissible = evidence_validity.get(evidence_id, False)
                if not admissible:
                    errors.append("NEW_VIEWPOINT_EVIDENCE_NOT_ADMISSIBLE")
                if (
                    network_available is False
                    and str(record.get("acquisition", "")).strip().casefold() == "opened_page"
                ):
                    errors.append("NEW_VIEWPOINT_EXTERNAL_EVIDENCE_OFFLINE")

    report_result = validate_user_report.validate_report(report_text) if report_text else {
        "ok": False,
        "error_count": 1,
    }
    report_error_count = int(report_result.get("error_count", 0))
    if not report_result.get("ok"):
        errors.append("USER_REPORT_INVALID")
    if publication_status in PUBLICATION_STATUSES and report_text:
        reader_text = validate_user_report.split_reader_layer(report_text)
        positions = validate_user_report.section_positions(reader_text)
        conclusion = validate_user_report.section_body(reader_text, positions, "结论")
        if PUBLICATION_STATUS_TEXT[publication_status] not in conclusion:
            errors.append("PUBLICATION_STATUS_REPORT_MISMATCH")

    unique_errors = sorted(set(errors))
    return {
        "ok": not unique_errors,
        "error_count": len(unique_errors),
        "error_codes": unique_errors,
        "issue_count": len(issues),
        "resolved_issue_count": resolved_issue_count,
        "unresolved_high_risk_count": unresolved_high_risk_count,
        "unresolved_material_risk_count": unresolved_material_risk_count,
        "unresolved_material_claim_count": unresolved_material_claim_count,
        "evidence_count": len(evidence_records),
        "new_viewpoint_count": len(viewpoints),
        "claim_count": collection_counts["claim_register"],
        "discovery_hit_count": collection_counts["discovery_hits"],
        "verification_action_count": collection_counts["verification_actions"],
        "closure_decision_count": collection_counts["closure_decisions"],
        "opened_page_count": len(opened_page_urls),
        "network_available": network_available,
        "report_error_count": report_error_count,
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a draft, technical ledger, and user report bundle.")
    parser.add_argument("draft", type=Path, help="Exact UTF-8 draft file referenced by the ledger hash.")
    parser.add_argument("ledger", type=Path, help="UTF-8 JSON technical ledger.")
    parser.add_argument("report", type=Path, help="Exact UTF-8 Markdown user report referenced by the ledger hash.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print privacy-minimized JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        draft_bytes = args.draft.read_bytes()
        ledger = json.loads(args.ledger.read_text(encoding="utf-8-sig"))
        report_bytes = args.report.read_bytes()
    except json.JSONDecodeError:
        result: dict[str, object] = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["LEDGER_JSON_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2
    except OSError:
        result = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["BUNDLE_READ_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    result = validate_bundle(draft_bytes, ledger, report_bytes)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
