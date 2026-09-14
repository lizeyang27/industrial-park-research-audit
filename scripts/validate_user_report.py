#!/usr/bin/env python3
"""Validate the reader-facing layer of an industry research audit report.

The validator intentionally emits only aggregate counts and stable error codes.
It does not echo report text, identifiers, or local paths.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
import unicodedata
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
ZERO_ISSUE_MARKER = "本稿没有仍需在发布前处理的问题"
INTERNAL_CODE_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:R[1-3]|V[1-3]|T[1-3]|D[0-4]|C[0-7]|G[0-3])(?![A-Za-z0-9_])"
)
ENGLISH_STATUS_RE = re.compile(r"(?<![A-Za-z0-9_])(?:ready|revise|hold)(?![A-Za-z0-9_])", re.IGNORECASE)
MACHINE_FIELD_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:"
    r"claim_id|issue_id|source_anchor|claim_type|evidence_status|source_locator|"
    r"evidence_id|action_id|closure_id|input_id|viewpoint_id|claim_refs|evidence_refs|fit_target_refs|"
    r"reader_binding|reader_item_sha256|reader_grouping_reason|item_index|"
    r"location_excerpt|problem_excerpt|reason_excerpt|action_excerpt|supplied_inputs|media_type|captured_at|"
    r"calculation_expression|calculation_result|calculation_operands|derived_result|calculation_evidence_ref|"
    r"closure_evidence_refs|verification_action_refs|closure_decision_ref|"
    r"requires_external_evidence|input_evidence_refs|predicate_results|human_reviewed|"
    r"draft_sha256|report_sha256|framework_timing|web_trace|publication_status|"
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
    r"(?<![A-Za-z0-9_])(?:CLM|ISS|EV|ACT|CLS|SUP|PQ|ESI|KC|CARD|NODE|FW)-[A-Za-z0-9][A-Za-z0-9_-]*",
    re.IGNORECASE,
)
TECHNICAL_APPENDIX_RE = re.compile(r"(?m)^##\s+技术附录\s*$")
SECOND_LEVEL_HEADING_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
HIDDEN_OR_FENCED_RE = re.compile(r"<!--|-->|^\s*(?:```|~~~)", re.MULTILINE)
RAW_HTML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
READY_CONTRADICTION_RE = re.compile(
    r"(?:(?:请勿|勿|禁止|严禁|不要|切勿|先别|暂缓|暂不|推迟|延后|搁置|停止|取消|"
    r"不适合|不具备|不满足|不得|不能|不可|不应|不宜|尚不能|不建议)"
    r".{0,12}(?:发布|上线|公开|刊发|推送))|"
    r"(?:(?:发布|上线|公开|刊发|推送).{0,12}"
    r"(?:请勿|勿|禁止|严禁|不要|切勿|暂缓|暂不|推迟|延后|搁置|停止|取消|撤回|下线|"
    r"不适合|不具备|不满足|不得|不能|不可|不应|不宜))|"
    r"(?:立即|应当|需要|建议|必须|仍需|先行)?.{0,4}(?:下线|撤回|作废|取消发稿|搁置)|"
    r"结论.{0,4}(?:不成立|作废)|"
    r"(?:当前|本|该)?版本.{0,4}(?:仅|只)(?:供|限于).{0,4}内部(?:流转|审阅|使用|参考)|"
    r"(?:完成|待).{0,8}(?:修改|补证|核验|整改).{0,8}(?:后再|再)(?:对外|公开|发布|上线|刊发|推送)"
)
THIRD_LEVEL_HEADING_RE = re.compile(r"(?m)^###\s+.+\S\s*$")
PRIORITY_LABEL_PATTERNS = {
    "原文位置": re.compile(r"(?m)^\s*-\s*原文位置\s*[：:]\s*(\S.*)$"),
    "为什么重要": re.compile(r"(?m)^\s*-\s*为什么重要\s*[：:]\s*(\S.*)$"),
    "建议处理": re.compile(r"(?m)^\s*-\s*建议处理\s*[：:]\s*(\S.*)$"),
}
VIEWPOINT_LABEL_PATTERNS = {
    "适用对象": re.compile(r"(?m)^\s*-\s*适用对象\s*[：:]\s*(\S.*)$"),
    "决策问题": re.compile(r"(?m)^\s*-\s*决策问题\s*[：:]\s*(\S.*)$"),
    "可能机制": re.compile(r"(?m)^\s*-\s*可能机制\s*[：:]\s*(\S.*)$"),
    "适用边界": re.compile(r"(?m)^\s*-\s*适用边界\s*[：:]\s*(\S.*)$"),
    "反证条件": re.compile(r"(?m)^\s*-\s*反证条件\s*[：:]\s*(\S.*)$"),
}


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
    return section_item_count(reader_text, positions, "发布前需要处理")


def section_item_count(reader_text: str, positions: dict[str, int], name: str) -> int:
    start = positions.get(name)
    if start is None:
        return 0
    later = [offset for title, offset in positions.items() if offset > start and title != name]
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


def section_content(reader_text: str, positions: dict[str, int], name: str) -> str:
    """Return a section without its H2 heading for substantive-content checks."""

    section = section_body(reader_text, positions, name)
    return re.sub(rf"(?m)^##\s+{re.escape(name)}\s*$", "", section, count=1).strip()


def has_substantive_section_content(content: str, minimum_chars: int = 4) -> bool:
    """Reject sections made only from Markdown furniture or punctuation."""

    content = html.unescape(RAW_HTML_TAG_RE.sub("", content))
    lexical = sum(
        1 for character in content if unicodedata.category(character)[0] in {"L", "N"}
    )
    return lexical >= minimum_chars


def section_items(reader_text: str, positions: dict[str, int], name: str) -> list[str]:
    """Return exact, stripped H3 blocks from one reader-facing section."""

    section = section_body(reader_text, positions, name)
    matches = list(THIRD_LEVEL_HEADING_RE.finditer(section))
    return [
        section[match.start() : matches[index + 1].start() if index + 1 < len(matches) else len(section)].strip()
        for index, match in enumerate(matches)
    ]


def reader_item_sha256(item: str) -> str:
    """Create the stable hash used to bind a ledger item to displayed prose."""

    normalized = item.strip().replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def item_heading_content(item: str) -> str:
    """Return an H3 item title without Markdown markers or its optional number."""

    first_line = item.splitlines()[0] if item.splitlines() else ""
    return re.sub(r"^###\s+(?:\d+[.、]\s*)?", "", first_line).strip()


def priority_label_values(item: str, label: str) -> list[str]:
    """Extract values only from the canonical bullet label, not loose mentions."""

    pattern = PRIORITY_LABEL_PATTERNS[label]
    return [match.group(1).strip() for match in pattern.finditer(item)]


def viewpoint_label_values(item: str, label: str) -> list[str]:
    """Extract values from one canonical reader-facing viewpoint label."""

    pattern = VIEWPOINT_LABEL_PATTERNS[label]
    return [match.group(1).strip() for match in pattern.finditer(item)]


def standalone_marker_present(section: str, marker: str) -> bool:
    """Accept a fixed zero-item sentence only as its own unqualified line."""

    return bool(
        re.search(
            rf"(?m)^\s*{re.escape(marker)}[。.!！]\s*$",
            section,
        )
    )


def leading_publication_status(conclusion_section: str) -> str | None:
    """Return the canonical status only when it is the complete first sentence."""

    body = re.sub(r"(?m)^##\s+结论\s*$", "", conclusion_section, count=1).lstrip()
    for status in PUBLICATION_STATUSES:
        if re.match(rf"^{re.escape(status)}[。.!！]", body):
            return status
    return None


def validate_report(text: str) -> dict[str, object]:
    reader_text = split_reader_layer(text)
    positions = section_positions(reader_text)
    errors: list[str] = []

    heading_titles = [match.group(1).strip() for match in SECOND_LEVEL_HEADING_RE.finditer(reader_text)]
    if any(heading_titles.count(section) > 1 for section in REQUIRED_SECTIONS):
        errors.append("DUPLICATE_REQUIRED_SECTION")
    if HIDDEN_OR_FENCED_RE.search(reader_text):
        errors.append("HIDDEN_OR_CODE_FENCED_READER_CONTENT")
    if RAW_HTML_TAG_RE.search(reader_text):
        errors.append("RAW_HTML_IN_READER_CONTENT")

    missing = [section for section in REQUIRED_SECTIONS if section not in positions]
    if missing:
        errors.append("MISSING_REQUIRED_SECTION")
    elif [positions[name] for name in REQUIRED_SECTIONS] != sorted(positions.values()):
        errors.append("SECTION_ORDER_INVALID")
    for section in REQUIRED_SECTIONS:
        if section in positions and not has_substantive_section_content(
            section_content(reader_text, positions, section)
        ):
            errors.append("REQUIRED_SECTION_BODY_EMPTY")
        if section in positions and section not in {
            "发布前需要处理",
            "可继续研究的新观点",
        } and section_item_count(reader_text, positions, section):
            errors.append("UNEXPECTED_H3_OUTSIDE_ITEM_SECTIONS")

    conclusion = section_body(reader_text, positions, "结论")
    status_count = sum(conclusion.count(status) for status in PUBLICATION_STATUSES)
    if status_count == 0:
        errors.append("MISSING_CHINESE_PUBLICATION_STATUS")
    elif status_count > 1:
        errors.append("MULTIPLE_PUBLICATION_STATUSES")
    if status_count and leading_publication_status(conclusion) is None:
        errors.append("PUBLICATION_STATUS_NOT_LEADING")
    if leading_publication_status(conclusion) == "可以发布":
        body_after_status = re.sub(r"^##\s+结论\s*", "", conclusion.lstrip(), count=1)
        body_after_status = re.sub(r"^可以发布[。.!！]", "", body_after_status, count=1)
        if READY_CONTRADICTION_RE.search(body_after_status):
            errors.append("READY_CONCLUSION_CONTRADICTED")

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
    issue_section = section_body(reader_text, positions, "发布前需要处理")
    zero_issue_marker_present = standalone_marker_present(
        issue_section, ZERO_ISSUE_MARKER
    )
    if item_count == 0 and not zero_issue_marker_present:
        errors.append("PRIORITY_ISSUE_ZERO_MARKER_MISSING")
    if item_count and zero_issue_marker_present:
        errors.append("PRIORITY_ISSUE_ZERO_MARKER_CONFLICT")
    for item in section_items(reader_text, positions, "发布前需要处理"):
        if not has_substantive_section_content(
            item_heading_content(item), minimum_chars=2
        ):
            errors.append("PRIORITY_ITEM_HEADING_MISSING")
        for label, error_code in (
            ("原文位置", "PRIORITY_ITEM_LOCATION_MISSING"),
            ("为什么重要", "PRIORITY_ITEM_REASON_MISSING"),
            ("建议处理", "PRIORITY_ITEM_ACTION_MISSING"),
        ):
            values = priority_label_values(item, label)
            if len(values) != 1 or not has_substantive_section_content(values[0]):
                errors.append(error_code)

    viewpoint_count = section_item_count(reader_text, positions, "可继续研究的新观点")
    viewpoint_section = section_body(reader_text, positions, "可继续研究的新观点")
    zero_viewpoint_marker = "本稿暂不足以形成可靠的新观点"
    zero_viewpoint_marker_present = standalone_marker_present(
        viewpoint_section, zero_viewpoint_marker
    )
    if viewpoint_count > 2:
        errors.append("TOO_MANY_READER_VIEWPOINTS")
    if viewpoint_count == 0 and not zero_viewpoint_marker_present:
        errors.append("NEW_VIEWPOINT_ZERO_MARKER_MISSING")
    if viewpoint_count and zero_viewpoint_marker_present:
        errors.append("NEW_VIEWPOINT_ZERO_MARKER_CONFLICT")
    for item in section_items(reader_text, positions, "可继续研究的新观点"):
        if not has_substantive_section_content(
            item_heading_content(item), minimum_chars=2
        ):
            errors.append("NEW_VIEWPOINT_HEADING_MISSING")
        for label, error_code in (
            ("适用对象", "NEW_VIEWPOINT_ACTOR_LABEL_MISSING"),
            ("决策问题", "NEW_VIEWPOINT_DECISION_LABEL_MISSING"),
            ("可能机制", "NEW_VIEWPOINT_MECHANISM_LABEL_MISSING"),
            ("适用边界", "NEW_VIEWPOINT_BOUNDARY_LABEL_MISSING"),
            ("反证条件", "NEW_VIEWPOINT_FALSIFIER_LABEL_MISSING"),
        ):
            values = viewpoint_label_values(item, label)
            if len(values) != 1 or not has_substantive_section_content(values[0]):
                errors.append(error_code)

    unique_errors = sorted(set(errors))
    return {
        "ok": not unique_errors,
        "error_count": len(unique_errors),
        "error_codes": unique_errors,
        "required_section_count": len(positions),
        "priority_item_count": item_count,
        "new_viewpoint_count": viewpoint_count,
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
