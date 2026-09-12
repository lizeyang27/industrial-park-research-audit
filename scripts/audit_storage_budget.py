#!/usr/bin/env python3
"""Summarize storage metadata and apply deterministic capacity budgets.

The scanner never opens file content. Standard output contains only aggregate
numbers, fixed group labels, and stable issue codes; it omits filenames and
paths even when metadata collection fails.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


SKIP_DIRECTORIES = {".git", ".hg", ".svn", ".venv", "venv", "__pycache__", ".pytest_cache"}
PUBLIC_GROUPS = ("root", "agents", "assets", "docs", "examples", "references", "scripts", "tests", "other")
CORPUS_GROUPS = ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "support")
CORPUS_LAYER_ROOTS = {
    "l0_raw": "L0",
    "l1_index": "L1",
    "l2_article_maps": "L2",
    "l2_maps": "L2",
    "l3_cards": "L3",
    "l3_patterns": "L3",
    "l4_clusters": "L4",
    "l4_contradictions": "L4",
    "l5_routes": "L5",
    "l5_rubrics": "L5",
    "l6_prior_packs": "L6",
    "l6_policy": "L6",
}


@dataclass
class GroupStats:
    file_count: int = 0
    total_bytes: int = 0
    max_file_bytes: int = 0

    def add(self, size: int) -> None:
        self.file_count += 1
        self.total_bytes += size
        self.max_file_bytes = max(self.max_file_bytes, size)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be a positive integer")
    return parsed


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("value must be a non-negative integer")
    return parsed


def public_group(top_level_name: str | None) -> str:
    if top_level_name is None:
        return "root"
    folded = top_level_name.casefold()
    return folded if folded in PUBLIC_GROUPS else "other"


def corpus_group(top_level_name: str | None) -> str:
    if top_level_name is None:
        return "support"
    return CORPUS_LAYER_ROOTS.get(top_level_name.casefold(), "support")


def scan_metadata(
    root: Path,
    group_names: tuple[str, ...],
    classify: Callable[[str | None], str],
) -> tuple[dict[str, GroupStats], Counter[str]]:
    groups = {name: GroupStats() for name in group_names}
    issues: Counter[str] = Counter()
    if not root.is_dir():
        issues["ROOT_NOT_DIRECTORY"] += 1
        return groups, issues

    stack: list[tuple[Path, str | None]] = [(root, None)]
    while stack:
        directory, top_level = stack.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if entry.is_symlink():
                            issues["SYMLINK_SKIPPED"] += 1
                        elif entry.is_dir(follow_symlinks=False):
                            if entry.name.casefold() not in SKIP_DIRECTORIES:
                                entry_top = entry.name if top_level is None else top_level
                                stack.append((Path(entry.path), entry_top))
                        elif entry.is_file(follow_symlinks=False):
                            size = entry.stat(follow_symlinks=False).st_size
                            groups[classify(top_level)].add(size)
                    except OSError:
                        issues["METADATA_SCAN_INCOMPLETE"] += 1
        except OSError:
            issues["METADATA_SCAN_INCOMPLETE"] += 1
    return groups, issues


def scaled(value: int, target_articles: int, sample_articles: int) -> int:
    return (value * target_articles + sample_articles - 1) // sample_articles


def serialized_groups(groups: dict[str, GroupStats], order: tuple[str, ...]) -> list[dict[str, Any]]:
    return [
        {
            "group": name,
            "file_count": groups[name].file_count,
            "total_bytes": groups[name].total_bytes,
            "max_file_bytes": groups[name].max_file_bytes,
        }
        for name in order
    ]


def projected_groups(
    groups: dict[str, GroupStats], order: tuple[str, ...], sample_articles: int, target_articles: int
) -> list[dict[str, Any]]:
    return [
        {
            "group": name,
            "projected_file_count": scaled(groups[name].file_count, target_articles, sample_articles),
            "projected_total_bytes": scaled(groups[name].total_bytes, target_articles, sample_articles),
        }
        for name in order
    ]


def audit(
    root: Path,
    *,
    profile: str,
    sample_articles: int | None = None,
    target_articles: int | None = None,
    soft_budget_bytes: int | None = None,
    hard_budget_bytes: int | None = None,
) -> dict[str, Any]:
    if profile == "public-skill":
        order, classify = PUBLIC_GROUPS, public_group
    elif profile == "layered-corpus":
        order, classify = CORPUS_GROUPS, corpus_group
    else:
        raise ValueError("unsupported profile")

    groups, issues = scan_metadata(root, order, classify)
    total_files = sum(group.file_count for group in groups.values())
    total_bytes = sum(group.total_bytes for group in groups.values())
    max_file_bytes = max((group.max_file_bytes for group in groups.values()), default=0)

    projection: dict[str, Any] = {"enabled": False}
    projection_requested = sample_articles is not None or target_articles is not None
    if projection_requested and (sample_articles is None or target_articles is None):
        issues["PROJECTION_ARGUMENTS_INCOMPLETE"] += 1
    elif sample_articles is not None and target_articles is not None:
        projection = {
            "enabled": True,
            "sample_article_count": sample_articles,
            "target_article_count": target_articles,
            "method": "linear_metadata_bytes_per_article",
            "projected_file_count": scaled(total_files, target_articles, sample_articles),
            "projected_total_bytes": scaled(total_bytes, target_articles, sample_articles),
            "groups": projected_groups(groups, order, sample_articles, target_articles),
        }

    if soft_budget_bytes is not None and hard_budget_bytes is not None and soft_budget_bytes > hard_budget_bytes:
        issues["BUDGET_CONFIGURATION_INVALID"] += 1

    budget_basis = projection.get("projected_total_bytes", total_bytes)
    hard_exceeded = hard_budget_bytes is not None and budget_basis > hard_budget_bytes
    soft_exceeded = soft_budget_bytes is not None and budget_basis > soft_budget_bytes
    if hard_exceeded:
        issues["STORAGE_HARD_BUDGET_EXCEEDED"] += 1
    elif soft_exceeded:
        issues["STORAGE_SOFT_BUDGET_EXCEEDED"] += 1

    fatal_codes = {
        "ROOT_NOT_DIRECTORY",
        "METADATA_SCAN_INCOMPLETE",
        "PROJECTION_ARGUMENTS_INCOMPLETE",
        "BUDGET_CONFIGURATION_INVALID",
        "STORAGE_HARD_BUDGET_EXCEEDED",
    }
    if any(code in issues for code in fatal_codes):
        status = "blocked"
    elif issues.get("STORAGE_SOFT_BUDGET_EXCEEDED") or issues.get("SYMLINK_SKIPPED"):
        status = "warning"
    else:
        status = "within_budget"

    return {
        "schema_version": "0.1",
        "tool": "audit_storage_budget",
        "status": status,
        "profile": profile,
        "observed": {
            "file_count": total_files,
            "total_bytes": total_bytes,
            "max_file_bytes": max_file_bytes,
            "groups": serialized_groups(groups, order),
        },
        "projection": projection,
        "budget": {
            "basis": "projected_total_bytes" if projection.get("enabled") else "observed_total_bytes",
            "evaluated_bytes": budget_basis,
            "soft_budget_bytes": soft_budget_bytes,
            "hard_budget_bytes": hard_budget_bytes,
        },
        "issues": [{"code": code, "count": count} for code, count in sorted(issues.items())],
        "privacy": "aggregate_file_metadata_only",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--profile", choices=("public-skill", "layered-corpus"), required=True)
    parser.add_argument("--sample-articles", type=positive_int)
    parser.add_argument("--target-articles", type=positive_int)
    parser.add_argument("--soft-budget-bytes", type=nonnegative_int)
    parser.add_argument("--hard-budget-bytes", type=nonnegative_int)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit(
            args.root,
            profile=args.profile,
            sample_articles=args.sample_articles,
            target_articles=args.target_articles,
            soft_budget_bytes=args.soft_budget_bytes,
            hard_budget_bytes=args.hard_budget_bytes,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 1 if report["status"] == "blocked" else 0
    except (OSError, ValueError):
        print(json.dumps({"status": "error", "error_code": "STORAGE_AUDIT_FAILED"}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
