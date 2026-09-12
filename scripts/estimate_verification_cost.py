#!/usr/bin/env python3
"""Estimate staged model-token ranges for a research verification plan.

The result is deliberately a planning range. It is not a tokenizer, usage meter,
price calculator, or promise about a particular model or host environment.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def _pair(low: float, high: float) -> tuple[int, int]:
    return math.ceil(low), math.ceil(high)


def _add(*bands: tuple[int, int]) -> tuple[int, int]:
    return sum(item[0] for item in bands), sum(item[1] for item in bands)


def _band(low: int, high: int) -> dict[str, int]:
    return {"low": int(low), "high": int(high)}


def estimate_cost(
    *,
    document_chars: int,
    material_claims: int,
    provided_sources: int,
    v1_issues: int,
    v2_issues: int,
    v3_issues: int,
    live_search_issues: int = 0,
    retrieved_kb_cards: int = 0,
) -> dict[str, Any]:
    values = {
        "document_chars": document_chars,
        "material_claims": material_claims,
        "provided_sources": provided_sources,
        "v1_issues": v1_issues,
        "v2_issues": v2_issues,
        "v3_issues": v3_issues,
        "live_search_issues": live_search_issues,
        "retrieved_kb_cards": retrieved_kb_cards,
    }
    if any(not isinstance(value, int) or value < 0 for value in values.values()):
        raise ValueError("all planning inputs must be non-negative integers")
    if live_search_issues > v2_issues + v3_issues:
        raise ValueError("live_search_issues cannot exceed V2 plus V3 issues")

    # Chinese and mixed-language tokenization varies. The wide context range
    # also absorbs prompt framing, table markup, and selective reference loading.
    context = _pair(document_chars * 0.75, document_chars * 1.60)
    decomposition = _pair(material_claims * 90, material_claims * 260)
    supplied_evidence = _pair(provided_sources * 250, provided_sources * 800)
    workflow_overhead = (1200, 3200)
    v1_work = _pair(v1_issues * 300, v1_issues * 900)
    initial_v1 = _add(context, decomposition, supplied_evidence, workflow_overhead, v1_work)

    v2_reasoning = _pair(v2_issues * 2600, v2_issues * 8000)
    live_research = _pair(live_search_issues * 1500, live_search_issues * 5000)
    v2_increment = _add(v2_reasoning, live_research)

    v3_reasoning = _pair(v3_issues * 7000, v3_issues * 22000)
    kb_context = _pair(retrieved_kb_cards * 250, retrieved_kb_cards * 900)
    v3_increment = _add(v3_reasoning, kb_context)
    all_levels = _add(initial_v1, v2_increment, v3_increment)

    return {
        "schema_version": "0.2",
        "estimate_kind": "planning_range_not_measured_usage",
        "inputs": values,
        "estimated_model_tokens": {
            "initial_triage_and_v1": _band(*initial_v1),
            "optional_v2_increment": _band(*v2_increment),
            "optional_v3_increment": _band(*v3_increment),
            "all_levels_total": _band(*all_levels),
        },
        "non_token_requirements": {
            "v2": "may require current primary sources and stakeholder or policy review",
            "v3": "may require private knowledge retrieval, comparable cases, or a domain expert; tokens cannot replace unavailable expertise",
        },
        "assumptions": [
            "The document is selectively loaded rather than duplicated in every step.",
            "Each issue is atomic; mixed policy and industry-mechanism questions are counted separately.",
            "Tool calls, caching, model choice, language, and output length can materially change actual usage.",
            "Local Python extraction and validation do not themselves consume model tokens.",
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--document-chars", type=int, required=True)
    parser.add_argument("--claims", type=int, required=True)
    parser.add_argument("--sources", type=int, default=0)
    parser.add_argument("--v1", type=int, default=0)
    parser.add_argument("--v2", type=int, default=0)
    parser.add_argument("--v3", type=int, default=0)
    parser.add_argument("--live-search", type=int, default=0)
    parser.add_argument("--kb-cards", type=int, default=0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        payload = estimate_cost(
            document_chars=args.document_chars,
            material_claims=args.claims,
            provided_sources=args.sources,
            v1_issues=args.v1,
            v2_issues=args.v2,
            v3_issues=args.v3,
            live_search_issues=args.live_search,
            retrieved_kb_cards=args.kb_cards,
        )
    except ValueError as exc:
        raise SystemExit(f"invalid estimate: {exc}") from exc
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
        print(json.dumps({"output_name": args.output.name}, ensure_ascii=False))
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
