#!/usr/bin/env python3
"""Validate, safely search, and build pre-draft knowledge manifests.

Standard CLI output intentionally omits card content, source identifiers,
locators, filenames, and paths. Source documents remain untrusted data.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "0.2"
TASK_ENVELOPE_VERSION = "0.1"
LEGACY_PRIOR_MANIFEST_VERSION = "0.1"
PRIOR_MANIFEST_VERSION = "0.2"
LEGACY_PRIOR_SELECTION_POLICY_VERSION = "metadata-scope-v0.1"
PRIOR_SELECTION_POLICY_VERSION = "metadata-scope-char-budget-v0.2"
PRIOR_CHARACTER_METRIC = "canonical-json-unicode-code-points-v1"
DEFAULT_MAX_QUESTIONS = 12
DEFAULT_PER_FAMILY_LIMIT = 2
DEFAULT_MAX_TEXT_ARRAY_ITEMS = 4
DEFAULT_MAX_TEXT_ARRAY_CHARS = 512
DEFAULT_MAX_QUESTION_CHARS = 2048
DEFAULT_MAX_MANIFEST_CHARS = 16384
HARD_MAX_QUESTIONS = DEFAULT_MAX_QUESTIONS
HARD_PER_FAMILY_LIMIT = DEFAULT_PER_FAMILY_LIMIT
HARD_MAX_TEXT_ARRAY_ITEMS = DEFAULT_MAX_TEXT_ARRAY_ITEMS
HARD_MAX_TEXT_ARRAY_CHARS = DEFAULT_MAX_TEXT_ARRAY_CHARS
HARD_MAX_QUESTION_CHARS = DEFAULT_MAX_QUESTION_CHARS
HARD_MAX_MANIFEST_CHARS = DEFAULT_MAX_MANIFEST_CHARS
PRIOR_QUESTION_ARRAY_FIELDS = (
    "applicability",
    "exclusions",
    "counterhypothesis_prompts",
    "recommended_channels",
)
PRIOR_BUDGET_ISSUE_CODES = {
    "PRIOR_TEXT_ARRAY_ITEM_LIMIT_OMITTED",
    "PRIOR_TEXT_ARRAY_CHAR_LIMIT_OMITTED",
    "PRIOR_QUESTION_CHAR_LIMIT_OMITTED",
    "PRIOR_MANIFEST_CHAR_LIMIT_OMITTED",
}
PRIOR_BUDGET_CLOSURE_CODES = {
    "PRIOR_BUDGET_WITHIN_LIMIT",
    "PRIOR_BUDGET_TRUNCATED_TO_LIMIT",
}
CARD_TYPES = {"fact_evidence", "expert_heuristic", "control"}
SENSITIVITIES = {"public", "restricted", "private"}
EPISTEMIC_STATUSES = {"reported", "verified", "corroborated", "disputed"}
BOUNDARIES = {"fact", "calculation", "inference", "opinion", "prediction", "reported_statement"}
VOLATILITIES = {"static", "slow", "dynamic"}
LIFECYCLE_STATUSES = {"active", "stale", "expired", "disputed", "revoked", "quarantined"}
CARD_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")
TOKEN_RE = re.compile(r"[0-9A-Za-z_\-]+|[\u4e00-\u9fff]+")
ENVELOPE_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,63}$")
AUTHORIZATION_ID_RE = re.compile(r"^[A-Z][A-Z0-9-]{2,127}$")
AUTHORIZATION_STATUSES = {"active", "expired", "revoked"}
PROCESSOR_CLASSES = {
    "local_deterministic",
    "model_context_abstract",
    "hosted_model_raw",
    "external_service",
}
OUTPUT_AUDIENCES = {"private_review", "public_demo", "external_publication"}
ENVELOPE_MODES = {"reasoning-audit", "thesis-reconstruction", "full-review"}
FIELD_PROVENANCE_VALUES = {
    "user_supplied",
    "topic_brief",
    "file_metadata",
    "previously_approved_context",
    "draft_text",
}
PRIOR_CARD_TYPES = {"expert_heuristic", "control"}
GENERIC_ISSUE_TAGS = {"synthetic", "demo", "test", "research-review"}
PUBLIC_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
IGNORED_PRIVATE_OUTPUT_ROOTS = {
    "work",
    "outputs",
    "artifacts",
    "tmp",
    "temp",
    "private",
    "private-kb",
    "local-knowledge",
    "knowledge-private",
}
TASK_ENVELOPE_FIELDS = {
    "mode",
    "domain_codes",
    "article_type",
    "research_stage",
    "audience_codes",
    "jurisdictions",
    "entity_types",
    "as_of",
    "available_material_roles",
    "allowed_channels",
}
TASK_ENVELOPE_TOP_LEVEL_FIELDS = TASK_ENVELOPE_FIELDS | {
    "schema_version",
    "envelope_id",
    "run_id",
    "created_at",
    "field_provenance",
    "draft_visibility_at_start",
    "precommit_possible",
    "authorization_context",
}
AUTHORIZATION_CONTEXT_FIELDS = {
    "authorization_id",
    "purpose",
    "processor_class",
    "output_audience",
}


class CardFileError(RuntimeError):
    """An input could not be parsed without exposing its path or content."""


class PriorManifestError(RuntimeError):
    """A pre-draft manifest could not be built safely."""


class AuthorizationError(RuntimeError):
    """Private knowledge authorization is missing, invalid, or insufficient."""


@dataclass(frozen=True)
class CardIssue:
    card_id: str
    line: int
    code: str

    def as_dict(self) -> dict[str, Any]:
        return {"card_id": self.card_id, "line": self.line, "code": self.code}


def safe_card_id(card: Any, line: int) -> str:
    if isinstance(card, dict):
        value = card.get("card_id")
        if isinstance(value, str) and CARD_ID_RE.fullmatch(value):
            return value
    return f"CARD-LINE-{line}"


def parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not a string")
    return date.fromisoformat(value)


def parse_datetime(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("not a string")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timezone required")
    return parsed


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def canonical_char_count(value: Any) -> int:
    """Count Unicode code points in deterministic compact JSON, not tokens or bytes."""

    return len(canonical_json(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise CardFileError("Input could not be read for hashing.") from exc
    return digest.hexdigest()


def read_json_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PriorManifestError(f"{label}_READ_ERROR") from exc
    if not isinstance(value, dict):
        raise PriorManifestError(f"{label}_NOT_OBJECT")
    return value


def write_json_object(path: Path, value: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise PriorManifestError("OUTPUT_WRITE_ERROR") from exc


def ensure_private_output_path(path: Path) -> None:
    """Refuse a sensitive artifact in a normally publishable repository path."""

    resolved = path.resolve(strict=False)
    try:
        relative = resolved.relative_to(PUBLIC_REPOSITORY_ROOT)
    except ValueError:
        return
    if not relative.parts or relative.parts[0].casefold() not in IGNORED_PRIVATE_OUTPUT_ROOTS:
        raise PriorManifestError("OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION")


def validate_task_envelope(envelope: Any) -> list[str]:
    """Validate the small runtime subset without requiring a JSON Schema package."""

    codes: list[str] = []
    if not isinstance(envelope, dict):
        return ["TASK_ENVELOPE_NOT_OBJECT"]
    if set(envelope) != TASK_ENVELOPE_TOP_LEVEL_FIELDS:
        codes.append("TASK_ENVELOPE_FIELDS")
    if envelope.get("schema_version") != TASK_ENVELOPE_VERSION:
        codes.append("TASK_ENVELOPE_SCHEMA_VERSION")
    for key in ("envelope_id", "run_id"):
        value = envelope.get(key)
        if not isinstance(value, str) or not ENVELOPE_ID_RE.fullmatch(value):
            codes.append(f"TASK_ENVELOPE_{key.upper()}")
    try:
        parse_datetime(envelope.get("created_at"))
    except (TypeError, ValueError):
        codes.append("TASK_ENVELOPE_CREATED_AT")
    if envelope.get("mode") not in ENVELOPE_MODES:
        codes.append("TASK_ENVELOPE_MODE")
    for key in (
        "domain_codes",
        "audience_codes",
        "jurisdictions",
        "entity_types",
        "available_material_roles",
        "allowed_channels",
    ):
        if not is_string_list(envelope.get(key), nonempty=True):
            codes.append(f"TASK_ENVELOPE_{key.upper()}")
    for key in ("article_type", "research_stage"):
        value = envelope.get(key)
        if not isinstance(value, str) or not value.strip():
            codes.append(f"TASK_ENVELOPE_{key.upper()}")
    try:
        if parse_date(envelope.get("as_of")) is None:
            codes.append("TASK_ENVELOPE_AS_OF")
    except ValueError:
        codes.append("TASK_ENVELOPE_AS_OF")

    field_provenance = envelope.get("field_provenance")
    if not isinstance(field_provenance, dict):
        codes.append("TASK_ENVELOPE_FIELD_PROVENANCE")
    else:
        missing = TASK_ENVELOPE_FIELDS.difference(field_provenance)
        extra = set(field_provenance).difference(TASK_ENVELOPE_FIELDS)
        if missing:
            codes.append("TASK_ENVELOPE_FIELD_PROVENANCE_MISSING")
        if extra:
            codes.append("TASK_ENVELOPE_FIELD_PROVENANCE_EXTRA")
        if any(value not in FIELD_PROVENANCE_VALUES for value in field_provenance.values()):
            codes.append("TASK_ENVELOPE_FIELD_PROVENANCE_VALUE")

    if envelope.get("draft_visibility_at_start") not in {"not_seen", "already_visible", "unknown"}:
        codes.append("TASK_ENVELOPE_DRAFT_VISIBILITY")
    if not isinstance(envelope.get("precommit_possible"), bool):
        codes.append("TASK_ENVELOPE_PRECOMMIT_POSSIBLE")

    context = envelope.get("authorization_context")
    if not isinstance(context, dict):
        codes.append("TASK_ENVELOPE_AUTHORIZATION_CONTEXT")
    else:
        if set(context) != AUTHORIZATION_CONTEXT_FIELDS:
            codes.append("TASK_ENVELOPE_AUTHORIZATION_CONTEXT_FIELDS")
        auth_id = context.get("authorization_id")
        if not isinstance(auth_id, str) or not AUTHORIZATION_ID_RE.fullmatch(auth_id):
            codes.append("TASK_ENVELOPE_AUTHORIZATION_ID")
        purpose = context.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            codes.append("TASK_ENVELOPE_AUTHORIZATION_PURPOSE")
        if context.get("processor_class") not in PROCESSOR_CLASSES:
            codes.append("TASK_ENVELOPE_PROCESSOR_CLASS")
        if context.get("output_audience") not in OUTPUT_AUDIENCES:
            codes.append("TASK_ENVELOPE_OUTPUT_AUDIENCE")
    return sorted(set(codes))


def load_authorizations(path: Path) -> dict[str, dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise AuthorizationError("AUTHORIZATION_FILE_READ_ERROR") from exc

    records: dict[str, dict[str, Any]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise AuthorizationError(f"AUTHORIZATION_JSON_LINE_{line_number}") from exc
        if not isinstance(record, dict):
            raise AuthorizationError(f"AUTHORIZATION_NOT_OBJECT_LINE_{line_number}")
        authorization_id = record.get("authorization_id")
        if not isinstance(authorization_id, str) or not AUTHORIZATION_ID_RE.fullmatch(authorization_id):
            raise AuthorizationError(f"AUTHORIZATION_ID_LINE_{line_number}")
        if authorization_id in records:
            raise AuthorizationError("DUPLICATE_AUTHORIZATION_ID")
        if record.get("status") not in AUTHORIZATION_STATUSES:
            raise AuthorizationError(f"AUTHORIZATION_STATUS_LINE_{line_number}")
        if not is_string_list(record.get("allowed_uses"), nonempty=True):
            raise AuthorizationError(f"AUTHORIZATION_ALLOWED_USES_LINE_{line_number}")
        for key, allowed_values in (
            ("allowed_processors", PROCESSOR_CLASSES),
            ("output_audiences", OUTPUT_AUDIENCES),
        ):
            value = record.get(key)
            if value is not None and (
                not is_string_list(value, nonempty=True) or any(item not in allowed_values for item in value)
            ):
                raise AuthorizationError(f"AUTHORIZATION_{key.upper()}_LINE_{line_number}")
        for key in ("abstract_guidance_allowed", "external_upload_allowed", "public_export_allowed"):
            value = record.get(key)
            if value is not None and not isinstance(value, bool):
                raise AuthorizationError(f"AUTHORIZATION_{key.upper()}_LINE_{line_number}")
        for key in ("valid_from", "expires_at"):
            try:
                parse_date(record.get(key))
            except ValueError as exc:
                raise AuthorizationError(f"AUTHORIZATION_{key.upper()}_LINE_{line_number}") from exc
        records[authorization_id] = record
    return records


def authorize_context(
    authorization: dict[str, Any],
    context: dict[str, Any],
    *,
    at_date: date,
) -> None:
    authorization_id = context["authorization_id"]
    if authorization.get("authorization_id") != authorization_id:
        raise AuthorizationError("AUTHORIZATION_NOT_FOUND")
    if authorization.get("status") != "active":
        raise AuthorizationError("AUTHORIZATION_NOT_ACTIVE")

    valid_from = parse_date(authorization.get("valid_from"))
    expires_at = parse_date(authorization.get("expires_at"))
    if valid_from is not None and at_date < valid_from:
        raise AuthorizationError("AUTHORIZATION_NOT_YET_VALID")
    if expires_at is not None and at_date > expires_at:
        raise AuthorizationError("AUTHORIZATION_EXPIRED")

    purpose = context["purpose"]
    if purpose not in authorization.get("allowed_uses", []):
        raise AuthorizationError("AUTHORIZATION_PURPOSE_DENIED")

    processor = context["processor_class"]
    allowed_processors = authorization.get("allowed_processors")
    if allowed_processors is None:
        allowed_processors = ["local_deterministic"]
    if processor not in allowed_processors:
        raise AuthorizationError("AUTHORIZATION_PROCESSOR_DENIED")
    if processor == "model_context_abstract" and authorization.get("abstract_guidance_allowed") is not True:
        raise AuthorizationError("ABSTRACT_MODEL_CONTEXT_DENIED")
    if processor in {"hosted_model_raw", "external_service"} and authorization.get("external_upload_allowed") is not True:
        raise AuthorizationError("EXTERNAL_PROCESSING_DENIED")

    output_audience = context["output_audience"]
    output_audiences = authorization.get("output_audiences")
    if output_audiences is None:
        output_audiences = ["private_review"]
    if output_audience not in output_audiences:
        raise AuthorizationError("AUTHORIZATION_OUTPUT_DENIED")
    if output_audience != "private_review" and authorization.get("public_export_allowed") is not True:
        raise AuthorizationError("PUBLIC_EXPORT_DENIED")


def authorize_card_for_prior(
    card: dict[str, Any],
    authorization: dict[str, Any],
    context: dict[str, Any],
    *,
    at_date: date,
) -> None:
    authorize_context(authorization, context, at_date=at_date)
    rights = card["rights_and_access"]
    if rights["sensitivity"] == "public":
        return
    if rights.get("authorization_id") != context["authorization_id"]:
        raise AuthorizationError("CARD_AUTHORIZATION_MISMATCH")
    if context["purpose"] not in rights.get("allowed_uses", []):
        raise AuthorizationError("CARD_PURPOSE_DENIED")


def is_string_list(value: Any, *, nonempty: bool = False) -> bool:
    return (
        isinstance(value, list)
        and (not nonempty or bool(value))
        and all(isinstance(item, str) and bool(item.strip()) for item in value)
    )


def require_object(card: dict[str, Any], key: str, issues: list[str]) -> dict[str, Any]:
    value = card.get(key)
    if not isinstance(value, dict):
        issues.append(f"MISSING_OR_INVALID_{key.upper()}")
        return {}
    return value


def validate_card(card: Any, line: int) -> list[CardIssue]:
    card_id = safe_card_id(card, line)
    codes: list[str] = []
    if not isinstance(card, dict):
        return [CardIssue(card_id, line, "CARD_NOT_OBJECT")]

    if card.get("schema_version") != SCHEMA_VERSION:
        codes.append("SCHEMA_VERSION")
    raw_id = card.get("card_id")
    if not isinstance(raw_id, str) or not CARD_ID_RE.fullmatch(raw_id):
        codes.append("CARD_ID")
    card_type = card.get("card_type")
    if card_type not in CARD_TYPES:
        codes.append("CARD_TYPE")
    if not isinstance(card.get("title"), str) or not card["title"].strip():
        codes.append("TITLE")
    if not is_string_list(card.get("tags")):
        codes.append("TAGS")

    content = require_object(card, "content", codes)
    for key in ("claim_or_guidance", "rationale", "recommended_action"):
        if not isinstance(content.get(key), str) or not content[key].strip():
            codes.append(f"CONTENT_{key.upper()}")

    provenance = require_object(card, "provenance", codes)
    if not is_string_list(provenance.get("source_ids")):
        codes.append("PROVENANCE_SOURCE_IDS")
    if not is_string_list(provenance.get("locators")):
        codes.append("PROVENANCE_LOCATORS")
    if not is_string_list(provenance.get("transformation")):
        codes.append("PROVENANCE_TRANSFORMATION")
    if not is_string_list(provenance.get("independent_source_ids")):
        codes.append("PROVENANCE_INDEPENDENT_SOURCES")
    if not isinstance(provenance.get("origin_type"), str):
        codes.append("PROVENANCE_ORIGIN")

    epistemic = require_object(card, "epistemic", codes)
    if epistemic.get("status") not in EPISTEMIC_STATUSES:
        codes.append("EPISTEMIC_STATUS")
    if epistemic.get("fact_inference_boundary") not in BOUNDARIES:
        codes.append("FACT_INFERENCE_BOUNDARY")
    if epistemic.get("confidence") not in {"low", "medium", "high"}:
        codes.append("CONFIDENCE")
    for key in ("assumptions", "limitations", "counterevidence_refs", "conditions_where_false"):
        if not is_string_list(epistemic.get(key)):
            codes.append(f"EPISTEMIC_{key.upper()}")
    if not isinstance(epistemic.get("falsification_test"), str) or not epistemic["falsification_test"].strip():
        codes.append("FALSIFICATION_TEST")

    scope = require_object(card, "scope", codes)
    for key in ("domains", "jurisdictions", "entity_types", "task_stages", "applies_when", "does_not_apply_when"):
        if not is_string_list(scope.get(key)):
            codes.append(f"SCOPE_{key.upper()}")

    temporal = require_object(card, "temporal", codes)
    for key in ("as_of", "last_verified_at", "valid_until"):
        try:
            parse_date(temporal.get(key))
        except ValueError:
            codes.append(f"TEMPORAL_{key.upper()}")
    if temporal.get("volatility") not in VOLATILITIES:
        codes.append("TEMPORAL_VOLATILITY")
    if not isinstance(temporal.get("requires_live_verification"), bool):
        codes.append("REQUIRES_LIVE_VERIFICATION")
    if temporal.get("volatility") == "dynamic" and temporal.get("requires_live_verification") is not True:
        codes.append("DYNAMIC_REQUIRES_LIVE_VERIFICATION")

    rights = require_object(card, "rights_and_access", codes)
    sensitivity = rights.get("sensitivity")
    if sensitivity not in SENSITIVITIES:
        codes.append("SENSITIVITY")
    if not isinstance(rights.get("rights_basis"), str):
        codes.append("RIGHTS_BASIS")
    if not is_string_list(rights.get("allowed_uses")):
        codes.append("ALLOWED_USES")
    if not isinstance(rights.get("verbatim_quote_allowed"), bool):
        codes.append("VERBATIM_QUOTE_ALLOWED")
    if not isinstance(rights.get("export_allowed"), bool):
        codes.append("EXPORT_ALLOWED")
    if sensitivity in {"private", "restricted"}:
        if not isinstance(rights.get("authorization_id"), str) or not rights["authorization_id"].strip():
            codes.append("PRIVATE_AUTHORIZATION_REQUIRED")
        if "local-review" not in rights.get("allowed_uses", []):
            codes.append("PRIVATE_LOCAL_USE_REQUIRED")
        if rights.get("verbatim_quote_allowed") is not False:
            codes.append("PRIVATE_QUOTE_MUST_BE_FALSE")
        if rights.get("export_allowed") is not False:
            codes.append("PRIVATE_EXPORT_MUST_BE_FALSE")

    lifecycle = require_object(card, "lifecycle", codes)
    if lifecycle.get("status") not in LIFECYCLE_STATUSES:
        codes.append("LIFECYCLE_STATUS")
    if not isinstance(lifecycle.get("owner"), str) or not lifecycle["owner"].strip():
        codes.append("LIFECYCLE_OWNER")
    try:
        parse_date(lifecycle.get("review_by"))
    except ValueError:
        codes.append("LIFECYCLE_REVIEW_BY")
    for key in ("supersedes", "superseded_by"):
        if not is_string_list(lifecycle.get(key)):
            codes.append(f"LIFECYCLE_{key.upper()}")

    if card_type == "fact_evidence":
        if not is_string_list(provenance.get("source_ids"), nonempty=True):
            codes.append("FACT_SOURCE_REQUIRED")
        if not is_string_list(provenance.get("locators"), nonempty=True):
            codes.append("FACT_LOCATOR_REQUIRED")
        try:
            if parse_date(temporal.get("as_of")) is None:
                codes.append("FACT_AS_OF_REQUIRED")
        except ValueError:
            pass
        if epistemic.get("status") in {"verified", "corroborated"}:
            try:
                if parse_date(temporal.get("last_verified_at")) is None:
                    codes.append("VERIFIED_AT_REQUIRED")
            except ValueError:
                pass

    if card_type == "expert_heuristic":
        if not is_string_list(scope.get("applies_when"), nonempty=True):
            codes.append("HEURISTIC_APPLIES_WHEN_REQUIRED")
        if not is_string_list(scope.get("does_not_apply_when"), nonempty=True):
            codes.append("HEURISTIC_EXCLUSIONS_REQUIRED")
        if not is_string_list(epistemic.get("conditions_where_false"), nonempty=True):
            codes.append("HEURISTIC_COUNTEREXAMPLE_REQUIRED")
        try:
            if parse_date(lifecycle.get("review_by")) is None:
                codes.append("HEURISTIC_REVIEW_DATE_REQUIRED")
        except ValueError:
            pass

    if provenance.get("origin_type") == "meeting_transcript" and not provenance.get("independent_source_ids"):
        if epistemic.get("status") != "reported":
            codes.append("MEETING_STATEMENT_NOT_VERIFIED")
        if epistemic.get("fact_inference_boundary") not in {"reported_statement", "opinion"}:
            codes.append("MEETING_STATEMENT_BOUNDARY")

    return [CardIssue(card_id, line, code) for code in sorted(set(codes))]


def load_cards(path: Path) -> list[tuple[int, dict[str, Any]]]:
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise CardFileError("Card input could not be read.") from exc

    cards: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise CardFileError(f"Invalid JSON at line {line_number}.") from exc
        if not isinstance(value, dict):
            raise CardFileError(f"Card at line {line_number} is not an object.")
        cards.append((line_number, value))
    return cards


def validate_cards(cards: Iterable[tuple[int, dict[str, Any]]]) -> list[CardIssue]:
    issues: list[CardIssue] = []
    seen: set[str] = set()
    for line, card in cards:
        issues.extend(validate_card(card, line))
        card_id = card.get("card_id")
        if isinstance(card_id, str) and CARD_ID_RE.fullmatch(card_id):
            if card_id in seen:
                issues.append(CardIssue(card_id, line, "DUPLICATE_CARD_ID"))
            seen.add(card_id)
    return sorted(set(issues), key=lambda issue: (issue.line, issue.card_id, issue.code))


def freshness(card: dict[str, Any], as_of: date) -> str:
    lifecycle = card["lifecycle"]
    status = lifecycle["status"]
    if status in {"revoked", "quarantined", "disputed"}:
        return status
    if status == "expired":
        return "expired"
    valid_until = parse_date(card["temporal"].get("valid_until"))
    if valid_until is not None and valid_until < as_of:
        return "expired"
    if status == "stale":
        return "stale"
    review_by = parse_date(lifecycle.get("review_by"))
    if review_by is not None and review_by < as_of:
        return "stale"
    return "current"


def access_allowed(card: dict[str, Any], include_private: bool, authorization_ids: set[str]) -> bool:
    rights = card["rights_and_access"]
    sensitivity = rights["sensitivity"]
    if sensitivity == "public":
        return True
    if not include_private:
        return False
    authorization_id = rights.get("authorization_id")
    return isinstance(authorization_id, str) and authorization_id in authorization_ids


def tokenize(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in TOKEN_RE.findall(value):
        token = raw.casefold()
        if not token.strip():
            continue
        tokens.append(token)
        if all("\u4e00" <= character <= "\u9fff" for character in token) and len(token) > 2:
            tokens.extend(token[index : index + 2] for index in range(len(token) - 1))
    return list(dict.fromkeys(tokens))


def match_score(card: dict[str, Any], query: str) -> int:
    terms = tokenize(query)
    if not terms:
        return 0
    searchable = json.dumps(
        {
            "title": card.get("title"),
            "tags": card.get("tags"),
            "content": card.get("content"),
            "scope": card.get("scope"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).casefold()
    return sum(1 for term in terms if term in searchable)


def safe_search_record(card: dict[str, Any], score: int, as_of: date) -> dict[str, Any]:
    card_freshness = freshness(card, as_of)
    card_type = card["card_type"]
    provenance = card["provenance"]
    epistemic = card["epistemic"]
    temporal = card["temporal"]

    meeting_without_independent_source = (
        provenance["origin_type"] == "meeting_transcript" and not provenance["independent_source_ids"]
    )
    requires_live = (
        temporal["requires_live_verification"]
        or temporal["volatility"] == "dynamic"
        or card_freshness in {"stale", "expired", "disputed"}
        or epistemic["status"] in {"reported", "disputed"}
        or meeting_without_independent_source
    )
    eligible = (
        card_type == "fact_evidence"
        and epistemic["status"] in {"verified", "corroborated"}
        and epistemic["fact_inference_boundary"] in {"fact", "calculation"}
        and card_freshness == "current"
        and not requires_live
        and not meeting_without_independent_source
    )

    if card_type in {"expert_heuristic", "control"}:
        use_mode = "review_prompt"
    elif meeting_without_independent_source:
        use_mode = "reported_lead"
    elif card_freshness == "disputed" or epistemic["status"] == "disputed":
        use_mode = "conflicting_evidence"
    elif requires_live:
        use_mode = "refresh_required"
    elif eligible:
        use_mode = "evidence_candidate"
    else:
        use_mode = "analysis_input"

    return {
        "card_id": card["card_id"],
        "card_type": card_type,
        "match_score": score,
        "freshness": card_freshness,
        "use_mode": use_mode,
        "eligible_as_evidence": eligible,
        "requires_live_verification": requires_live,
        "human_review_required": True,
    }


def search_cards(
    cards: Iterable[tuple[int, dict[str, Any]]],
    query: str,
    *,
    as_of: date,
    include_private: bool = False,
    authorization_ids: set[str] | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if not query.strip():
        raise ValueError("Query must not be empty.")
    authorization_ids = authorization_ids or set()
    candidates: list[tuple[int, dict[str, Any]]] = []
    for _, card in cards:
        if card["lifecycle"]["status"] in {"revoked", "quarantined"}:
            continue
        if not access_allowed(card, include_private, authorization_ids):
            continue
        score = match_score(card, query)
        if score:
            candidates.append((score, card))
    candidates.sort(key=lambda item: (-item[0], item[1]["card_id"]))
    return [safe_search_record(card, score, as_of) for score, card in candidates[:limit]]


def _scope_dimension_score(card_values: list[str], task_values: list[str], wildcards: set[str]) -> int | None:
    normalized_card = {value.casefold() for value in card_values}
    normalized_task = {value.casefold() for value in task_values}
    if not normalized_card or normalized_card.intersection(wildcards):
        return 0
    if normalized_card.intersection(normalized_task):
        return 1
    return None


def prior_scope_score(card: dict[str, Any], envelope: dict[str, Any]) -> int | None:
    """Return a deterministic routing score without reading draft text."""

    scope = card["scope"]
    dimensions = (
        (scope["domains"], envelope["domain_codes"], {"research-review", "not-applicable"}),
        (scope["jurisdictions"], envelope["jurisdictions"], {"not-applicable", "global"}),
        (scope["entity_types"], envelope["entity_types"], {"research-draft", "not-applicable"}),
        (
            scope["task_stages"],
            [envelope["research_stage"], envelope["mode"]],
            {"all", "not-applicable"},
        ),
    )
    score = 0
    for card_values, task_values, wildcards in dimensions:
        value = _scope_dimension_score(card_values, task_values, wildcards)
        if value is None:
            return None
        score += value
    return score


def issue_family(card: dict[str, Any]) -> str:
    tags = [tag.strip().casefold() for tag in card.get("tags", []) if tag.strip()]
    for tag in tags:
        if tag not in GENERIC_ISSUE_TAGS:
            return tag
    return "general-review"


def question_from_card(card: dict[str, Any]) -> dict[str, Any]:
    card_id = card["card_id"]
    family = issue_family(card)
    channels = (
        ["deterministic_text", "document_set"]
        if card["card_type"] == "control"
        else ["document_set", "context_or_human"]
    )
    return {
        "prior_question_id": f"PQ-{card_id}",
        "knowledge_card_id": card_id,
        "issue_family": family,
        "risk_type": family,
        "atomic_question": card["content"]["claim_or_guidance"],
        "applicability": card["scope"]["applies_when"],
        "exclusions": card["scope"]["does_not_apply_when"],
        "counterhypothesis_prompts": card["epistemic"]["conditions_where_false"],
        "verification_action": card["content"]["recommended_action"],
        "recommended_channels": channels,
        "closure_condition": card["epistemic"]["falsification_test"],
        "knowledge_role": "review_prompt",
        "eligible_as_evidence": False,
    }


def make_prior_character_budget(
    *,
    max_text_array_items: int = DEFAULT_MAX_TEXT_ARRAY_ITEMS,
    max_text_array_chars: int = DEFAULT_MAX_TEXT_ARRAY_CHARS,
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
    max_manifest_chars: int = DEFAULT_MAX_MANIFEST_CHARS,
) -> dict[str, Any]:
    values = {
        "max_text_array_items": max_text_array_items,
        "max_text_array_chars": max_text_array_chars,
        "max_question_chars": max_question_chars,
        "max_manifest_chars": max_manifest_chars,
    }
    hard_limits = {
        "max_text_array_items": HARD_MAX_TEXT_ARRAY_ITEMS,
        "max_text_array_chars": HARD_MAX_TEXT_ARRAY_CHARS,
        "max_question_chars": HARD_MAX_QUESTION_CHARS,
        "max_manifest_chars": HARD_MAX_MANIFEST_CHARS,
    }
    if any(type(value) is not int or value < 1 for value in values.values()):
        raise PriorManifestError("PRIOR_BUDGET_NOT_POSITIVE")
    if any(values[key] > hard_limits[key] for key in values):
        raise PriorManifestError("PRIOR_BUDGET_ABOVE_HARD_LIMIT")
    return {"metric": PRIOR_CHARACTER_METRIC, **values}


def question_budget_issue(question: dict[str, Any], budget: dict[str, Any]) -> str | None:
    """Return one stable omission code; never truncate a question or array item."""

    for field in PRIOR_QUESTION_ARRAY_FIELDS:
        values = question[field]
        if len(values) > budget["max_text_array_items"]:
            return "PRIOR_TEXT_ARRAY_ITEM_LIMIT_OMITTED"
        if canonical_char_count(values) > budget["max_text_array_chars"]:
            return "PRIOR_TEXT_ARRAY_CHAR_LIMIT_OMITTED"
    if canonical_char_count(question) > budget["max_question_chars"]:
        return "PRIOR_QUESTION_CHAR_LIMIT_OMITTED"
    return None


def compute_manifest_hash(manifest: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_hash"}
    return canonical_hash(unsigned)


def build_prior_manifest(
    cards: list[tuple[int, dict[str, Any]]],
    envelope: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    knowledge_snapshot_hash: str,
    authorization_snapshot_hash: str,
    created_at: datetime | None = None,
    max_questions: int = DEFAULT_MAX_QUESTIONS,
    per_family_limit: int = DEFAULT_PER_FAMILY_LIMIT,
    max_text_array_items: int = DEFAULT_MAX_TEXT_ARRAY_ITEMS,
    max_text_array_chars: int = DEFAULT_MAX_TEXT_ARRAY_CHARS,
    max_question_chars: int = DEFAULT_MAX_QUESTION_CHARS,
    max_manifest_chars: int = DEFAULT_MAX_MANIFEST_CHARS,
) -> dict[str, Any]:
    envelope_issues = validate_task_envelope(envelope)
    if envelope_issues:
        raise PriorManifestError(";".join(envelope_issues))
    if type(max_questions) is not int or type(per_family_limit) is not int:
        raise PriorManifestError("PRIOR_BUDGET_NOT_POSITIVE")
    if max_questions < 1 or per_family_limit < 1:
        raise PriorManifestError("PRIOR_BUDGET_NOT_POSITIVE")
    if max_questions > HARD_MAX_QUESTIONS or per_family_limit > HARD_PER_FAMILY_LIMIT:
        raise PriorManifestError("PRIOR_BUDGET_ABOVE_HARD_LIMIT")
    character_budget = make_prior_character_budget(
        max_text_array_items=max_text_array_items,
        max_text_array_chars=max_text_array_chars,
        max_question_chars=max_question_chars,
        max_manifest_chars=max_manifest_chars,
    )
    if envelope["draft_visibility_at_start"] != "not_seen" or envelope["precommit_possible"] is not True:
        raise PriorManifestError("PRECOMMIT_NOT_POSSIBLE")
    if "draft_text" in envelope["field_provenance"].values():
        raise PriorManifestError("DRAFT_DERIVED_ROUTING_FORBIDDEN")
    if "private_knowledge" not in envelope["allowed_channels"]:
        raise PriorManifestError("PRIVATE_KNOWLEDGE_CHANNEL_NOT_ALLOWED")

    context = envelope["authorization_context"]
    if context["processor_class"] not in {"local_deterministic", "model_context_abstract"}:
        raise PriorManifestError("RAW_OR_EXTERNAL_PRIOR_PROCESSING_FORBIDDEN")
    if context["output_audience"] != "private_review":
        raise PriorManifestError("PRIOR_OUTPUT_MUST_BE_PRIVATE")
    authorization = authorizations.get(context["authorization_id"])
    if authorization is None:
        raise AuthorizationError("AUTHORIZATION_NOT_FOUND")
    as_of = parse_date(envelope["as_of"])
    assert as_of is not None
    authorize_context(authorization, context, at_date=as_of)

    manifest_created_at = created_at or datetime.now(timezone.utc)
    if manifest_created_at.tzinfo is None or manifest_created_at.utcoffset() is None:
        raise PriorManifestError("PRIOR_CREATED_AT_TIMEZONE_REQUIRED")
    if manifest_created_at < parse_datetime(envelope["created_at"]):
        raise PriorManifestError("PRIOR_CREATED_BEFORE_TASK_ENVELOPE")

    eligible: list[tuple[int, dict[str, Any]]] = []
    for _, card in cards:
        if card["card_type"] not in PRIOR_CARD_TYPES:
            continue
        if freshness(card, as_of) != "current":
            continue
        if card["lifecycle"]["status"] != "active":
            continue
        rights = card["rights_and_access"]
        if context["purpose"] not in rights["allowed_uses"]:
            continue
        if rights["sensitivity"] != "public":
            if rights.get("authorization_id") != context["authorization_id"]:
                continue
            authorize_card_for_prior(card, authorization, context, at_date=as_of)
        score = prior_scope_score(card, envelope)
        if score is not None:
            eligible.append((score, card))

    eligible.sort(key=lambda item: (-item[0], issue_family(item[1]), item[1]["card_id"]))
    selected: list[dict[str, Any]] = []
    family_counts: Counter[str] = Counter()
    budget_issue_counts: Counter[str] = Counter()
    for _, card in eligible:
        family = issue_family(card)
        question = question_from_card(card)
        budget_issue = question_budget_issue(question, character_budget)
        if budget_issue is not None:
            budget_issue_counts[budget_issue] += 1
            continue
        if family_counts.get(family, 0) >= per_family_limit:
            continue
        if len(selected) >= max_questions:
            break
        selected.append(question)
        family_counts[family] += 1

    created_iso = manifest_created_at.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    envelope_hash = canonical_hash(envelope)
    manifest_seed = {
        "run_id": envelope["run_id"],
        "task_envelope_hash": envelope_hash,
        "knowledge_snapshot_hash": knowledge_snapshot_hash,
        "authorization_snapshot_hash": authorization_snapshot_hash,
        "created_at": created_iso,
        "selection_policy_version": PRIOR_SELECTION_POLICY_VERSION,
        "character_budget": character_budget,
    }
    manifest_id = "PM-" + canonical_hash(manifest_seed)[:20].upper()

    def assemble_manifest() -> dict[str, Any]:
        budget_issues = [
            {"code": code, "count": budget_issue_counts[code]}
            for code in sorted(budget_issue_counts)
            if budget_issue_counts[code]
        ]
        manifest: dict[str, Any] = {
            "schema_version": PRIOR_MANIFEST_VERSION,
            "manifest_id": manifest_id,
            "run_id": envelope["run_id"],
            "task_envelope_id": envelope["envelope_id"],
            "task_envelope_hash": envelope_hash,
            "knowledge_snapshot_hash": knowledge_snapshot_hash,
            "authorization_snapshot_hash": authorization_snapshot_hash,
            "authorization_ref": context["authorization_id"],
            "selection_policy_version": PRIOR_SELECTION_POLICY_VERSION,
            "created_at": created_iso,
            "draft_seen": False,
            "questions": list(selected),
            "coverage_by_issue_family": dict(sorted(family_counts.items())),
            "truncation": {
                "max_questions": max_questions,
                "per_family_limit": per_family_limit,
                "eligible_count": len(eligible),
                "selected_count": len(selected),
                "omitted_count": len(eligible) - len(selected),
            },
            "character_budget": character_budget,
            "budget_closure_code": (
                "PRIOR_BUDGET_TRUNCATED_TO_LIMIT"
                if budget_issues
                else "PRIOR_BUDGET_WITHIN_LIMIT"
            ),
            "budget_issues": budget_issues,
            "privacy": {
                "private_review_only": True,
                "card_bodies_not_logged": True,
                "source_locations_omitted": True,
                "questions_are_not_evidence": True,
            },
        }
        manifest["manifest_hash"] = compute_manifest_hash(manifest)
        return manifest

    manifest = assemble_manifest()
    while canonical_char_count(manifest) > character_budget["max_manifest_chars"]:
        if not selected:
            raise PriorManifestError("PRIOR_MANIFEST_BASE_OVER_BUDGET")
        removed = selected.pop()
        removed_family = removed["issue_family"]
        family_counts[removed_family] -= 1
        if family_counts[removed_family] == 0:
            del family_counts[removed_family]
        budget_issue_counts["PRIOR_MANIFEST_CHAR_LIMIT_OMITTED"] += 1
        manifest = assemble_manifest()
    return manifest


def parse_cli_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected an ISO date in YYYY-MM-DD form.") from exc


def parse_cli_datetime(value: str) -> datetime:
    try:
        return parse_datetime(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected a timezone-aware ISO date-time.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, safely search, or precommit local knowledge prompts.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser("validate", help="Validate a JSON Lines card file.")
    validate_parser.add_argument("cards", type=Path)

    search_parser = subparsers.add_parser("search", help="Search cards without printing content or source paths.")
    search_parser.add_argument("cards", type=Path)
    search_parser.add_argument("query")
    search_parser.add_argument("--at-date", type=parse_cli_date, default=date.today())
    search_parser.add_argument("--limit", type=int, default=10)
    search_parser.add_argument("--include-private", action="store_true")
    search_parser.add_argument("--authorization-id", action="append", default=[])

    prior_parser = subparsers.add_parser(
        "prior",
        help="Build a private pre-draft question manifest from structured task metadata.",
    )
    prior_parser.add_argument("cards", type=Path)
    prior_parser.add_argument("task_envelope", type=Path)
    prior_parser.add_argument("--authorizations", type=Path, required=True)
    prior_parser.add_argument("--output", type=Path, required=True)
    prior_parser.add_argument("--created-at", type=parse_cli_datetime)
    prior_parser.add_argument("--max-questions", type=int, default=DEFAULT_MAX_QUESTIONS)
    prior_parser.add_argument("--per-family-limit", type=int, default=DEFAULT_PER_FAMILY_LIMIT)
    prior_parser.add_argument(
        "--max-text-array-items",
        type=int,
        default=DEFAULT_MAX_TEXT_ARRAY_ITEMS,
    )
    prior_parser.add_argument(
        "--max-text-array-chars",
        type=int,
        default=DEFAULT_MAX_TEXT_ARRAY_CHARS,
    )
    prior_parser.add_argument(
        "--max-question-chars",
        type=int,
        default=DEFAULT_MAX_QUESTION_CHARS,
    )
    prior_parser.add_argument(
        "--max-manifest-chars",
        type=int,
        default=DEFAULT_MAX_MANIFEST_CHARS,
    )
    prior_parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    try:
        cards = load_cards(args.cards)
    except CardFileError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    issues = validate_cards(cards)
    if issues:
        print(
            json.dumps(
                {
                    "status": "invalid",
                    "card_count": len(cards),
                    "issue_count": len(issues),
                    "issues": [issue.as_dict() for issue in issues],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 1

    if args.command == "validate":
        print(json.dumps({"status": "valid", "card_count": len(cards), "issue_count": 0}, sort_keys=True))
        return 0

    if args.command == "prior":
        try:
            ensure_private_output_path(args.output)
        except PriorManifestError as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
            return 2
        if args.output.exists() and not args.force:
            print(json.dumps({"status": "error", "error": "OUTPUT_EXISTS"}, sort_keys=True))
            return 2
        try:
            envelope = read_json_object(args.task_envelope, label="TASK_ENVELOPE")
            authorizations = load_authorizations(args.authorizations)
            manifest = build_prior_manifest(
                cards,
                envelope,
                authorizations,
                knowledge_snapshot_hash=sha256_file(args.cards),
                authorization_snapshot_hash=sha256_file(args.authorizations),
                created_at=args.created_at,
                max_questions=args.max_questions,
                per_family_limit=args.per_family_limit,
                max_text_array_items=args.max_text_array_items,
                max_text_array_chars=args.max_text_array_chars,
                max_question_chars=args.max_question_chars,
                max_manifest_chars=args.max_manifest_chars,
            )
            write_json_object(args.output, manifest)
        except (AuthorizationError, CardFileError, PriorManifestError, ValueError) as exc:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
            return 2
        print(
            json.dumps(
                {
                    "status": "ok",
                    "manifest_id": manifest["manifest_id"],
                    "manifest_hash": manifest["manifest_hash"],
                    "question_count": len(manifest["questions"]),
                    "canonical_char_count": canonical_char_count(manifest),
                    "budget_closure_code": manifest["budget_closure_code"],
                    "issue_codes": [issue["code"] for issue in manifest["budget_issues"]],
                    "privacy": "content_sources_and_paths_omitted_from_stdout",
                },
                sort_keys=True,
            )
        )
        return 0

    if args.limit < 1:
        print(json.dumps({"status": "error", "error": "Limit must be positive."}, sort_keys=True))
        return 2
    try:
        results = search_cards(
            cards,
            args.query,
            as_of=args.at_date,
            include_private=args.include_private,
            authorization_ids=set(args.authorization_id),
            limit=args.limit,
        )
    except ValueError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2

    print(
        json.dumps(
            {
                "status": "ok",
                "result_count": len(results),
                "results": results,
                "privacy": "content_and_source_locations_omitted",
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
