#!/usr/bin/env python3
"""Promote human-accepted private L3 abstractions into prior prompt cards.

The output is private and remains a review-prompt interface. Standard output is
aggregate-only: it never includes card text, titles, identifiers, source
locators, filenames, or paths.
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import knowledge_base


PROMOTION_VERSION = "l3-prior-promotion-v0.1"
PROMOTABLE_TYPES = {"expert_heuristic", "control"}
ABSTRACT_BOUNDARIES = {
    "expert_heuristic": {"inference", "opinion"},
    "control": {"process_control", "inference", "opinion"},
}
CONFIDENCE_LEVELS = {"low", "medium", "high"}
VOLATILITIES = {"static", "slow", "dynamic"}


class PromotionInputError(RuntimeError):
    """An input could not be safely processed without exposing private data."""


def _string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> bool:
    return isinstance(value, list) and all(_string(item) for item in value)


def _unique_strings(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not _string(value) or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _date_part(value: Any) -> str | None:
    """Return an ISO date from an ISO date or timezone-aware date-time."""

    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not a string")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone required")
        return parsed.date().isoformat()


def load_l3_records(path: Path) -> list[tuple[int, dict[str, Any]]]:
    """Read JSON Lines without placing content or path details in errors."""

    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError as exc:
        raise PromotionInputError("INPUT_FILE_READ_ERROR") from exc

    records: list[tuple[int, dict[str, Any]]] = []
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise PromotionInputError("INPUT_JSON_INVALID") from exc
        if not isinstance(value, dict):
            raise PromotionInputError("INPUT_RECORD_NOT_OBJECT")
        records.append((line_number, value))
    return records


def _structure_is_convertible(record: dict[str, Any]) -> bool:
    if not all(
        _string(record.get(key))
        for key in ("record_id", "title", "paraphrased_content", "rights_ref", "method_version")
    ):
        return False
    if not all(
        _string_list(record.get(key))
        for key in ("tags", "parent_ids", "independent_source_ids", "limitations", "counterevidence_refs")
    ):
        return False
    if not _string(record.get("falsification_test")):
        return False

    input_hashes = record.get("input_hashes")
    if not isinstance(input_hashes, list) or any(
        not isinstance(item, dict) or not _string(item.get("object_id"))
        for item in input_hashes
    ):
        return False

    source_refs = record.get("source_refs")
    if not isinstance(source_refs, list) or not source_refs:
        return False
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            return False
        if not _string(source_ref.get("article_id")) or not _string(source_ref.get("source_version_id")):
            return False
        locators = source_ref.get("locator_refs")
        if not isinstance(locators, list) or not locators:
            return False
        for locator in locators:
            if not isinstance(locator, dict):
                return False
            if not all(
                _string(locator.get(key)) for key in ("source_version_id", "chunk_id")
            ):
                return False
            if not _string_list(locator.get("section_path")):
                return False
            if any(
                not isinstance(locator.get(key), int) or locator[key] < 0
                for key in ("paragraph_index", "char_start", "char_end")
            ):
                return False

    epistemic = record.get("epistemic")
    if not isinstance(epistemic, dict) or epistemic.get("confidence") not in CONFIDENCE_LEVELS:
        return False

    scope = record.get("scope")
    if not isinstance(scope, dict) or not all(
        _string_list(scope.get(key))
        for key in ("domains", "jurisdictions", "entity_types", "applies_when", "does_not_apply_when")
    ):
        return False

    temporal = record.get("temporal")
    if not isinstance(temporal, dict) or temporal.get("volatility") not in VOLATILITIES:
        return False
    try:
        for key in ("as_of", "last_verified_at", "valid_until"):
            _date_part(temporal.get(key))
    except (TypeError, ValueError):
        return False

    verification = record.get("verification")
    if not isinstance(verification, dict) or not _string(verification.get("next_check")):
        return False

    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, dict):
        return False
    if not all(_string_list(lifecycle.get(key)) for key in ("supersedes", "superseded_by")):
        return False
    try:
        _date_part(lifecycle.get("review_by"))
    except (TypeError, ValueError):
        return False

    return isinstance(record.get("human_review"), dict)


def _authorization_issue(
    record: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    at_date: date,
) -> str | None:
    authorization = authorizations.get(record.get("rights_ref"))
    if authorization is None:
        return "AUTHORIZATION_NOT_FOUND"
    if authorization.get("status") != "active":
        return "AUTHORIZATION_NOT_ACTIVE"

    try:
        valid_from = knowledge_base.parse_date(authorization.get("valid_from"))
        expires_at = knowledge_base.parse_date(authorization.get("expires_at"))
    except ValueError:
        return "AUTHORIZATION_DATE_INVALID"
    if valid_from is not None and at_date < valid_from:
        return "AUTHORIZATION_NOT_YET_VALID"
    if expires_at is not None and at_date > expires_at:
        return "AUTHORIZATION_EXPIRED"
    if authorization.get("abstract_guidance_allowed") is not True:
        return "ABSTRACT_GUIDANCE_DENIED"
    if "local-review" not in authorization.get("allowed_uses", []):
        return "AUTHORIZATION_LOCAL_REVIEW_DENIED"

    allowed_processors = authorization.get("allowed_processors")
    if allowed_processors is None or "model_context_abstract" not in allowed_processors:
        return "AUTHORIZATION_ABSTRACT_PROCESSOR_DENIED"
    output_audiences = authorization.get("output_audiences", ["private_review"])
    if "private_review" not in output_audiences:
        return "AUTHORIZATION_PRIVATE_OUTPUT_DENIED"
    return None


def eligibility_issue(
    record: dict[str, Any],
    authorizations: dict[str, dict[str, Any]],
    *,
    at_date: date,
) -> str | None:
    """Return one stable skip code, ordered from governance gate to structure."""

    card_type = record.get("card_type")
    if card_type not in PROMOTABLE_TYPES:
        return "CARD_TYPE_NOT_PROMOTABLE"

    human_review = record.get("human_review")
    if not isinstance(human_review, dict) or human_review.get("status") != "accepted":
        return "HUMAN_REVIEW_NOT_ACCEPTED"

    lifecycle = record.get("lifecycle")
    if not isinstance(lifecycle, dict) or lifecycle.get("status") != "active":
        return "LIFECYCLE_NOT_ACTIVE"

    epistemic = record.get("epistemic")
    boundary = epistemic.get("fact_inference_boundary") if isinstance(epistemic, dict) else None
    if boundary not in ABSTRACT_BOUNDARIES[card_type]:
        return "ABSTRACT_BOUNDARY_REQUIRED"

    if not _structure_is_convertible(record):
        return "INPUT_CARD_INVALID"

    return _authorization_issue(record, authorizations, at_date=at_date)


def _source_ids(record: dict[str, Any]) -> list[str]:
    values: list[Any] = [record.get("record_id"), record.get("corpus_id")]
    values.extend(record.get("parent_ids", []))
    values.extend(item.get("object_id") for item in record.get("input_hashes", []))
    for source_ref in record.get("source_refs", []):
        values.extend((source_ref.get("article_id"), source_ref.get("source_version_id")))
        for locator in source_ref.get("locator_refs", []):
            values.extend((locator.get("source_version_id"), locator.get("chunk_id")))
    values.extend(record.get("independent_source_ids", []))
    return _unique_strings(values)


def _locators(record: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for source_ref in record.get("source_refs", []):
        for locator in source_ref.get("locator_refs", []):
            values.append("l3-locator:" + knowledge_base.canonical_json(locator))
    return _unique_strings(values)


def _review_by(record: dict[str, Any], authorization: dict[str, Any], at_date: date) -> str:
    """Use the earliest explicit boundary; otherwise require immediate re-review."""

    candidates: list[date] = []
    for value in (record["lifecycle"].get("review_by"), authorization.get("expires_at")):
        parsed = _date_part(value)
        if parsed is not None:
            candidates.append(date.fromisoformat(parsed))
    return min(candidates).isoformat() if candidates else at_date.isoformat()


def convert_record(
    record: dict[str, Any],
    authorization: dict[str, Any],
    *,
    at_date: date,
) -> dict[str, Any]:
    """Map one accepted abstraction to the private public-interface shape."""

    record_id = record["record_id"]
    card_id = "KC-PRIOR-" + knowledge_base.canonical_hash({"record_id": record_id})[:20].upper()
    card_type = record["card_type"]
    source_boundary = record["epistemic"]["fact_inference_boundary"]
    output_boundary = source_boundary if source_boundary in {"inference", "opinion"} else "opinion"
    volatility = record["temporal"]["volatility"]

    limitations = _unique_strings(
        [
            *record["limitations"],
            "This card is an abstract review prompt, not factual evidence.",
            "Source-reported statements require independent verification before factual use.",
        ]
    )
    exclusions = record["scope"]["does_not_apply_when"]
    conditions_where_false = exclusions or ["The stated applicability conditions do not hold."]

    return {
        "schema_version": knowledge_base.SCHEMA_VERSION,
        "card_id": card_id,
        "card_type": card_type,
        "title": record["title"],
        "tags": _unique_strings([*record["tags"], "research-review"]),
        "content": {
            "claim_or_guidance": record["paraphrased_content"],
            "rationale": "Human-accepted abstract guidance; it remains a review prompt rather than factual evidence.",
            "recommended_action": record["verification"]["next_check"],
        },
        "provenance": {
            "source_ids": _source_ids(record),
            "origin_type": "analysis",
            "locators": _locators(record),
            "transformation": _unique_strings(
                [record["method_version"], "human-review-accepted", PROMOTION_VERSION]
            ),
            "independent_source_ids": _unique_strings(record["independent_source_ids"]),
            "content_hash": knowledge_base.canonical_hash(record),
        },
        "epistemic": {
            "status": "reported",
            "fact_inference_boundary": output_boundary,
            "confidence": record["epistemic"]["confidence"],
            "assumptions": [],
            "limitations": limitations,
            "counterevidence_refs": _unique_strings(record["counterevidence_refs"]),
            "conditions_where_false": _unique_strings(conditions_where_false),
            "falsification_test": record["falsification_test"],
        },
        "scope": {
            "domains": _unique_strings(record["scope"]["domains"]),
            "jurisdictions": _unique_strings(record["scope"]["jurisdictions"]),
            "entity_types": _unique_strings(record["scope"]["entity_types"]),
            "task_stages": ["reasoning-audit", "full-review"],
            "applies_when": _unique_strings(record["scope"]["applies_when"]),
            "does_not_apply_when": _unique_strings(exclusions),
        },
        "temporal": {
            "as_of": _date_part(record["temporal"].get("as_of")),
            "last_verified_at": None,
            "valid_until": _date_part(record["temporal"].get("valid_until")),
            "volatility": volatility,
            "requires_live_verification": volatility == "dynamic",
        },
        "rights_and_access": {
            "sensitivity": "private",
            "rights_basis": "user_authorized",
            "authorization_id": record["rights_ref"],
            "allowed_uses": _unique_strings(authorization["allowed_uses"]),
            "verbatim_quote_allowed": False,
            "export_allowed": False,
        },
        "lifecycle": {
            "status": "active",
            "owner": "ROLE-HUMAN-APPROVER",
            "review_by": _review_by(record, authorization, at_date),
            "supersedes": _unique_strings(record["lifecycle"]["supersedes"]),
            "superseded_by": _unique_strings(record["lifecycle"]["superseded_by"]),
        },
    }


def promote_records(
    records: list[tuple[int, dict[str, Any]]],
    authorizations: dict[str, dict[str, Any]],
    *,
    at_date: date,
) -> tuple[list[dict[str, Any]], Counter[str]]:
    promoted: list[dict[str, Any]] = []
    issues: Counter[str] = Counter()
    for _, record in records:
        issue = eligibility_issue(record, authorizations, at_date=at_date)
        if issue is not None:
            issues[issue] += 1
            continue
        authorization = authorizations[record["rights_ref"]]
        promoted.append(convert_record(record, authorization, at_date=at_date))

    output_issues = knowledge_base.validate_cards(list(enumerate(promoted, start=1)))
    if output_issues:
        raise PromotionInputError("OUTPUT_CARD_INVALID")
    return promoted, issues


def write_private_jsonl(path: Path, cards: list[dict[str, Any]], *, force: bool = False) -> None:
    knowledge_base.ensure_private_output_path(path)
    if path.exists() and not force:
        raise PromotionInputError("OUTPUT_EXISTS")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=".promotion-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            for card in cards:
                stream.write(json.dumps(card, ensure_ascii=False, sort_keys=True) + "\n")
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except (OSError, UnboundLocalError):
            pass
        raise PromotionInputError("OUTPUT_WRITE_ERROR") from exc


def _report(
    *,
    status: str,
    input_count: int,
    promoted_count: int,
    skipped_count: int,
    issues: Counter[str],
) -> None:
    print(
        json.dumps(
            {
                "status": status,
                "input_count": input_count,
                "promoted_count": promoted_count,
                "skipped_count": skipped_count,
                "issue_counts": dict(sorted(issues.items())),
                "privacy": "content_titles_sources_ids_locators_and_paths_omitted",
            },
            sort_keys=True,
        )
    )


def parse_cli_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected an ISO date in YYYY-MM-DD form.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Promote accepted private L3 abstractions into private prior prompt cards."
    )
    parser.add_argument("l3_cards", type=Path)
    parser.add_argument("--authorizations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--at-date", type=parse_cli_date, default=date.today())
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    records: list[tuple[int, dict[str, Any]]] = []
    try:
        knowledge_base.ensure_private_output_path(args.output)
        records = load_l3_records(args.l3_cards)
        try:
            authorizations = knowledge_base.load_authorizations(args.authorizations)
        except knowledge_base.AuthorizationError as exc:
            raise PromotionInputError("AUTHORIZATION_FILE_INVALID") from exc
        promoted, issues = promote_records(records, authorizations, at_date=args.at_date)
        write_private_jsonl(args.output, promoted, force=args.force)
    except (PromotionInputError, knowledge_base.PriorManifestError) as exc:
        code = (
            "OUTPUT_NOT_IN_PRIVATE_OR_IGNORED_LOCATION"
            if isinstance(exc, knowledge_base.PriorManifestError)
            else str(exc)
        )
        _report(
            status="error",
            input_count=len(records),
            promoted_count=0,
            skipped_count=len(records),
            issues=Counter({code: 1}),
        )
        return 2

    _report(
        status="ok",
        input_count=len(records),
        promoted_count=len(promoted),
        skipped_count=len(records) - len(promoted),
        issues=issues,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
