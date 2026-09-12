#!/usr/bin/env python3
"""Validate and score the v1.1 C/D/E/F factorial evaluation protocol.

The scorer consumes caller-produced case-group aggregates. It does not call a
model, inspect private knowledge, adjudicate findings, infer missing values, or
estimate tokens and operational costs from characters or bytes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Callable

import knowledge_base


PROTOCOL_ID = "IRA-ABCDEF-V1.1"
SCHEMA_VERSION = "0.1"
GROUPS = ("C", "D", "E", "F")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
TOKEN_FIELD_RE = re.compile(r"^[a-z][a-z0-9_]*_tokens$")
NON_REPORTED_TOKEN_MARKERS = ("estimate", "estimated", "approx", "projected", "planned")
METRIC_CLASSES = {"common_outcome", "group_process"}
METRIC_DIRECTIONS = {"higher_is_better", "lower_is_better", "descriptive"}
OPERATIONAL_COST_FIELDS = ("external_calls", "human_review_minutes")

SHARED_HASH_FIELDS = (
    "draft_sha256",
    "source_pack_sha256",
    "skill_sha256",
    "execution_configuration_hash",
    "prior_manifest_hash",
)

EFFECTS: dict[str, dict[str, Any]] = {
    "support_direct": {"contrast": "D-C", "weights": {"C": -1.0, "D": 1.0}},
    "support_when_framed": {"contrast": "F-E", "weights": {"E": -1.0, "F": 1.0}},
    "frame_without_support": {"contrast": "E-C", "weights": {"C": -1.0, "E": 1.0}},
    "frame_with_support": {"contrast": "F-D", "weights": {"D": -1.0, "F": 1.0}},
    "support_main_effect": {
        "contrast": "((D-C)+(F-E))/2",
        "weights": {"C": -0.5, "D": 0.5, "E": -0.5, "F": 0.5},
    },
    "frame_main_effect": {
        "contrast": "((E-C)+(F-D))/2",
        "weights": {"C": -0.5, "D": -0.5, "E": 0.5, "F": 0.5},
    },
    "interaction": {
        "contrast": "F-E-D+C",
        "weights": {"C": 1.0, "D": -1.0, "E": -1.0, "F": 1.0},
    },
}


class ProtocolError(ValueError):
    """Raised when an input cannot be scored without violating the protocol."""


def _is_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{location} must be an object")
    return value


def _require_hash(value: Any, location: str) -> str:
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ProtocolError(f"{location} must be a lowercase SHA-256 hex digest")
    return value


def is_fully_synthetic_payload(payload: Any) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get("fixture") == "fully_synthetic"
        and payload.get("data_classification") == "public_synthetic_no_private_content"
    )


def _validate_metric_definitions(value: Any) -> dict[str, dict[str, Any]]:
    definitions = _require_object(value, "metric_definitions")
    if not definitions:
        raise ProtocolError("metric_definitions must not be empty")

    validated: dict[str, dict[str, Any]] = {}
    required_fields = {"metric_class", "direction", "range", "applicable_groups"}
    for name, raw_definition in definitions.items():
        location = f"metric_definitions.{name}"
        if not isinstance(name, str) or not name:
            raise ProtocolError("metric_definitions contains an empty or non-string name")
        definition = _require_object(raw_definition, location)
        if set(definition) != required_fields:
            raise ProtocolError(f"{location} must contain exactly {sorted(required_fields)}")

        metric_class = definition.get("metric_class")
        if metric_class not in METRIC_CLASSES:
            raise ProtocolError(f"{location}.metric_class is invalid")
        direction = definition.get("direction")
        if direction not in METRIC_DIRECTIONS:
            raise ProtocolError(f"{location}.direction is invalid")

        raw_range = _require_object(definition.get("range"), f"{location}.range")
        if set(raw_range) != {"minimum", "maximum"}:
            raise ProtocolError(f"{location}.range must contain minimum and maximum")
        minimum = raw_range.get("minimum")
        maximum = raw_range.get("maximum")
        for bound_name, bound in (("minimum", minimum), ("maximum", maximum)):
            if bound is not None and not _is_numeric(bound):
                raise ProtocolError(f"{location}.range.{bound_name} must be finite or null")
        if minimum is None and maximum is None:
            raise ProtocolError(f"{location}.range must have at least one finite bound")
        if minimum is not None and maximum is not None and minimum > maximum:
            raise ProtocolError(f"{location}.range minimum exceeds maximum")

        applicable_groups = definition.get("applicable_groups")
        if (
            not isinstance(applicable_groups, list)
            or not applicable_groups
            or any(group not in GROUPS for group in applicable_groups)
            or len(set(applicable_groups)) != len(applicable_groups)
        ):
            raise ProtocolError(f"{location}.applicable_groups must be a unique non-empty C/D/E/F subset")
        normalized_groups = [group for group in GROUPS if group in applicable_groups]
        if metric_class == "common_outcome" and normalized_groups != list(GROUPS):
            raise ProtocolError(f"{location} common_outcome must apply to C/D/E/F")
        if metric_class == "group_process" and normalized_groups == list(GROUPS):
            raise ProtocolError(f"{location} group_process must apply to a proper group subset")

        validated[name] = {
            "metric_class": metric_class,
            "direction": direction,
            "range": {"minimum": minimum, "maximum": maximum},
            "applicable_groups": normalized_groups,
        }
    return validated


def _validate_metrics(
    value: Any,
    definitions: dict[str, dict[str, Any]],
    group: str,
    location: str,
) -> dict[str, int | float | None]:
    metrics = _require_object(value, location)
    validated: dict[str, int | float | None] = {}
    for name, measurement in metrics.items():
        if name not in definitions:
            raise ProtocolError(f"{location}.{name} has no metric definition")
        if measurement is not None and not _is_numeric(measurement):
            raise ProtocolError(f"{location}.{name} must be a finite number or null")
        definition = definitions[name]
        if group not in definition["applicable_groups"] and measurement is not None:
            raise ProtocolError(f"{location}.{name} is not applicable to group {group}")
        if measurement is not None:
            minimum = definition["range"]["minimum"]
            maximum = definition["range"]["maximum"]
            if minimum is not None and measurement < minimum:
                raise ProtocolError(f"{location}.{name} is below its declared range")
            if maximum is not None and measurement > maximum:
                raise ProtocolError(f"{location}.{name} is above its declared range")
        validated[name] = measurement
    return validated


def _validate_reported_token_usage(value: Any, location: str) -> dict[str, int | None]:
    if value is None:
        return {}
    usage = _require_object(value, location)
    validated: dict[str, int | None] = {}
    for name, measurement in usage.items():
        if (
            not isinstance(name, str)
            or not TOKEN_FIELD_RE.fullmatch(name)
            or any(marker in name for marker in NON_REPORTED_TOKEN_MARKERS)
        ):
            raise ProtocolError(
                f"{location}.{name} is not a reported token field; only non-estimated names ending in _tokens are accepted"
            )
        if measurement is not None and (
            not isinstance(measurement, int) or isinstance(measurement, bool) or measurement < 0
        ):
            raise ProtocolError(f"{location}.{name} must be a non-negative integer or null")
        validated[name] = measurement
    return validated


def _validate_reported_operational_costs(value: Any, location: str) -> dict[str, int | float | None]:
    if value is None:
        return {}
    costs = _require_object(value, location)
    extras = set(costs).difference(OPERATIONAL_COST_FIELDS)
    if extras:
        raise ProtocolError(f"{location} contains unsupported fields")
    validated: dict[str, int | float | None] = {}
    for name, measurement in costs.items():
        if measurement is not None and (not _is_numeric(measurement) or measurement < 0):
            raise ProtocolError(f"{location}.{name} must be a non-negative finite number or null")
        if name == "external_calls" and measurement is not None and not isinstance(measurement, int):
            raise ProtocolError(f"{location}.external_calls must be an integer or null")
        validated[name] = measurement
    return validated


def validate_protocol(payload: Any) -> dict[str, Any]:
    root = _require_object(payload, "root")
    if root.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError(f"schema_version must be {SCHEMA_VERSION}")
    if root.get("protocol_id") != PROTOCOL_ID:
        raise ProtocolError(f"protocol_id must be {PROTOCOL_ID}")
    if not isinstance(root.get("evaluation_id"), str) or not root["evaluation_id"]:
        raise ProtocolError("evaluation_id must be a non-empty string")

    metric_definitions = _validate_metric_definitions(root.get("metric_definitions"))
    cases = root.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ProtocolError("cases must be a non-empty array")

    validated_cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for case_index, raw_case in enumerate(cases):
        case_location = f"cases[{case_index}]"
        case = _require_object(raw_case, case_location)
        case_id = case.get("case_id")
        if not isinstance(case_id, str) or not case_id:
            raise ProtocolError(f"{case_location}.case_id must be a non-empty string")
        if case_id in seen_case_ids:
            raise ProtocolError(f"duplicate case_id: {case_id}")
        seen_case_ids.add(case_id)

        raw_groups = _require_object(case.get("groups"), f"{case_location}.groups")
        missing_groups = [group for group in GROUPS if group not in raw_groups]
        if missing_groups:
            raise ProtocolError(f"{case_id} is missing required groups: {', '.join(missing_groups)}")

        groups: dict[str, dict[str, Any]] = {}
        for group in GROUPS:
            run_location = f"{case_id}.groups.{group}"
            run = _require_object(raw_groups[group], run_location)
            if "actual_usage" in run:
                raise ProtocolError(f"{run_location}.actual_usage is unsupported; use reported_token_usage")
            run_count = run.get("run_count")
            if not isinstance(run_count, int) or isinstance(run_count, bool) or run_count < 1:
                raise ProtocolError(f"{run_location}.run_count must be a positive integer")

            artifacts = _require_object(run.get("artifact_hashes"), f"{run_location}.artifact_hashes")
            normalized_artifacts: dict[str, str | None] = {}
            for field in SHARED_HASH_FIELDS:
                normalized_artifacts[field] = _require_hash(
                    artifacts.get(field), f"{run_location}.artifact_hashes.{field}"
                )

            support_hash = artifacts.get("support_bundle_hash")
            if support_hash is not None:
                support_hash = _require_hash(support_hash, f"{run_location}.artifact_hashes.support_bundle_hash")
            normalized_artifacts["support_bundle_hash"] = support_hash

            framework_hash = artifacts.get("framework_hash")
            if framework_hash is not None:
                framework_hash = _require_hash(framework_hash, f"{run_location}.artifact_hashes.framework_hash")
            normalized_artifacts["framework_hash"] = framework_hash

            groups[group] = {
                "run_count": run_count,
                "artifact_hashes": normalized_artifacts,
                "metrics": _validate_metrics(
                    run.get("metrics"), metric_definitions, group, f"{run_location}.metrics"
                ),
                "reported_token_usage": _validate_reported_token_usage(
                    run.get("reported_token_usage"), f"{run_location}.reported_token_usage"
                ),
                "reported_operational_costs": _validate_reported_operational_costs(
                    run.get("reported_operational_costs"),
                    f"{run_location}.reported_operational_costs",
                ),
            }

        for field in SHARED_HASH_FIELDS:
            values = {groups[group]["artifact_hashes"][field] for group in GROUPS}
            if len(values) != 1:
                raise ProtocolError(f"{case_id} violates shared {field} invariant across C/D/E/F")
        if len({groups[group]["run_count"] for group in GROUPS}) != 1:
            raise ProtocolError(f"{case_id} violates equal run_count invariant across C/D/E/F")

        if groups["C"]["artifact_hashes"]["support_bundle_hash"] is not None:
            raise ProtocolError(f"{case_id} group C must not receive a support bundle")
        if groups["E"]["artifact_hashes"]["support_bundle_hash"] is not None:
            raise ProtocolError(f"{case_id} group E must not receive a support bundle")
        support_d = groups["D"]["artifact_hashes"]["support_bundle_hash"]
        support_f = groups["F"]["artifact_hashes"]["support_bundle_hash"]
        if support_d is None or support_f is None or support_d != support_f:
            raise ProtocolError(f"{case_id} groups D and F must share one non-null support_bundle_hash")

        if groups["C"]["artifact_hashes"]["framework_hash"] is not None:
            raise ProtocolError(f"{case_id} group C must not receive a reading framework")
        if groups["D"]["artifact_hashes"]["framework_hash"] is not None:
            raise ProtocolError(f"{case_id} group D must not receive a reading framework")
        for group in ("E", "F"):
            if groups[group]["artifact_hashes"]["framework_hash"] is None:
                raise ProtocolError(f"{case_id} group {group} must have a non-null framework_hash")

        validated_cases.append({"case_id": case_id, "groups": groups})

    return {"metric_definitions": metric_definitions, "cases": validated_cases}


def _mean(values: list[int | float]) -> float | None:
    if not values:
        return None
    return sum(float(value) for value in values) / len(values)


def _case_reference(case: dict[str, Any], ordinal: int, include_case_ids: bool) -> dict[str, Any]:
    reference: dict[str, Any] = {"case_ordinal": ordinal}
    if include_case_ids:
        reference["case_id"] = case["case_id"]
    return reference


def _score_field(
    cases: list[dict[str, Any]],
    value_getter: Callable[[dict[str, Any], str], int | float | None],
    *,
    applicable_groups: list[str],
    include_case_ids: bool,
    include_observed_sum: bool,
) -> dict[str, Any]:
    applicable = set(applicable_groups)
    per_case_values: list[dict[str, Any]] = []
    values_by_group: dict[str, list[int | float]] = {group: [] for group in GROUPS}

    for ordinal, case in enumerate(cases, start=1):
        group_values = {
            group: value_getter(case, group) if group in applicable else None for group in GROUPS
        }
        for group, value in group_values.items():
            if value is not None:
                values_by_group[group].append(value)
        record = _case_reference(case, ordinal, include_case_ids)
        record["group_values"] = group_values
        per_case_values.append(record)

    group_summaries: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        if group not in applicable:
            summary: dict[str, Any] = {
                "applicable": False,
                "n_observed": 0,
                "n_missing": 0,
                "mean": None,
            }
        else:
            observed = values_by_group[group]
            summary = {
                "applicable": True,
                "n_observed": len(observed),
                "n_missing": len(cases) - len(observed),
                "mean": _mean(observed),
            }
        if include_observed_sum:
            observed = values_by_group[group]
            summary["observed_sum"] = sum(observed) if observed else None
        group_summaries[group] = summary

    effect_summaries: dict[str, dict[str, Any]] = {}
    for effect_name, specification in EFFECTS.items():
        weights: dict[str, float] = specification["weights"]
        required_groups = list(weights)
        if not set(required_groups).issubset(applicable):
            effect_summaries[effect_name] = {
                "contrast": specification["contrast"],
                "applicable": False,
                "reason": "contrast_requires_groups_outside_metric_applicability",
                "required_groups": required_groups,
                "n_pairs": 0,
                "n_missing": 0,
                "mean": None,
                "per_case": [],
            }
            continue

        effects_by_case: list[dict[str, Any]] = []
        observed_effects: list[float] = []
        for case_values in per_case_values:
            missing = [
                group for group in required_groups if case_values["group_values"].get(group) is None
            ]
            if missing:
                value = None
            else:
                value = sum(
                    weights[group] * float(case_values["group_values"][group]) for group in required_groups
                )
                if value == 0:
                    value = 0.0
                observed_effects.append(value)
            record = {key: val for key, val in case_values.items() if key != "group_values"}
            record.update({"value": value, "missing_groups": missing})
            effects_by_case.append(record)
        effect_summaries[effect_name] = {
            "contrast": specification["contrast"],
            "applicable": True,
            "required_groups": required_groups,
            "n_pairs": len(observed_effects),
            "n_missing": len(cases) - len(observed_effects),
            "mean": _mean(observed_effects),
            "per_case": effects_by_case,
        }

    return {
        "applicable_groups": applicable_groups,
        "per_case_values": per_case_values,
        "groups": group_summaries,
        "effects": effect_summaries,
    }


def score_factorial_eval(payload: Any, *, include_case_ids: bool = False) -> dict[str, Any]:
    validated = validate_protocol(payload)
    cases = validated["cases"]
    metric_definitions = validated["metric_definitions"]
    root = _require_object(payload, "root")

    metric_scores: dict[str, Any] = {}
    metric_sets = {"common_outcomes": [], "group_process": []}
    for metric in sorted(metric_definitions):
        definition = metric_definitions[metric]
        metric_scores[metric] = {
            "definition": definition,
            **_score_field(
                cases,
                lambda case, group, metric=metric: case["groups"][group]["metrics"].get(metric),
                applicable_groups=definition["applicable_groups"],
                include_case_ids=include_case_ids,
                include_observed_sum=False,
            ),
        }
        key = "common_outcomes" if definition["metric_class"] == "common_outcome" else "group_process"
        metric_sets[key].append(metric)

    token_fields = sorted(
        {
            name
            for case in cases
            for group in GROUPS
            for name in case["groups"][group]["reported_token_usage"]
        }
    )
    token_scores = {
        field: _score_field(
            cases,
            lambda case, group, field=field: case["groups"][group]["reported_token_usage"].get(field),
            applicable_groups=list(GROUPS),
            include_case_ids=include_case_ids,
            include_observed_sum=True,
        )
        for field in token_fields
    }

    operational_fields = sorted(
        {
            name
            for case in cases
            for group in GROUPS
            for name in case["groups"][group]["reported_operational_costs"]
        }
    )
    operational_scores = {
        field: _score_field(
            cases,
            lambda case, group, field=field: case["groups"][group]["reported_operational_costs"].get(field),
            applicable_groups=list(GROUPS),
            include_case_ids=include_case_ids,
            include_observed_sum=True,
        )
        for field in operational_fields
    }

    run_counts = []
    for ordinal, case in enumerate(cases, start=1):
        record = _case_reference(case, ordinal, include_case_ids)
        record["run_count_per_group"] = case["groups"]["C"]["run_count"]
        run_counts.append(record)

    return {
        "schema_version": SCHEMA_VERSION,
        "protocol_id": PROTOCOL_ID,
        "evaluation_id": root["evaluation_id"],
        "validation": {
            "valid": True,
            "case_count": len(cases),
            "required_groups": list(GROUPS),
            "artifact_check_scope": "hash_presence_and_equality_only",
            "invariants_checked": [
                "shared_draft_source_skill_execution_configuration_and_prior_hashes",
                "equal_run_count_within_case_across_c_d_e_f",
                "support_bundle_absent_in_c_and_e",
                "support_bundle_hash_identity_for_d_and_f",
                "framework_absent_in_c_and_d_and_present_in_e_and_f",
            ],
        },
        "identifier_policy": {
            "case_ids_included": include_case_ids,
            "default_for_non_synthetic": "case_ordinal_only",
        },
        "execution_control": {
            "execution_configuration_hash_scope": (
                "model_reasoning_context_output_network_tool_retry_and_output_contract; "
                "group_treatment_artifacts_excluded"
            ),
            "run_counts": run_counts,
        },
        "arithmetic_scope": {
            "unit": "case_group_aggregate",
            "aggregation": "unweighted_arithmetic_mean_of_complete_within_case_contrasts",
            "missingness": "pairwise_complete_per_case_no_zero_imputation",
            "not_computed": [
                "run_level_pooling",
                "document_length_weighting",
                "bootstrap_interval",
                "statistical_significance",
                "causal_effect_beyond_protocol_contrasts",
            ],
        },
        "metric_sets": metric_sets,
        "metrics": metric_scores,
        "reported_token_usage": {
            "provenance": "caller_reported",
            "reporting_rule": "reported_token_fields_only",
            "derived_from_characters_or_bytes": False,
            "unreported_token_fields_inferred": False,
            "fields": token_scores,
        },
        "reported_operational_costs": {
            "provenance": "caller_reported",
            "reporting_rule": "reported_values_only",
            "unreported_values_inferred": False,
            "fields": operational_scores,
        },
    }


def _render_json(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    allow_identical_existing: bool = False,
) -> str:
    rendered = _render_json(payload)
    if path.exists():
        if allow_identical_existing:
            try:
                if path.is_file() and path.read_bytes() == rendered:
                    return "unchanged_identical"
            except OSError as exc:
                raise ProtocolError("EXISTING_OUTPUT_READ_ERROR") from exc
        raise ProtocolError("OUTPUT_ALREADY_EXISTS")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("xb") as stream:
            stream.write(rendered)
    except FileExistsError as exc:
        raise ProtocolError("OUTPUT_ALREADY_EXISTS") from exc
    except OSError as exc:
        raise ProtocolError("OUTPUT_WRITE_ERROR") from exc
    return "written"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="JSON file containing C/D/E/F case results")
    parser.add_argument("--output", type=Path, help="write the aggregate JSON to this path")
    parser.add_argument(
        "--include-private-case-ids",
        action="store_true",
        help="retain case IDs in a private output; stdout remains identifier-free",
    )
    parser.add_argument(
        "--allow-identical-existing",
        action="store_true",
        help="succeed without rewriting only when the existing output is byte-identical",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload: Any = None
    fully_synthetic = False
    try:
        if args.allow_identical_existing and args.output is None:
            raise ProtocolError("IDEMPOTENT_OUTPUT_POLICY_REQUIRES_OUTPUT")
        if args.include_private_case_ids and args.output is None:
            raise ProtocolError("PRIVATE_CASE_IDS_REQUIRE_OUTPUT")

        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        fully_synthetic = is_fully_synthetic_payload(payload)
        if not fully_synthetic:
            if args.output is None:
                raise ProtocolError("PRIVATE_EVALUATION_REQUIRES_OUTPUT")
            knowledge_base.ensure_private_output_path(args.output)

        include_case_ids = fully_synthetic or args.include_private_case_ids
        result = score_factorial_eval(payload, include_case_ids=include_case_ids)
        write_status = None
        if args.output:
            write_status = write_json(
                args.output,
                result,
                allow_identical_existing=args.allow_identical_existing,
            )
    except (
        OSError,
        json.JSONDecodeError,
        ProtocolError,
        knowledge_base.PriorManifestError,
    ) as error:
        message = str(error) if fully_synthetic else "PRIVATE_EVALUATION_VALIDATION_FAILED"
        print(
            json.dumps(
                {"valid": False, "error_type": type(error).__name__, "message": message},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 2

    if args.output:
        status = {
            "valid": True,
            "protocol_id": result["protocol_id"],
            "case_count": result["validation"]["case_count"],
            "write_status": write_status,
        }
        if fully_synthetic:
            status.update(
                {
                    "evaluation_id": result["evaluation_id"],
                    "output_name": args.output.name,
                }
            )
        else:
            status.update(
                {
                    "output_written_or_identical": True,
                    "privacy": "evaluation_case_and_path_identifiers_omitted",
                }
            )
        print(json.dumps(status, ensure_ascii=False))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
