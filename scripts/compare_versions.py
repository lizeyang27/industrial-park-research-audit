#!/usr/bin/env python3
"""Compare two research-document versions without retaining raw text by default."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from extract_review_context import MAX_TEXT_CHARS, extract_material


def stable_id(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"DOC-{digest.hexdigest()[:12]}"


def units(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"\n+", text) if line.strip()]


def change_counts(before: list[str], after: list[str]) -> dict[str, int]:
    matcher = difflib.SequenceMatcher(a=before, b=after)
    counts = {"equal_blocks": 0, "insert_blocks": 0, "delete_blocks": 0, "replace_blocks": 0}
    for tag, *_ in matcher.get_opcodes():
        counts[f"{tag}_blocks"] += 1
    return counts


def build_payload(before_path: Path, after_path: Path, include_diff_text: bool) -> dict[str, Any]:
    before = extract_material(before_path, MAX_TEXT_CHARS)["text"]
    after = extract_material(after_path, MAX_TEXT_CHARS)["text"]
    before_units = units(before)
    after_units = units(after)
    matcher = difflib.SequenceMatcher(a=before_units, b=after_units)
    payload: dict[str, Any] = {
        "schema_version": "0.1",
        "before_id": stable_id(before_path),
        "after_id": stable_id(after_path),
        "before_chars": len(before),
        "after_chars": len(after),
        "char_delta": len(after) - len(before),
        "paragraph_delta": len(after_units) - len(before_units),
        "paragraph_similarity": round(matcher.ratio(), 6),
        "change_blocks": change_counts(before_units, after_units),
        "diff_text_included": include_diff_text,
        "local_paths_included": False,
    }
    if include_diff_text:
        payload["unified_diff"] = list(
            difflib.unified_diff(
                before_units,
                after_units,
                fromfile="before",
                tofile="after",
                lineterm="",
            )
        )
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-diff-text", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = build_payload(args.before, args.after, args.include_diff_text)
    write_json(args.output, payload)
    if args.include_diff_text:
        print("warning: output contains explicitly requested source text", file=sys.stderr)
    print(json.dumps({"before_id": payload["before_id"], "after_id": payload["after_id"], "output_name": args.output.name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
