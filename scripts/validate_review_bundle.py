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
      "supplied_inputs": [
        {
          "input_id": "SUP-001", "sha256": "64 hex characters",
          "media_type": "text/html", "captured_at": "2026-09-14T08:30:00+08:00"
        }
      ],
      "claim_register": [
        {
          "claim_id": "CLM-001",
          "source_anchor": "exact draft text",
          "source_locator": "paragraph/table/page locator",
          "claim": "one atomic claim",
          "claim_type": "fact|calculation|inference|forecast|judgment|recommendation",
          "scope": "entity, place, period, population, unit",
          "importance": "core|important|supporting",
          "requires_external_evidence": true,
          "evidence_status": "supported",
          "evidence_refs": ["EV-001"],
          "calculation_evidence_ref": "required when status is derived",
          "derived_result": "required when status is derived"
        }
      ],
      "discovery_hits": [],
      "verification_actions": [
        {
          "action_id": "ACT-001", "issue_refs": ["ISS-001"],
          "evidence_refs": ["EV-001"], "action_type": "...", "result": "...",
          "performed_by": "...", "performed_at": "..."
        }
      ],
      "closure_decisions": [
        {
          "closure_id": "CLS-001", "issue_refs": ["ISS-001"],
          "evidence_refs": ["EV-001"], "predicate_results": {"...": true},
          "disposition": "evidence_added", "resolution_action": "...",
          "reviewer_role": "...", "reviewed_at": "...",
          "residual_limitations": "...", "reopen_conditions": ["..."],
          "human_reviewed": true
        }
      ],
      "evidence_records": [
        {
          "evidence_id": "EV-001",
          "acquisition": "opened_page",
          "url": "https://example.invalid/source",
          "locator": "section/table/page locator",
          "fit": {"entity": true, "time": true, "unit": "not_applicable", "scope": true},
          "fit_explanation": {"unit": "the claim has no numeric unit"},
          "fit_target_refs": ["claim:CLM-001", "viewpoint:VP-001"]
        }
      ],
      "issues": [
        {
          "issue_id": "ISS-001",
          "claim_refs": ["CLM-001"],
          "risk_level": "R1|R2|R3",
          "requires_external_evidence": true,
          "source_anchor": "exact text copied from the draft",
          "workflow_state": "closed|...",
          "verification_status": "supported|...",
          "publication_effect": "none|advisory|revise_before_publish|block_publication",
          "closure_evidence_refs": ["EV-001"],
          "verification_action_refs": ["ACT-001"],
          "closure_decision_ref": "CLS-001",
          "reader_binding": {
            "item_index": 1, "location_excerpt": "...",
            "problem_excerpt": "...", "reason_excerpt": "...",
            "action_excerpt": "..."
          }
        }
      ],
      "new_viewpoints": [
        {
          "viewpoint_id": "VP-001",
          "actor": "...", "decision": "...", "mechanism": "...",
          "evidence_refs": ["EV-001"], "boundary": "...",
          "falsifier": "...", "reader_item_sha256": "64 hex characters"
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
import ast
import datetime as dt
import hashlib
import ipaddress
import json
import re
import sys
import unicodedata
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from pathlib import Path
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

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
    "derived",
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
READY_CLAIM_STATUSES = {"supported", "derived"}
NON_READY_CLAIM_STATUSES = CLAIM_EVIDENCE_STATUSES - READY_CLAIM_STATUSES
CLAIM_IMPORTANCE_LEVELS = {"core", "important", "supporting"}
CLAIM_TYPES = {
    "fact",
    "calculation",
    "inference",
    "forecast",
    "judgment",
    "recommendation",
}
ALWAYS_EXTERNAL_CLAIM_TYPES = {"fact", "calculation", "forecast"}
SEARCH_ONLY_ACQUISITIONS = {
    "search_snippet",
    "search_summary",
    "search_result",
    "snippet",
}
MAX_DECIMAL_ADJUSTED_EXPONENT = 10_000
MAX_EVIDENCE_RECORDS = 200
MAX_CALCULATION_LINEAGE_DEPTH = 64
ADMISSIBLE_ACQUISITIONS = {
    "opened_page",
    "supplied_source",
    "reproducible_calculation",
    "draft_internal",
}
EXTERNAL_ACQUISITIONS = {"opened_page", "supplied_source"}
LOCAL_INPUT_ACQUISITIONS = ADMISSIBLE_ACQUISITIONS - {"opened_page"}
CLOSURE_DISPOSITIONS = {
    "evidence_added",
    "false_positive",
    "narrowed",
    "revised",
    "deleted",
}
PUBLICATION_EFFECTS = {"none", "advisory", "revise_before_publish", "block_publication"}
FIT_DIMENSIONS = ("entity", "time", "unit", "scope")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
CALCULATION_OPERAND_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
SUPPLIED_INPUT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
DECIMAL_VALUE_RE = re.compile(
    r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?(?P<unit>%?)$"
)
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
    "supplied_inputs",
    "claim_register",
    "discovery_hits",
    "verification_actions",
    "closure_decisions",
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _substantive_string(value: Any, minimum_chars: int) -> bool:
    """Reject blank and one-token placeholder fields without judging semantics."""

    if not _nonempty_string(value):
        return False
    compact = re.sub(r"\s+", "", value.strip())
    lexical_count = sum(
        1 for character in compact if unicodedata.category(character)[0] in {"L", "N"}
    )
    return len(compact) >= minimum_chars and lexical_count >= min(2, minimum_chars)


OBVIOUS_NEGATION_PREFIX_RE = re.compile(
    r"(?:无需|无须|不必|不需要|并不存在|不存在|并非|不是|禁止|不得|不可|"
    r"不会|不应|不宜|不影响|不)\s*[^，。；;：:\n]{0,8}$"
)


def _contains_unnegated_excerpt(container: Any, excerpt: Any) -> bool:
    """Reject an exact binding when its displayed occurrence is plainly negated."""

    if not _nonempty_string(container) or not _nonempty_string(excerpt):
        return False
    start = 0
    while True:
        index = container.find(excerpt, start)
        if index < 0:
            return False
        prefix = container[max(0, index - 24) : index]
        if not OBVIOUS_NEGATION_PREFIX_RE.search(prefix):
            return True
        start = index + 1


def _iso_timestamp(value: Any) -> bool:
    """Require an ISO-8601 timestamp with an explicit UTC offset."""

    return _parse_timestamp(value) is not None


def _parse_timestamp(value: Any) -> dt.datetime | None:
    """Parse an ISO-8601 timestamp and normalize it only for comparisons."""

    if not _nonempty_string(value):
        return None
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _string_list(value: Any) -> list[str] | None:
    if not isinstance(value, list):
        return None
    if not all(_nonempty_string(item) for item in value):
        return None
    return value


def _nonempty_conditions(value: Any) -> bool:
    """Accept one condition or a non-empty list of explicit conditions."""

    if isinstance(value, str):
        return _substantive_string(value, 4)
    items = _string_list(value)
    return bool(items) and all(_substantive_string(item, 4) for item in items)


def _http_url(value: Any) -> bool:
    if not _nonempty_string(value):
        return False
    candidate = value.strip()
    if re.search(r"[\x00-\x20\x7f]", candidate) or re.search(
        r"%(?![0-9A-Fa-f]{2})", candidate
    ):
        return False
    try:
        parsed = urlparse(candidate)
        hostname = parsed.hostname
        port = parsed.port
    except (ValueError, UnicodeError):
        return False
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port is not None and not 1 <= port <= 65_535
    ):
        return False
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    try:
        ascii_host = hostname.rstrip(".").encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        return False
    if not ascii_host or len(ascii_host) > 253 or "." not in ascii_host:
        return False
    labels = ascii_host.split(".")
    return all(
        1 <= len(label) <= 63
        and re.fullmatch(r"[A-Za-z0-9-]+", label) is not None
        and not label.startswith("-")
        and not label.endswith("-")
        for label in labels
    )


def _search_result_url(value: Any) -> bool:
    """Identify common search-result and redirect pages that are not sources."""

    if not _http_url(value):
        return False
    try:
        parsed = urlparse(value.strip())
    except (ValueError, UnicodeError):
        return True
    host = (parsed.hostname or "").casefold().rstrip(".")
    try:
        host = host.encode("idna").decode("ascii")
    except UnicodeError:
        return True
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path
    for _ in range(3):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.casefold().rstrip("/") or "/"
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    search_query_keys = {"q", "query", "keyword", "keywords", "wd", "text", "p"}
    if path in {"/search", "/web", "/s"} and query_keys & search_query_keys:
        return True
    if (host.startswith("google.") or ".google." in host) and path in {
        "/search",
        "/url",
        "/scholar",
    }:
        return True
    if (host == "bing.com" or host.endswith(".bing.com")) and (
        path == "/search" or path.startswith("/ck/")
    ):
        return True
    if (host == "baidu.com" or host.endswith(".baidu.com")) and path in {"/s", "/link"}:
        return True
    if (host == "sogou.com" or host.endswith(".sogou.com")) and path in {"/web", "/link"}:
        return True
    if (host == "so.com" or host.endswith(".so.com")) and path in {"/s", "/link"}:
        return True
    if host.startswith("search.yahoo.") and path == "/search":
        return True
    if (host == "duckduckgo.com" or host.endswith(".duckduckgo.com")) and path in {
        "/",
        "/html",
        "/lite",
    }:
        return True
    if (host.startswith("yandex.") or ".yandex." in host) and path == "/search":
        return True
    if (host == "startpage.com" or host.endswith(".startpage.com")) and path == "/sp/search":
        return True
    if host == "search.brave.com" and path == "/search":
        return True
    if (host == "ecosia.org" or host.endswith(".ecosia.org")) and path == "/search":
        return True
    if (host == "qwant.com" or host.endswith(".qwant.com")) and path in {"/", "/search"}:
        return bool(query_keys & search_query_keys)
    return host.startswith("search.") and (
        path in {"/", "/search", "/web", "/s"} or bool(query_keys & search_query_keys)
    )


def _unsafe_input_ref(value: str) -> bool:
    """Reject local filesystem paths while allowing opaque stable references."""

    stripped = value.strip()
    if re.match(r"^[A-Za-z]:[\\/]", stripped):
        return True
    if (
        stripped.startswith(("/", "\\"))
        or stripped.casefold().startswith("file:")
        or "\\" in stripped
    ):
        return True
    return ".." in PurePosixPath(stripped).parts


def _stable_supplied_input_id(value: Any) -> bool:
    """Accept an opaque portable ID, never a path or URI."""

    return _nonempty_string(value) and SUPPLIED_INPUT_ID_RE.fullmatch(value.strip()) is not None


class _CalculationExpressionError(ValueError):
    """Raised when a calculation expression is outside the safe arithmetic grammar."""


class _DuplicateJSONKeyError(ValueError):
    """Raised when JSON object keys are ambiguous under last-value-wins parsing."""


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKeyError(key)
        result[key] = value
    return result


def _decimal_value(value: Any) -> tuple[Decimal, str] | None:
    """Parse a finite decimal scalar and preserve the optional percent marker."""

    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        candidate = str(value).strip()
    except ValueError:
        return None
    if len(candidate) > 256:
        return None
    match = DECIMAL_VALUE_RE.fullmatch(candidate)
    if match is None:
        return None
    try:
        parsed = Decimal(candidate.removesuffix("%"))
        adjusted_exponent = parsed.adjusted()
    except (InvalidOperation, DecimalException):
        return None
    if not parsed.is_finite() or abs(adjusted_exponent) > MAX_DECIMAL_ADJUSTED_EXPONENT:
        return None
    return parsed, match.group("unit")


def _numeric_token_occurs(text: Any, token: Any) -> bool:
    """Match one complete numeric surface token, preserving a percent marker."""

    if not _nonempty_string(text) or not _nonempty_string(token):
        return False
    normalized_text = text.replace("％", "%")
    candidate = token.strip().replace("％", "%")
    if _decimal_value(candidate) is None:
        return False
    numeric_neighbors = r"0-9A-Za-z_.,%+\-"
    return bool(
        re.search(
            rf"(?<![{numeric_neighbors}]){re.escape(candidate)}(?![{numeric_neighbors}])",
            normalized_text,
        )
    )


def _evaluate_decimal_expression(
    expression: Any, operands: dict[str, Decimal]
) -> tuple[Decimal, set[str]]:
    """Evaluate a small arithmetic AST with Decimal values and no ``eval`` call."""

    if not _nonempty_string(expression) or len(expression) > 500:
        raise _CalculationExpressionError
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError, MemoryError, RecursionError) as exc:
        raise _CalculationExpressionError from exc
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise _CalculationExpressionError

    used_operands: set[str] = set()

    def evaluate(node: ast.AST) -> Decimal:
        if isinstance(node, ast.Expression):
            return evaluate(node.body)
        if isinstance(node, ast.Constant):
            # Python's AST has already rounded float literals.  Requiring an
            # integer constant keeps decimal/scientific values in named,
            # evidence-bound operands parsed directly by Decimal.
            if isinstance(node.value, bool) or not isinstance(node.value, int):
                raise _CalculationExpressionError
            parsed = _decimal_value(node.value)
            if parsed is None or parsed[1]:
                raise _CalculationExpressionError
            return parsed[0]
        if isinstance(node, ast.Name):
            if node.id not in operands:
                raise _CalculationExpressionError
            used_operands.add(node.id)
            return operands[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = evaluate(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            left = evaluate(node.left)
            right = evaluate(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                return left / right
            if isinstance(node.op, ast.Mod):
                return left % right
            if isinstance(node.op, ast.Pow):
                integral_exponent = right.to_integral_value()
                if right != integral_exponent or abs(integral_exponent) > 100:
                    raise _CalculationExpressionError
                return left**int(integral_exponent)
            raise _CalculationExpressionError
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            if node.func.id == "abs" and len(node.args) == 1:
                return abs(evaluate(node.args[0]))
            if node.func.id == "round" and len(node.args) in {1, 2}:
                value = evaluate(node.args[0])
                if len(node.args) == 1:
                    return Decimal(round(value))
                digits = evaluate(node.args[1])
                integral_digits = digits.to_integral_value()
                if digits != integral_digits or abs(integral_digits) > 28:
                    raise _CalculationExpressionError
                return round(value, int(integral_digits))
            raise _CalculationExpressionError
        raise _CalculationExpressionError

    try:
        with localcontext() as context:
            context.prec = 50
            result = evaluate(tree)
    except (
        ArithmeticError,
        DecimalException,
        OverflowError,
        ValueError,
        RecursionError,
    ) as exc:
        raise _CalculationExpressionError from exc
    if not result.is_finite():
        raise _CalculationExpressionError
    return result, used_operands


def _operand_influences_result(
    expression: Any,
    operands: dict[str, Decimal],
    operand_name: str,
    baseline_result: Decimal,
) -> bool:
    """Check that a named input has an observable effect on the formula result.

    Merely mentioning an externally sourced variable is not enough: expressions
    such as ``source * 0 + 80`` and ``source - source + 80`` otherwise create a
    false external lineage.  Several deterministic perturbations reduce false
    negatives from rounding, absolute values, and zero-valued inputs.
    """

    original = operands[operand_name]
    try:
        scale = max(abs(original), Decimal(1))
        candidates = (
            original + Decimal(1),
            original - Decimal(1),
            original + scale,
            original - scale,
            original * Decimal(2) + Decimal(1),
            original * Decimal("1.137") + Decimal("0.731"),
        )
    except (ArithmeticError, DecimalException, OverflowError, ValueError):
        return False
    for candidate in candidates:
        if candidate == original:
            continue
        perturbed = dict(operands)
        perturbed[operand_name] = candidate
        try:
            result, _ = _evaluate_decimal_expression(expression, perturbed)
        except _CalculationExpressionError:
            continue
        if result != baseline_result:
            return True
    return False


def _resource_path(value: Any) -> Path | None:
    """Resolve a portable repository-relative POSIX path without allowing escape."""

    if not _nonempty_string(value):
        return None
    try:
        portable = PurePosixPath(value)
        if portable.is_absolute() or ".." in portable.parts or portable.as_posix() != value:
            return None
        candidate = (ROOT / Path(*portable.parts)).resolve()
    except (OSError, ValueError):
        return None
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
        except (OSError, ValueError):
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
    if not isinstance(status, str) or status not in {
        "strict_precommit",
        "retrospective_framework_first_pass",
    }:
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

    if not _substantive_string(web_trace.get("stop_reason"), 4):
        errors.append("WEB_STOP_REASON_MISSING")
    if network_available is False:
        if opened_pages:
            errors.append("OFFLINE_OPENED_PAGES_NOT_EMPTY")
        if not _substantive_string(web_trace.get("skip_reason"), 4):
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

    if not _substantive_string(record.get("locator"), 2):
        errors.append("EVIDENCE_LOCATOR_MISSING")

    if acquisition == "opened_page":
        evidence_url = record.get("url")
        if not _http_url(evidence_url):
            errors.append("OPENED_PAGE_EVIDENCE_URL_INVALID")
        elif _search_result_url(evidence_url):
            errors.append("SEARCH_RESULT_URL_NOT_ADMISSIBLE")
        elif network_available is True and evidence_url not in opened_page_urls:
            errors.append("OPENED_PAGE_EVIDENCE_NOT_IN_WEB_TRACE")
        elif network_available is False:
            errors.append("OPENED_PAGE_EVIDENCE_RECORDED_OFFLINE")
    elif acquisition in LOCAL_INPUT_ACQUISITIONS:
        input_refs = _string_list(record.get("input_refs"))
        if not input_refs:
            errors.append("EVIDENCE_INPUT_REFS_MISSING")
        elif any(_unsafe_input_ref(input_ref) for input_ref in input_refs):
            errors.append("EVIDENCE_INPUT_REF_NOT_PORTABLE")

    if acquisition == "reproducible_calculation":
        if not _substantive_string(record.get("calculation_expression"), 3):
            errors.append("CALCULATION_EXPRESSION_MISSING")
        calculation_result = record.get("calculation_result")
        if (
            calculation_result is None
            or isinstance(calculation_result, (bool, list, dict))
            or (isinstance(calculation_result, str) and not calculation_result.strip())
        ):
            errors.append("CALCULATION_RESULT_MISSING")

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
                if not isinstance(explanations, dict) or not _substantive_string(
                    explanations.get(dimension), 4
                ):
                    errors.append("EVIDENCE_FIT_NA_EXPLANATION_MISSING")
            else:
                errors.append("EVIDENCE_FIT_INVALID")
    return not errors, errors


def validate_bundle(
    draft_bytes: bytes,
    ledger: Any,
    report_bytes: bytes,
    supplied_input_bytes: dict[str, bytes] | None = None,
) -> dict[str, object]:
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
    if not isinstance(publication_status, str) or publication_status not in PUBLICATION_STATUSES:
        errors.append("INVALID_PUBLICATION_STATUS")

    supplied_input_by_id: dict[str, dict[str, Any]] = {}
    supplied_input_validity: dict[str, bool] = {}
    supplied_input_capture_times: dict[str, dt.datetime] = {}
    supplied_input_bytes = supplied_input_bytes or {}
    for supplied_input in collections["supplied_inputs"]:
        if not isinstance(supplied_input, dict):
            errors.append("SUPPLIED_INPUT_NOT_OBJECT")
            continue
        input_id = supplied_input.get("input_id")
        if not _nonempty_string(input_id):
            errors.append("MISSING_SUPPLIED_INPUT_ID")
            continue
        if not _stable_supplied_input_id(input_id):
            errors.append("SUPPLIED_INPUT_ID_UNSAFE")
            continue
        if input_id in supplied_input_by_id:
            errors.append("DUPLICATE_SUPPLIED_INPUT_ID")
            continue
        supplied_input_by_id[input_id] = supplied_input
        supplied_input_validity[input_id] = True
        claimed_hash = supplied_input.get("sha256")
        if not _nonempty_string(claimed_hash) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", claimed_hash.strip()
        ):
            errors.append("SUPPLIED_INPUT_SHA256_INVALID")
            supplied_input_validity[input_id] = False
        if not _substantive_string(supplied_input.get("media_type"), 3):
            errors.append("SUPPLIED_INPUT_MEDIA_TYPE_INVALID")
            supplied_input_validity[input_id] = False
        captured_at = _parse_timestamp(supplied_input.get("captured_at"))
        if captured_at is None:
            errors.append("SUPPLIED_INPUT_CAPTURED_AT_INVALID")
            supplied_input_validity[input_id] = False
        else:
            supplied_input_capture_times[input_id] = captured_at
        supplied_bytes = supplied_input_bytes.get(input_id)
        if not isinstance(supplied_bytes, bytes):
            errors.append("SUPPLIED_INPUT_BYTES_NOT_VERIFIED")
            supplied_input_validity[input_id] = False
        elif _nonempty_string(claimed_hash) and re.fullmatch(
            r"[0-9a-fA-F]{64}", claimed_hash.strip()
        ) and _sha256(supplied_bytes) != claimed_hash.strip().casefold():
            errors.append("SUPPLIED_INPUT_SHA256_MISMATCH")
            supplied_input_validity[input_id] = False

    if any(input_id not in supplied_input_by_id for input_id in supplied_input_bytes):
        errors.append("UNDECLARED_SUPPLIED_INPUT_BYTES")

    if "evidence_records" not in ledger:
        errors.append("EVIDENCE_RECORDS_MISSING")
    evidence_records = ledger.get("evidence_records", [])
    if not isinstance(evidence_records, list):
        errors.append("EVIDENCE_RECORDS_NOT_LIST")
        evidence_records = []
    elif len(evidence_records) > MAX_EVIDENCE_RECORDS:
        errors.append("TOO_MANY_EVIDENCE_RECORDS")
        evidence_records = evidence_records[:MAX_EVIDENCE_RECORDS]
    evidence_by_id: dict[str, dict[str, Any]] = {}
    evidence_fit_targets: dict[str, set[str]] = {}
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
        if "fit_target_refs" not in record:
            errors.append("EVIDENCE_FIT_TARGET_REFS_MISSING")
            evidence_fit_targets[evidence_id] = set()
            continue
        target_refs = _string_list(record.get("fit_target_refs"))
        if target_refs is None:
            errors.append("EVIDENCE_FIT_TARGET_REFS_NOT_LIST")
            evidence_fit_targets[evidence_id] = set()
        elif not target_refs:
            errors.append("EVIDENCE_FIT_TARGET_REFS_EMPTY")
            evidence_fit_targets[evidence_id] = set()
        else:
            targets: set[str] = set()
            for target_ref in target_refs:
                if not re.fullmatch(r"(?:claim|viewpoint):[A-Za-z0-9][A-Za-z0-9_-]*", target_ref):
                    errors.append("EVIDENCE_FIT_TARGET_REF_INVALID")
                    continue
                if target_ref in targets:
                    errors.append("DUPLICATE_EVIDENCE_FIT_TARGET_REF")
                    continue
                targets.add(target_ref)
            evidence_fit_targets[evidence_id] = targets

    base_evidence_validity: dict[str, bool] = {}
    for evidence_id, record in evidence_by_id.items():
        admissible, evidence_errors = _evidence_is_admissible(
            record, network_available, opened_page_urls
        )
        acquisition = str(record.get("acquisition", "")).strip().casefold()
        if acquisition == "supplied_source":
            input_refs = _string_list(record.get("input_refs")) or []
            for input_ref in input_refs:
                if input_ref.casefold().startswith("draft:"):
                    evidence_errors.append("DRAFT_REF_USED_AS_SUPPLIED_SOURCE")
                if input_ref not in supplied_input_by_id:
                    evidence_errors.append("UNKNOWN_SUPPLIED_INPUT_REF")
                elif not supplied_input_validity.get(input_ref, False):
                    evidence_errors.append("SUPPLIED_INPUT_NOT_BYTE_VERIFIED")
        elif acquisition == "draft_internal":
            input_refs = _string_list(record.get("input_refs")) or []
            if input_refs and any(
                not input_ref.casefold().startswith("draft:") for input_ref in input_refs
            ):
                evidence_errors.append("DRAFT_INTERNAL_REF_INVALID")
        base_evidence_validity[evidence_id] = admissible and not evidence_errors
        errors.extend(evidence_errors)

    # Calculations are admissible evidence only when their inputs form an
    # auditable graph.  A free-form ``input_refs`` note is retained for local
    # provenance, but it cannot replace links to actual EvidenceItems.
    calculation_validity: dict[str, bool] = {}
    calculation_visiting: set[str] = set()

    def validate_calculation_lineage(evidence_id: str, depth: int = 0) -> bool:
        if depth >= MAX_CALCULATION_LINEAGE_DEPTH:
            errors.append("CALCULATION_LINEAGE_TOO_DEEP")
            return False
        if evidence_id in calculation_validity:
            return calculation_validity[evidence_id]
        if evidence_id in calculation_visiting:
            errors.append("CALCULATION_INPUT_EVIDENCE_CYCLE")
            return False

        record = evidence_by_id[evidence_id]
        if str(record.get("acquisition", "")).strip().casefold() != "reproducible_calculation":
            return base_evidence_validity.get(evidence_id, False)

        calculation_visiting.add(evidence_id)
        valid = base_evidence_validity.get(evidence_id, False)
        input_evidence_refs = _string_list(record.get("input_evidence_refs"))
        if not input_evidence_refs:
            errors.append("CALCULATION_INPUT_EVIDENCE_REFS_INVALID")
            valid = False
            input_evidence_refs = []

        raw_operands = record.get("calculation_operands")
        operand_values: dict[str, Decimal] = {}
        operand_units: dict[str, str] = {}
        operand_evidence_refs: dict[str, str] = {}
        seen_operand_names: set[str] = set()
        if not isinstance(raw_operands, list) or not raw_operands:
            errors.append("CALCULATION_OPERANDS_INVALID")
            valid = False
            raw_operands = []
        for operand in raw_operands:
            if not isinstance(operand, dict):
                errors.append("CALCULATION_OPERAND_NOT_OBJECT")
                valid = False
                continue
            name = operand.get("name")
            if not _nonempty_string(name) or not CALCULATION_OPERAND_NAME_RE.fullmatch(
                name.strip()
            ):
                errors.append("CALCULATION_OPERAND_NAME_INVALID")
                valid = False
                continue
            name = name.strip()
            if name in seen_operand_names:
                errors.append("DUPLICATE_CALCULATION_OPERAND_NAME")
                valid = False
                continue
            seen_operand_names.add(name)
            parsed_value = _decimal_value(operand.get("value"))
            if parsed_value is None:
                errors.append("CALCULATION_OPERAND_VALUE_INVALID")
                valid = False
                continue
            evidence_ref = operand.get("evidence_ref")
            if not _nonempty_string(evidence_ref):
                errors.append("CALCULATION_OPERAND_EVIDENCE_REF_INVALID")
                valid = False
                continue
            evidence_ref = evidence_ref.strip()
            operand_values[name] = parsed_value[0]
            operand_units[name] = parsed_value[1]
            operand_evidence_refs[name] = evidence_ref
            if evidence_ref not in input_evidence_refs:
                errors.append("CALCULATION_OPERAND_EVIDENCE_REF_NOT_INPUT")
                valid = False

        used_operands: set[str] = set()
        calculated_result: Decimal | None = None
        try:
            calculated_result, used_operands = _evaluate_decimal_expression(
                record.get("calculation_expression"), operand_values
            )
        except _CalculationExpressionError:
            errors.append("CALCULATION_EXPRESSION_INVALID")
            valid = False
        else:
            declared_result = _decimal_value(record.get("calculation_result"))
            if declared_result is None:
                errors.append("CALCULATION_RESULT_NOT_NUMERIC")
                valid = False
            elif calculated_result != declared_result[0]:
                errors.append("CALCULATION_RESULT_MISMATCH")
                valid = False
        if set(operand_values) - used_operands:
            errors.append("CALCULATION_OPERAND_UNUSED")
            valid = False
        if calculated_result is not None:
            for operand_name in used_operands:
                if operand_name in operand_values and not _operand_influences_result(
                    record.get("calculation_expression"),
                    operand_values,
                    operand_name,
                    calculated_result,
                ):
                    errors.append("CALCULATION_OPERAND_NOT_INFLUENTIAL")
                    valid = False

        operand_backed_inputs = {
            operand_evidence_refs[name]
            for name in used_operands
            if name in operand_evidence_refs
        }
        if any(input_id not in operand_backed_inputs for input_id in input_evidence_refs):
            errors.append("CALCULATION_INPUT_EVIDENCE_REF_WITHOUT_OPERAND")
            valid = False

        seen_input_refs: set[str] = set()
        has_external_input = False
        for input_evidence_id in input_evidence_refs:
            if input_evidence_id in seen_input_refs:
                errors.append("DUPLICATE_CALCULATION_INPUT_EVIDENCE_REF")
                valid = False
                continue
            seen_input_refs.add(input_evidence_id)
            if input_evidence_id == evidence_id:
                errors.append("CALCULATION_INPUT_EVIDENCE_REF_SELF")
                valid = False
                continue
            input_record = evidence_by_id.get(input_evidence_id)
            if input_record is None:
                errors.append("UNKNOWN_CALCULATION_INPUT_EVIDENCE_REF")
                valid = False
                continue
            if not base_evidence_validity.get(input_evidence_id, False):
                errors.append("CALCULATION_INPUT_EVIDENCE_NOT_ADMISSIBLE")
                valid = False
                continue
            input_acquisition = str(input_record.get("acquisition", "")).strip().casefold()
            if input_acquisition == "reproducible_calculation":
                if not validate_calculation_lineage(input_evidence_id, depth + 1):
                    errors.append("CALCULATION_INPUT_EVIDENCE_NOT_ADMISSIBLE")
                    valid = False
                else:
                    has_external_input = True
                upstream_result = _decimal_value(input_record.get("calculation_result"))
                for operand_name, operand_ref in operand_evidence_refs.items():
                    if operand_ref != input_evidence_id or operand_name not in used_operands:
                        continue
                    operand_result = (operand_values[operand_name], operand_units[operand_name])
                    if upstream_result is None or operand_result != upstream_result:
                        errors.append("CALCULATION_NESTED_OPERAND_VALUE_MISMATCH")
                        valid = False
            elif input_acquisition in EXTERNAL_ACQUISITIONS:
                has_external_input = True

        if not has_external_input:
            errors.append("CALCULATION_EXTERNAL_INPUT_MISSING")
            valid = False
        calculation_visiting.discard(evidence_id)
        calculation_validity[evidence_id] = valid
        return valid

    evidence_validity = dict(base_evidence_validity)
    for evidence_id, record in evidence_by_id.items():
        if str(record.get("acquisition", "")).strip().casefold() == "reproducible_calculation":
            evidence_validity[evidence_id] = validate_calculation_lineage(evidence_id)

    material_external_fit_cache: dict[tuple[str, str], bool] = {}
    material_external_fit_visiting: set[tuple[str, str]] = set()

    def has_material_external_fit(
        evidence_id: str, target_ref: str, depth: int = 0
    ) -> bool:
        """Require target-bound, scope-matched external lineage at every layer."""

        if depth >= MAX_CALCULATION_LINEAGE_DEPTH:
            errors.append("CALCULATION_LINEAGE_TOO_DEEP")
            return False
        cache_key = (evidence_id, target_ref)
        if cache_key in material_external_fit_cache:
            return material_external_fit_cache[cache_key]
        if cache_key in material_external_fit_visiting:
            return False
        record = evidence_by_id.get(evidence_id)
        if (
            record is None
            or not evidence_validity.get(evidence_id, False)
            or target_ref not in evidence_fit_targets.get(evidence_id, set())
        ):
            return False
        material_external_fit_visiting.add(cache_key)
        acquisition = str(record.get("acquisition", "")).strip().casefold()
        fit = record.get("fit")
        own_material_fit = isinstance(fit, dict) and all(
            fit.get(dimension) is True for dimension in ("entity", "time", "scope")
        ) and (
            fit.get("unit") is True
            or fit.get("unit") == "not_applicable"
        )
        if acquisition in EXTERNAL_ACQUISITIONS:
            result = own_material_fit
        elif acquisition == "reproducible_calculation" and own_material_fit:
            input_refs = _string_list(record.get("input_evidence_refs")) or []
            result = bool(input_refs) and all(
                has_material_external_fit(input_id, target_ref, depth + 1)
                for input_id in input_refs
            )
        else:
            result = False
        material_external_fit_visiting.remove(cache_key)
        material_external_fit_cache[cache_key] = result
        return result

    claims = collections["claim_register"]
    if draft_text.strip() and not claims:
        errors.append("NONEMPTY_DRAFT_WITHOUT_CLAIM_REGISTER")
    seen_claim_ids: set[str] = set()
    claim_by_id: dict[str, dict[str, Any]] = {}
    unresolved_material_claim_count = 0
    unresolved_external_claim_count = 0
    unresolved_claim_ids: set[str] = set()
    core_claim_count = 0
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
            claim_by_id[claim_id] = claim

        anchor = claim.get("source_anchor")
        if not _substantive_string(anchor, 4):
            errors.append("CLAIM_SOURCE_ANCHOR_MISSING")
        elif anchor not in draft_text:
            errors.append("CLAIM_SOURCE_ANCHOR_NOT_FOUND")

        if not _substantive_string(claim.get("claim"), 4):
            errors.append("CLAIM_TEXT_MISSING")
        if not _substantive_string(claim.get("source_locator"), 2):
            errors.append("CLAIM_SOURCE_LOCATOR_MISSING")
        if not _substantive_string(claim.get("scope"), 4):
            errors.append("CLAIM_SCOPE_MISSING")
        claim_type = claim.get("claim_type")
        if not isinstance(claim_type, str) or claim_type not in CLAIM_TYPES:
            errors.append("CLAIM_TYPE_INVALID")

        importance = claim.get("importance")
        if not isinstance(importance, str) or importance not in CLAIM_IMPORTANCE_LEVELS:
            errors.append("CLAIM_IMPORTANCE_INVALID")
        elif importance == "core":
            core_claim_count += 1
        requires_external = claim.get("requires_external_evidence")
        if not isinstance(requires_external, bool):
            errors.append("CLAIM_EXTERNAL_EVIDENCE_FLAG_INVALID")
        elif requires_external is False and (
            isinstance(claim_type, str)
            and claim_type in ALWAYS_EXTERNAL_CLAIM_TYPES
            or (
                isinstance(importance, str)
                and importance in {"core", "important"}
                and claim_type == "inference"
            )
        ):
            errors.append("CLAIM_EXTERNAL_FLAG_CONTRADICTS_TYPE")

        evidence_status = claim.get("evidence_status")
        if evidence_status is not None and (
            not isinstance(evidence_status, str)
            or evidence_status not in CLAIM_EVIDENCE_STATUSES
        ):
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
        has_unresolved_reason = _substantive_string(unresolved_reason, 4)
        has_next_action = _substantive_string(next_action, 4)
        if has_unresolved_reason != has_next_action:
            errors.append("CLAIM_UNRESOLVED_PLAN_INCOMPLETE")
        refs_fit_claim = bool(evidence_refs) and _nonempty_string(claim_id) and all(
            f"claim:{claim_id}" in evidence_fit_targets.get(evidence_id, set())
            for evidence_id in evidence_refs
        )
        if evidence_refs and _nonempty_string(claim_id) and not refs_fit_claim:
            errors.append("CLAIM_EVIDENCE_FIT_TARGET_MISSING")
        refs_are_valid = bool(evidence_refs) and refs_fit_claim and all(
            evidence_id in evidence_by_id
            and evidence_validity.get(evidence_id, False)
            for evidence_id in evidence_refs
        )
        has_valid_calculation = False
        calculation_evidence_ref = claim.get("calculation_evidence_ref")
        derived_result = claim.get("derived_result")
        if evidence_status == "derived":
            if not _nonempty_string(calculation_evidence_ref):
                errors.append("DERIVED_CLAIM_CALCULATION_REF_MISSING")
            elif calculation_evidence_ref not in evidence_refs:
                errors.append("DERIVED_CLAIM_CALCULATION_REF_NOT_CITED")
            else:
                calculation_record = evidence_by_id.get(calculation_evidence_ref)
                if (
                    calculation_record is None
                    or str(calculation_record.get("acquisition", "")).strip().casefold()
                    != "reproducible_calculation"
                    or not evidence_validity.get(calculation_evidence_ref, False)
                ):
                    errors.append("DERIVED_CLAIM_CALCULATION_REF_INVALID")
                else:
                    declared_value = _decimal_value(derived_result)
                    calculated_value = _decimal_value(
                        calculation_record.get("calculation_result")
                    )
                    result_matches = False
                    result_is_anchored = False
                    if declared_value is None:
                        errors.append("DERIVED_CLAIM_RESULT_INVALID")
                    else:
                        result_matches = (
                            calculated_value is not None
                            and declared_value == calculated_value
                        )
                        if not result_matches:
                            errors.append("DERIVED_CLAIM_RESULT_MISMATCH")
                        result_is_anchored = (
                            isinstance(derived_result, str)
                            and _numeric_token_occurs(anchor, derived_result)
                            and _numeric_token_occurs(claim.get("claim"), derived_result)
                        )
                        if not result_is_anchored:
                            errors.append("DERIVED_CLAIM_RESULT_NOT_ANCHORED")
                        if result_matches and result_is_anchored:
                            has_valid_calculation = refs_are_valid
            if not has_valid_calculation:
                errors.append("DERIVED_CLAIM_WITHOUT_VALID_CALCULATION")
        elif calculation_evidence_ref is not None or derived_result is not None:
            errors.append("DERIVED_BINDING_ON_NON_DERIVED_CLAIM")

        claim_target_ref = f"claim:{claim_id}" if _nonempty_string(claim_id) else ""
        has_external_evidence = refs_are_valid and bool(claim_target_ref) and any(
            has_material_external_fit(evidence_id, claim_target_ref)
            for evidence_id in evidence_refs
        )
        has_direct_external_evidence = refs_are_valid and any(
            str(evidence_by_id[evidence_id].get("acquisition", "")).strip().casefold()
            in EXTERNAL_ACQUISITIONS
            and bool(claim_target_ref)
            and has_material_external_fit(evidence_id, claim_target_ref)
            for evidence_id in evidence_refs
        )
        has_external_acquisition = refs_are_valid and any(
            str(evidence_by_id[evidence_id].get("acquisition", "")).strip().casefold()
            in EXTERNAL_ACQUISITIONS | {"reproducible_calculation"}
            for evidence_id in evidence_refs
        )
        ready_status_valid = (
            isinstance(evidence_status, str) and evidence_status in READY_CLAIM_STATUSES
        )
        if evidence_status == "derived" and not has_valid_calculation:
            ready_status_valid = False
        has_evidence_route = ready_status_valid and refs_are_valid
        if requires_external is True and has_evidence_route and not has_external_evidence:
            if has_external_acquisition:
                errors.append("EXTERNAL_CLAIM_EVIDENCE_SCOPE_FIT_INSUFFICIENT")
            else:
                errors.append("EXTERNAL_CLAIM_WITHOUT_EXTERNAL_EVIDENCE")
            has_evidence_route = False
        if (
            requires_external is True
            and evidence_status == "supported"
            and has_evidence_route
            and not has_direct_external_evidence
        ):
            errors.append("SUPPORTED_EXTERNAL_CLAIM_WITHOUT_DIRECT_EVIDENCE")
            has_evidence_route = False

        has_unresolved_route = has_unresolved_reason and has_next_action
        if requires_external is True:
            if not has_evidence_route:
                unresolved_external_claim_count += 1
                if _nonempty_string(claim_id):
                    unresolved_claim_ids.add(claim_id)
                if isinstance(importance, str) and importance in {"core", "important"}:
                    unresolved_material_claim_count += 1
            if not has_evidence_route and not has_unresolved_route:
                if isinstance(importance, str) and importance in {"core", "important"}:
                    errors.append("IMPORTANT_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN")
                else:
                    errors.append("SUPPORTING_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN")
        elif (
            isinstance(importance, str)
            and importance in {"core", "important"}
            and isinstance(evidence_status, str)
            and evidence_status in NON_READY_CLAIM_STATUSES
        ):
            unresolved_material_claim_count += 1
            if _nonempty_string(claim_id):
                unresolved_claim_ids.add(claim_id)
            if not has_unresolved_route:
                errors.append("MATERIAL_CLAIM_NEGATIVE_STATUS_WITHOUT_PLAN")

    if draft_text.strip() and core_claim_count == 0:
        errors.append("NONEMPTY_DRAFT_WITHOUT_CORE_CLAIM")

    if publication_status == "ready" and unresolved_material_claim_count:
        errors.append("MATERIAL_CLAIM_UNRESOLVED_WHILE_READY")
    if publication_status == "ready" and unresolved_external_claim_count:
        errors.append("EXTERNAL_CLAIM_UNRESOLVED_WHILE_READY")

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
        if not isinstance(risk_level, str) or risk_level not in RISK_LEVELS:
            errors.append("INVALID_RISK_LEVEL")

        anchor = issue.get("source_anchor")
        if not _substantive_string(anchor, 4):
            errors.append("SOURCE_ANCHOR_MISSING")
        elif anchor not in draft_text:
            errors.append("SOURCE_ANCHOR_NOT_FOUND")

        for field, minimum_chars in (
            ("source_locator", 2),
            ("problem", 4),
            ("reason", 4),
            ("suggested_action", 4),
        ):
            if not _substantive_string(issue.get(field), minimum_chars):
                errors.append(f"ISSUE_{field.upper()}_MISSING")

        workflow_state_value = issue.get("workflow_state")
        if not isinstance(workflow_state_value, str) or workflow_state_value not in WORKFLOW_STATES:
            errors.append("INVALID_WORKFLOW_STATE")
        verification_status_value = issue.get("verification_status")
        if (
            not isinstance(verification_status_value, str)
            or verification_status_value not in VERIFICATION_STATUSES
        ):
            errors.append("INVALID_VERIFICATION_STATUS")
        publication_effect_value = issue.get("publication_effect")
        if (
            not isinstance(publication_effect_value, str)
            or publication_effect_value not in PUBLICATION_EFFECTS
        ):
            errors.append("INVALID_PUBLICATION_EFFECT")

        claim_refs = _string_list(issue.get("claim_refs"))
        if not claim_refs:
            errors.append("ISSUE_CLAIM_REFS_INVALID")
        else:
            for claim_id in claim_refs:
                if claim_id not in claim_by_id:
                    errors.append("UNKNOWN_ISSUE_CLAIM_REF")
        if not isinstance(issue.get("requires_external_evidence"), bool):
            errors.append("ISSUE_EXTERNAL_EVIDENCE_FLAG_INVALID")
        elif issue.get("requires_external_evidence") is False and claim_refs and any(
            claim_by_id.get(claim_id, {}).get("requires_external_evidence") is True
            for claim_id in claim_refs
        ):
            errors.append("ISSUE_EXTERNAL_FLAG_CONTRADICTS_CLAIM")

    seen_discovery_hit_ids: set[str] = set()
    for hit in collections["discovery_hits"]:
        if not isinstance(hit, dict):
            errors.append("DISCOVERY_HIT_NOT_OBJECT")
            continue
        hit_id = hit.get("hit_id")
        if not _nonempty_string(hit_id):
            errors.append("MISSING_DISCOVERY_HIT_ID")
        elif hit_id in seen_discovery_hit_ids:
            errors.append("DUPLICATE_DISCOVERY_HIT_ID")
        else:
            seen_discovery_hit_ids.add(hit_id)
        issue_ref = hit.get("issue_id")
        if not _nonempty_string(issue_ref):
            errors.append("DISCOVERY_HIT_ISSUE_REF_INVALID")
        elif issue_ref not in issue_by_id:
            errors.append("UNKNOWN_DISCOVERY_HIT_ISSUE_REF")
        for field, minimum_chars in (
            ("channel", 2),
            ("candidate_locator", 4),
            ("role", 3),
            ("promotion_status", 4),
        ):
            if not _substantive_string(hit.get(field), minimum_chars):
                errors.append(f"DISCOVERY_HIT_{field.upper()}_MISSING")
        if not _iso_timestamp(hit.get("captured_at")):
            errors.append("DISCOVERY_HIT_CAPTURED_AT_INVALID")

    supplied_refs_cache: dict[str, set[str]] = {}

    def supplied_refs_for_evidence(
        evidence_id: str, visiting: set[str] | None = None
    ) -> set[str]:
        """Return every byte-verified supplied input in an evidence lineage."""

        if evidence_id in supplied_refs_cache:
            return supplied_refs_cache[evidence_id]
        visiting = set() if visiting is None else visiting
        if evidence_id in visiting or len(visiting) >= MAX_CALCULATION_LINEAGE_DEPTH:
            return set()
        record = evidence_by_id.get(evidence_id)
        if record is None:
            return set()
        next_visiting = visiting | {evidence_id}
        acquisition = str(record.get("acquisition", "")).strip().casefold()
        if acquisition == "supplied_source":
            result = {
                input_id
                for input_id in (_string_list(record.get("input_refs")) or [])
                if input_id in supplied_input_by_id
            }
        elif acquisition == "reproducible_calculation":
            result = set().union(
                *(
                    supplied_refs_for_evidence(input_id, next_visiting)
                    for input_id in (
                        _string_list(record.get("input_evidence_refs")) or []
                    )
                )
            )
        else:
            result = set()
        supplied_refs_cache[evidence_id] = result
        return result

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

            if prefix == "VERIFICATION_ACTION":
                for field, minimum_chars in (
                    ("action_type", 4),
                    ("result", 4),
                    ("performed_by", 3),
                ):
                    if not _substantive_string(record.get(field), minimum_chars):
                        errors.append(f"VERIFICATION_ACTION_{field.upper()}_MISSING")
                        valid = False
                performed_at = _parse_timestamp(record.get("performed_at"))
                if performed_at is None:
                    errors.append("VERIFICATION_ACTION_PERFORMED_AT_INVALID")
                    valid = False
                else:
                    for evidence_id in evidence_refs or []:
                        for input_id in supplied_refs_for_evidence(evidence_id):
                            captured_at = supplied_input_capture_times.get(input_id)
                            if captured_at is not None and performed_at < captured_at:
                                errors.append(
                                    "VERIFICATION_ACTION_BEFORE_SUPPLIED_INPUT_CAPTURE"
                                )
                                valid = False
            elif prefix == "CLOSURE_DECISION":
                predicate_results = record.get("predicate_results")
                if (
                    not isinstance(predicate_results, dict)
                    or not predicate_results
                    or any(not _nonempty_string(key) for key in predicate_results)
                    or any(value is not True for value in predicate_results.values())
                ):
                    errors.append("CLOSURE_DECISION_PREDICATE_RESULTS_INVALID")
                    valid = False
                disposition = record.get("disposition")
                if not isinstance(disposition, str) or disposition not in CLOSURE_DISPOSITIONS:
                    errors.append("CLOSURE_DECISION_DISPOSITION_INVALID")
                    valid = False
                for field, minimum_chars in (
                    ("resolution_action", 4),
                    ("reviewer_role", 3),
                    ("residual_limitations", 4),
                ):
                    if not _substantive_string(record.get(field), minimum_chars):
                        errors.append(f"CLOSURE_DECISION_{field.upper()}_MISSING")
                        valid = False
                reviewed_at = _parse_timestamp(record.get("reviewed_at"))
                if reviewed_at is None:
                    errors.append("CLOSURE_DECISION_REVIEWED_AT_INVALID")
                    valid = False
                else:
                    for evidence_id in evidence_refs or []:
                        for input_id in supplied_refs_for_evidence(evidence_id):
                            captured_at = supplied_input_capture_times.get(input_id)
                            if captured_at is not None and reviewed_at < captured_at:
                                errors.append(
                                    "CLOSURE_DECISION_BEFORE_SUPPLIED_INPUT_CAPTURE"
                                )
                                valid = False
                if not _nonempty_conditions(record.get("reopen_conditions")):
                    errors.append("CLOSURE_DECISION_REOPEN_CONDITIONS_INVALID")
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
    issue_closure_validity: dict[str, bool] = {}
    for issue in issues:
        if not isinstance(issue, dict):
            continue

        raw_issue_id = issue.get("issue_id")
        issue_id = raw_issue_id if _nonempty_string(raw_issue_id) else None
        risk_level = issue.get("risk_level")
        workflow_state = issue.get("workflow_state")
        verification_status = issue.get("verification_status")
        needs_evidence = workflow_state == "closed" or (
            isinstance(verification_status, str)
            and verification_status in RESOLVED_VERIFICATION_STATUSES
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
                    if claim_refs := _string_list(issue.get("claim_refs")):
                        if not any(
                            f"claim:{claim_id}" in evidence_fit_targets.get(evidence_id, set())
                            for claim_id in claim_refs
                        ):
                            errors.append("CLOSURE_EVIDENCE_FIT_TARGET_MISSING")
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

        issue_claim_refs = _string_list(issue.get("claim_refs")) or []
        if workflow_state == "closed":
            for claim_id in issue_claim_refs:
                target_ref = f"claim:{claim_id}"
                if not any(
                    target_ref in evidence_fit_targets.get(evidence_id, set())
                    for evidence_id in closure_refs
                ):
                    errors.append("CLOSURE_CLAIM_NOT_COVERED")
                    closure_evidence_valid = False
                if claim_by_id.get(claim_id, {}).get("requires_external_evidence") is True and not any(
                    has_material_external_fit(evidence_id, target_ref)
                    for evidence_id in closure_refs
                ):
                    errors.append("CLOSED_EXTERNAL_ISSUE_WITHOUT_MATCHED_EXTERNAL_EVIDENCE")
                    closure_evidence_valid = False
            if issue.get("requires_external_evidence") is True and not any(
                has_material_external_fit(evidence_id, f"claim:{claim_id}")
                for evidence_id in closure_refs
                for claim_id in issue_claim_refs
            ):
                errors.append("CLOSED_EXTERNAL_ISSUE_WITHOUT_MATCHED_EXTERNAL_EVIDENCE")
                closure_evidence_valid = False
        if (
            workflow_state == "closed"
            and verification_status == "derived"
            and (
                not issue_claim_refs
                or any(
                    claim_by_id.get(claim_id, {}).get("evidence_status") != "derived"
                    or claim_by_id.get(claim_id, {}).get("calculation_evidence_ref")
                    not in closure_refs
                    for claim_id in issue_claim_refs
                )
            )
        ):
            errors.append("CLOSED_DERIVED_ISSUE_CLAIM_BINDING_MISSING")
            errors.append("CLOSED_DERIVED_ISSUE_WITHOUT_VALID_CALCULATION")
            closure_evidence_valid = False

        trace_valid = True
        if workflow_state == "closed":
            if (
                not isinstance(verification_status, str)
                or verification_status not in CLOSABLE_VERIFICATION_STATUSES
            ):
                errors.append("CLOSED_ISSUE_VERIFICATION_STATUS_UNRESOLVED")
                trace_valid = False

            action_refs = _string_list(issue.get("verification_action_refs"))
            action_evidence_union: set[str] = set()
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
                    action_issue_refs = _string_list(action.get("issue_refs")) or []
                    if issue_id not in action_issue_refs:
                        errors.append("VERIFICATION_ACTION_DOES_NOT_REFERENCE_ISSUE")
                        trace_valid = False
                    action_evidence = set(_string_list(action.get("evidence_refs")) or [])
                    action_evidence_union.update(action_evidence)
                    if not action_evidence.issubset(set(closure_refs)):
                        errors.append("VERIFICATION_ACTION_EVIDENCE_NOT_IN_CLOSURE")
                        trace_valid = False
                if set(closure_refs) != action_evidence_union:
                    errors.append("VERIFICATION_ACTIONS_DO_NOT_COVER_CLOSURE_EVIDENCE")
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
                    if (
                        isinstance(risk_level, str)
                        and risk_level in {"R2", "R3"}
                        and decision.get("human_reviewed") is not True
                    ):
                        errors.append("CLOSED_MATERIAL_ISSUE_NOT_HUMAN_REVIEWED")
                        trace_valid = False
                    required_predicates = {"evidence_admissible", "claim_resolved"}
                    if issue.get("requires_external_evidence") is True:
                        required_predicates.add("scope_fit_confirmed")
                    if isinstance(risk_level, str) and risk_level in {"R2", "R3"}:
                        required_predicates.add("human_review_completed")
                    predicate_results = decision.get("predicate_results")
                    if not isinstance(predicate_results, dict) or not required_predicates.issubset(
                        predicate_results
                    ):
                        errors.append("CLOSURE_DECISION_REQUIRED_PREDICATES_MISSING")
                        trace_valid = False
                    decision_issue_refs = _string_list(decision.get("issue_refs")) or []
                    if issue_id not in decision_issue_refs:
                        errors.append("CLOSURE_DECISION_DOES_NOT_REFERENCE_ISSUE")
                        trace_valid = False
                    if set(_string_list(decision.get("evidence_refs")) or []) != set(closure_refs):
                        errors.append("CLOSURE_DECISION_EVIDENCE_MISMATCH")
                        trace_valid = False
                    reviewed_at = _parse_timestamp(decision.get("reviewed_at"))
                    for action_id in action_refs or []:
                        action = verification_actions.get(action_id)
                        if action is None:
                            continue
                        performed_at = _parse_timestamp(action.get("performed_at"))
                        if (
                            performed_at is not None
                            and reviewed_at is not None
                            and performed_at > reviewed_at
                        ):
                            errors.append("VERIFICATION_ACTION_AFTER_CLOSURE_DECISION")
                            trace_valid = False
                    decision_disposition = decision.get("disposition")
                    if isinstance(decision_disposition, str) and decision_disposition in {
                        "revised",
                        "deleted",
                        "narrowed",
                    }:
                        errors.append("CLOSED_ISSUE_SELF_REPORTED_EDIT_UNVERIFIABLE")
                        trace_valid = False

        closure_valid = (
            workflow_state == "closed"
            and isinstance(verification_status, str)
            and verification_status in CLOSABLE_VERIFICATION_STATUSES
            and closure_evidence_valid
            and trace_valid
        )
        if closure_valid:
            resolved_issue_count += 1
        if _nonempty_string(issue_id):
            issue_closure_validity[issue_id] = closure_valid
        if risk_level == "R3" and not closure_valid:
            unresolved_high_risk_count += 1
            if issue.get("publication_effect") != "block_publication":
                errors.append("UNRESOLVED_R3_MUST_BLOCK_PUBLICATION")
        if isinstance(risk_level, str) and risk_level in {"R2", "R3"} and not closure_valid:
            unresolved_material_risk_count += 1
            issue_effect = issue.get("publication_effect")
            if not isinstance(issue_effect, str) or issue_effect not in {
                "revise_before_publish",
                "block_publication",
            }:
                errors.append("UNRESOLVED_MATERIAL_ISSUE_PUBLICATION_EFFECT_TOO_WEAK")

    if publication_status == "ready" and unresolved_material_risk_count:
        errors.append("R2_R3_UNRESOLVED_WHILE_READY")
    active_publication_effects: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        raw_issue_id = issue.get("issue_id")
        issue_id = raw_issue_id if _nonempty_string(raw_issue_id) else None
        effect = issue.get("publication_effect")
        if issue_closure_validity.get(issue_id, False):
            if isinstance(effect, str) and effect in {
                "revise_before_publish",
                "block_publication",
            }:
                errors.append("CLOSED_ISSUE_PUBLICATION_EFFECT_INVALID")
        elif isinstance(effect, str) and effect in PUBLICATION_EFFECTS:
            active_publication_effects.add(effect)
    if publication_status == "ready" and active_publication_effects & {
        "revise_before_publish",
        "block_publication",
    }:
        errors.append("READY_STATUS_CONTRADICTS_PUBLICATION_EFFECT")
    if "block_publication" in active_publication_effects and publication_status != "hold":
        errors.append("BLOCKING_ISSUE_REQUIRES_HOLD_STATUS")
    if publication_status == "revise" and (
        "revise_before_publish" not in active_publication_effects
        or "block_publication" in active_publication_effects
    ):
        errors.append("REVISE_STATUS_WITHOUT_ACTIVE_REVISION_ISSUE")
    if publication_status == "hold" and "block_publication" not in active_publication_effects:
        errors.append("HOLD_STATUS_WITHOUT_ACTIVE_BLOCKING_ISSUE")

    active_issue_claim_refs: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        raw_issue_id = issue.get("issue_id")
        issue_id = raw_issue_id if _nonempty_string(raw_issue_id) else None
        if issue_closure_validity.get(issue_id, False):
            continue
        active_issue_claim_refs.update(_string_list(issue.get("claim_refs")) or [])
    if unresolved_claim_ids - active_issue_claim_refs:
        errors.append("UNRESOLVED_CLAIM_WITHOUT_ACTIVE_ISSUE")

    if "new_viewpoints" not in ledger:
        errors.append("NEW_VIEWPOINTS_MISSING")
    viewpoints = ledger.get("new_viewpoints", [])
    if not isinstance(viewpoints, list):
        errors.append("NEW_VIEWPOINTS_NOT_LIST")
        viewpoints = []
    if len(viewpoints) > 2:
        errors.append("TOO_MANY_NEW_VIEWPOINTS")
    seen_viewpoint_ids: set[str] = set()
    for viewpoint in viewpoints:
        if not isinstance(viewpoint, dict):
            errors.append("NEW_VIEWPOINT_NOT_OBJECT")
            continue
        viewpoint_id = viewpoint.get("viewpoint_id")
        if not _nonempty_string(viewpoint_id):
            errors.append("MISSING_NEW_VIEWPOINT_ID")
        elif viewpoint_id in seen_viewpoint_ids:
            errors.append("DUPLICATE_NEW_VIEWPOINT_ID")
        else:
            seen_viewpoint_ids.add(viewpoint_id)
        for field in ("actor", "decision", "mechanism", "boundary", "falsifier"):
            minimum_chars = 2 if field == "actor" else 4
            if not _substantive_string(viewpoint.get(field), minimum_chars):
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
                if _nonempty_string(viewpoint_id) and (
                    f"viewpoint:{viewpoint_id}"
                    not in evidence_fit_targets.get(evidence_id, set())
                ):
                    errors.append("NEW_VIEWPOINT_EVIDENCE_FIT_TARGET_MISSING")
                if (
                    network_available is False
                    and str(record.get("acquisition", "")).strip().casefold() == "opened_page"
                ):
                    errors.append("NEW_VIEWPOINT_EXTERNAL_EVIDENCE_OFFLINE")
            if not any(
                has_material_external_fit(evidence_id, f"viewpoint:{viewpoint_id}")
                and _nonempty_string(viewpoint_id)
                and f"viewpoint:{viewpoint_id}"
                in evidence_fit_targets.get(evidence_id, set())
                for evidence_id in refs
            ):
                errors.append("NEW_VIEWPOINT_MATCHED_EXTERNAL_EVIDENCE_MISSING")

    for evidence_id, target_refs in evidence_fit_targets.items():
        for target_ref in target_refs:
            target_type, target_id = target_ref.split(":", 1)
            if target_type == "claim" and target_id not in claim_by_id:
                errors.append("UNKNOWN_EVIDENCE_CLAIM_FIT_TARGET")
            if target_type == "viewpoint" and target_id not in seen_viewpoint_ids:
                errors.append("UNKNOWN_EVIDENCE_VIEWPOINT_FIT_TARGET")

    report_result = validate_user_report.validate_report(report_text) if report_text else {
        "ok": False,
        "error_count": 1,
    }
    report_error_count = int(report_result.get("error_count", 0))
    if not report_result.get("ok"):
        errors.append("USER_REPORT_INVALID")
    if (
        isinstance(publication_status, str)
        and publication_status in PUBLICATION_STATUSES
        and report_text
    ):
        reader_text = validate_user_report.split_reader_layer(report_text)
        positions = validate_user_report.section_positions(reader_text)
        conclusion = validate_user_report.section_body(reader_text, positions, "结论")
        if validate_user_report.leading_publication_status(conclusion) != PUBLICATION_STATUS_TEXT[publication_status]:
            errors.append("PUBLICATION_STATUS_REPORT_MISMATCH")
    reader_viewpoint_count = report_result.get("new_viewpoint_count")
    if isinstance(reader_viewpoint_count, int) and reader_viewpoint_count != len(viewpoints):
        errors.append("NEW_VIEWPOINT_REPORT_COUNT_MISMATCH")
    reader_issue_count = report_result.get("priority_item_count")
    if isinstance(reader_issue_count, int):
        if not issues and reader_issue_count > 0:
            errors.append("READER_REPORT_ISSUES_WITHOUT_LEDGER")

    # Bind every displayed issue to ledger records by the exact reader-item
    # bytes, and require each mapped issue's verbatim draft anchor to appear in
    # that item.  This proves byte-level correspondence and material coverage;
    # it deliberately does not claim to solve full natural-language entailment.
    reader_issue_items: list[str] = []
    reader_viewpoint_items: list[str] = []
    if report_text:
        reader_text = validate_user_report.split_reader_layer(report_text)
        positions = validate_user_report.section_positions(reader_text)
        reader_issue_items = validate_user_report.section_items(
            reader_text, positions, "发布前需要处理"
        )
        reader_viewpoint_items = validate_user_report.section_items(
            reader_text, positions, "可继续研究的新观点"
        )
    issue_ids_by_reader_index: dict[int, list[str]] = {}
    required_reader_issue_ids: set[str] = set()
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        issue_id = issue.get("issue_id")
        publication_effect = issue.get("publication_effect")
        must_display = isinstance(publication_effect, str) and publication_effect in {
            "revise_before_publish",
            "block_publication",
        }
        if must_display and _nonempty_string(issue_id):
            required_reader_issue_ids.add(issue_id)
        binding = issue.get("reader_binding")
        if not isinstance(binding, dict):
            if must_display:
                errors.append("PUBLICATION_EFFECT_READER_MAPPING_MISSING")
            continue
        if publication_effect == "none":
            errors.append("NO_EFFECT_ISSUE_MAPPED_TO_READER_REPORT")
        item_index = binding.get("item_index")
        if isinstance(item_index, bool) or not isinstance(item_index, int) or not (
            1 <= item_index <= len(reader_issue_items)
        ):
            errors.append("ISSUE_READER_ITEM_INDEX_INVALID")
            continue
        item_text = reader_issue_items[item_index - 1]
        if _nonempty_string(issue_id):
            issue_ids_by_reader_index.setdefault(item_index, []).append(issue_id)

        item_lines = item_text.splitlines()
        item_heading = item_lines[0] if item_lines else ""
        location_lines = "\n".join(
            validate_user_report.priority_label_values(item_text, "原文位置")
        )
        reason_lines = "\n".join(
            validate_user_report.priority_label_values(item_text, "为什么重要")
        )
        action_lines = "\n".join(
            validate_user_report.priority_label_values(item_text, "建议处理")
        )
        excerpt_checks = (
            (
                "location_excerpt",
                (issue.get("source_anchor"), issue.get("source_locator")),
                location_lines,
                "ISSUE_READER_LOCATION_BINDING_INVALID",
            ),
            (
                "problem_excerpt",
                (issue.get("problem"),),
                item_heading,
                "ISSUE_READER_PROBLEM_BINDING_INVALID",
            ),
            (
                "reason_excerpt",
                (issue.get("reason"),),
                reason_lines,
                "ISSUE_READER_REASON_BINDING_INVALID",
            ),
            (
                "action_excerpt",
                (issue.get("suggested_action"),),
                action_lines,
                "ISSUE_READER_ACTION_BINDING_INVALID",
            ),
        )
        for field, ledger_values, reader_target, error_code in excerpt_checks:
            excerpt = binding.get(field)
            if (
                not _substantive_string(excerpt, 4)
                or not _contains_unnegated_excerpt(reader_target, excerpt)
                or not any(_nonempty_string(value) and excerpt in value for value in ledger_values)
            ):
                errors.append(error_code)

    if set(issue_ids_by_reader_index) != set(range(1, len(reader_issue_items) + 1)):
        errors.append("READER_ISSUE_ITEM_MAPPING_INCOMPLETE")
    mapped_issue_ids = {
        issue_id for issue_ids in issue_ids_by_reader_index.values() for issue_id in issue_ids
    }
    if not required_reader_issue_ids.issubset(mapped_issue_ids):
        errors.append("PUBLICATION_EFFECT_NOT_COVERED_IN_READER_REPORT")
    for item_index, issue_ids in issue_ids_by_reader_index.items():
        if len(issue_ids) <= 1:
            continue
        for issue_id in issue_ids:
            issue = issue_by_id.get(issue_id, {})
            if not _substantive_string(issue.get("reader_grouping_reason"), 6):
                errors.append("GROUPED_READER_ISSUE_REASON_MISSING")

    reader_viewpoint_by_hash = {
        validate_user_report.reader_item_sha256(item): item for item in reader_viewpoint_items
    }
    reader_viewpoint_hashes = set(reader_viewpoint_by_hash)
    ledger_viewpoint_hashes: list[str] = []
    for viewpoint in viewpoints:
        if not isinstance(viewpoint, dict):
            continue
        reader_hash = viewpoint.get("reader_item_sha256")
        if not _nonempty_string(reader_hash) or not re.fullmatch(
            r"[0-9a-fA-F]{64}", reader_hash.strip()
        ):
            errors.append("NEW_VIEWPOINT_READER_MAPPING_MISSING")
            continue
        normalized_hash = reader_hash.strip().casefold()
        ledger_viewpoint_hashes.append(normalized_hash)
        item_text = reader_viewpoint_by_hash.get(normalized_hash)
        if item_text is None:
            continue
        for field, label, error_code in (
            ("actor", "适用对象", "NEW_VIEWPOINT_READER_ACTOR_BINDING_INVALID"),
            ("decision", "决策问题", "NEW_VIEWPOINT_READER_DECISION_BINDING_INVALID"),
            ("mechanism", "可能机制", "NEW_VIEWPOINT_READER_MECHANISM_BINDING_INVALID"),
            ("boundary", "适用边界", "NEW_VIEWPOINT_READER_BOUNDARY_BINDING_INVALID"),
            ("falsifier", "反证条件", "NEW_VIEWPOINT_READER_FALSIFIER_BINDING_INVALID"),
        ):
            values = validate_user_report.viewpoint_label_values(item_text, label)
            ledger_value = viewpoint.get(field)
            if (
                len(values) != 1
                or not _nonempty_string(ledger_value)
                or not _contains_unnegated_excerpt(values[0], ledger_value.strip())
            ):
                errors.append(error_code)
            if (
                field == "mechanism"
                and _nonempty_string(ledger_value)
                and ledger_value.strip() in draft_text
            ):
                errors.append("NEW_VIEWPOINT_MECHANISM_COPIES_DRAFT")
    if len(set(ledger_viewpoint_hashes)) != len(ledger_viewpoint_hashes):
        errors.append("DUPLICATE_NEW_VIEWPOINT_READER_MAPPING")
    if set(ledger_viewpoint_hashes) != reader_viewpoint_hashes:
        errors.append("NEW_VIEWPOINT_READER_ITEM_MAPPING_MISMATCH")

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
        "unresolved_external_claim_count": unresolved_external_claim_count,
        "evidence_count": len(evidence_records),
        "new_viewpoint_count": len(viewpoints),
        "supplied_input_count": collection_counts["supplied_inputs"],
        "verified_supplied_input_count": sum(supplied_input_validity.values()),
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
    parser.add_argument(
        "--supplied-input",
        action="append",
        default=[],
        metavar="ID=PATH",
        help=(
            "Bind one supplied_inputs manifest ID to the exact local source bytes. "
            "Repeat for every declared supplied input."
        ),
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print privacy-minimized JSON output.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        draft_bytes = args.draft.read_bytes()
        ledger_bytes = args.ledger.read_bytes()
        report_bytes = args.report.read_bytes()
    except OSError:
        result: dict[str, object] = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["BUNDLE_READ_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    try:
        ledger_text = ledger_bytes.decode("utf-8-sig")
    except UnicodeError:
        result: dict[str, object] = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["LEDGER_READ_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    try:
        ledger = json.loads(ledger_text, object_pairs_hook=_unique_json_object)
    except _DuplicateJSONKeyError:
        result = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["LEDGER_DUPLICATE_KEY"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2
    except (json.JSONDecodeError, RecursionError, ValueError):
        result = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["LEDGER_JSON_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    supplied_input_bytes: dict[str, bytes] = {}
    supplied_input_cli_errors: list[str] = []
    for specification in args.supplied_input:
        input_id, separator, raw_path = specification.partition("=")
        input_id = input_id.strip()
        if not separator or not input_id or not raw_path.strip():
            supplied_input_cli_errors.append("SUPPLIED_INPUT_ARGUMENT_INVALID")
            continue
        if not _stable_supplied_input_id(input_id):
            supplied_input_cli_errors.append("SUPPLIED_INPUT_ARGUMENT_ID_UNSAFE")
            continue
        if input_id in supplied_input_bytes:
            supplied_input_cli_errors.append("SUPPLIED_INPUT_ARGUMENT_DUPLICATE")
            continue
        try:
            supplied_input_bytes[input_id] = Path(raw_path.strip()).read_bytes()
        except (OSError, ValueError):
            supplied_input_cli_errors.append("SUPPLIED_INPUT_READ_ERROR")

    result = validate_bundle(
        draft_bytes,
        ledger,
        report_bytes,
        supplied_input_bytes=supplied_input_bytes,
    )
    if supplied_input_cli_errors:
        merged_errors = sorted(set(result.get("error_codes", [])) | set(supplied_input_cli_errors))
        result["ok"] = False
        result["error_codes"] = merged_errors
        result["error_count"] = len(merged_errors)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
