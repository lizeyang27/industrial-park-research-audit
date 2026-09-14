#!/usr/bin/env python3
"""Validate the reader-facing layer of an industry research audit report.

The validator intentionally emits only aggregate counts and stable error codes.
It does not echo report text, identifiers, or local paths.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


REQUIRED_SECTIONS = (
    "结论",
    "这篇文章已经做好的地方",
    "发布前需要处理",
    "建议怎么改",
    "可继续研究的新观点",
    "如果还要继续核验",
    "本次审阅没有验证什么",
)
PUBLICATION_STATUSES = ("可以发布", "修改后发布", "暂缓发布")
INTERNAL_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:R[1-3]|V[1-3]|T[1-3]|D[0-4]|C[0-7]|G[0-3])(?![A-Za-z0-9_])"
)
ENGLISH_STATUS_RE = re.compile(r"(?<![A-Za-z0-9_])(?:ready|revise|hold)(?![A-Za-z0-9_])", re.IGNORECASE)
MACHINE_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"claim_id|issue_id|source_anchor|claim_type|evidence_status|source_locator|"
    r"hidden_assumptions|counterevidence|granularity|text_detectability|"
    r"discovery_channels?|discovery_origin|prior_question_refs|support_item_refs|"
    r"framework_node_refs|framework_mapping_status|risk_level|verification_level|"
    r"level_basis|required_evidence|required_reviewer|can_auto_close|closure_routes?|"
    r"closure_condition|workflow_state|disposition|publication_effect|closure_note|"
    r"reopen_conditions"
    r")(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
INTERNAL_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:CLM|ISS|PQ|ESI|KC|CARD|NODE|FW)-[A-Za-z0-9][A-Za-z0-9_-]*",
    re.IGNORECASE,
)
TECHNICAL_APPENDIX_RE = re.compile(r"(?m)^#{1,6}\s+技术附录\s*$")
SECOND_LEVEL_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")


def split_reader_layer(text: str) -> str:
    match = TECHNICAL_APPENDIX_RE.search(text)
    return text[: match.start()] if match else text


def section_positions(reader_text: str) -> dict[str, int]:
    positions: dict[str, int] = {}
    for match in SECOND_LEVEL_HEADING_RE.finditer(reader_text):
        title = match.group(1).strip()
        if title in REQUIRED_SECTIONS and title not in positions:
            positions[title] = match.start()
    return positions


def publication_item_count(reader_text: str, positions: dict[str, int]) -> int:
    start = positions.get("发布前需要处理")
    if start is None:
        return 0
    later = [offset for title, offset in positions.items() if offset > start and title != "发布前需要处理"]
    end = min(later) if later else len(reader_text)
    section = reader_text[start:end]
    return len(re.findall(r"(?m)^###\s+(?:\d+[.、]\s*)?.+\S\s*$", section))


def section_body(reader_text: str, positions: dict[str, int], name: str) -> str:
    start = positions.get(name)
    if start is None:
        return ""
    later = [offset for offset in positions.values() if offset > start]
    end = min(later) if later else len(reader_text)
    return reader_text[start:end]


def validate_report(text: str) -> dict[str, object]:
    reader_text = split_reader_layer(text)
    positions = section_positions(reader_text)
    errors: list[str] = []

    missing = [section for section in REQUIRED_SECTIONS if section not in positions]
    if missing:
        errors.append("MISSING_REQUIRED_SECTION")
    elif [positions[name] for name in REQUIRED_SECTIONS] != sorted(positions.values()):
        errors.append("SECTION_ORDER_INVALID")

    conclusion = section_body(reader_text, positions, "结论")
    status_count = sum(conclusion.count(status) for status in PUBLICATION_STATUSES)
    if status_count == 0:
        errors.append("MISSING_CHINESE_PUBLICATION_STATUS")
    elif status_count > 1:
        errors.append("MULTIPLE_PUBLICATION_STATUSES")

    if INTERNAL_CODE_RE.search(reader_text):
        errors.append("INTERNAL_CODE_IN_READER_LAYER")
    if MACHINE_FIELD_RE.search(reader_text):
        errors.append("MACHINE_FIELD_IN_READER_LAYER")
    if INTERNAL_ID_RE.search(reader_text):
        errors.append("INTERNAL_ID_IN_READER_LAYER")
    if ENGLISH_STATUS_RE.search(reader_text):
        errors.append("ENGLISH_STATUS_IN_READER_LAYER")

    item_count = publication_item_count(reader_text, positions)
    if item_count > 8:
        errors.append("TOO_MANY_PRIORITY_ITEMS")

    unique_errors = sorted(set(errors))
    return {
        "ok": not unique_errors,
        "error_count": len(unique_errors),
        "error_codes": unique_errors,
        "required_section_count": len(positions),
        "priority_item_count": item_count,
        "reader_character_count": len(reader_text),
        "technical_appendix_present": bool(TECHNICAL_APPENDIX_RE.search(text)),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a plain-language Chinese user report.")
    parser.add_argument("report", type=Path, help="UTF-8 Markdown report to validate.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the privacy-minimized JSON result.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        text = args.report.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError):
        result: dict[str, object] = {
            "ok": False,
            "error_count": 1,
            "error_codes": ["REPORT_READ_ERROR"],
        }
        print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
        return 2

    result = validate_report(text)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
