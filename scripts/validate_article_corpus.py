#!/usr/bin/env python3
"""Validate a raw/derived article corpus without emitting article content.

Accepted input is one UTF-8 JSONL file or a directory recursively containing
JSONL files. Exit codes: 0 valid (warnings may exist), 1 validation errors, and
2 input/configuration errors.

Raw records preserve source material. Derived records point to a raw record and
carry only transformed content plus reproducibility metadata. ``article_id`` is
deterministic from the normalized platform plus the case-preserved source
account ID and native article ID; it must not depend on a mutable title, URL,
or body. ``--dry-run`` inspects directory structure without opening JSONL files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse


SCHEMA_VERSION = "1.0"
ARTICLE_ID_RE = re.compile(r"^art_[0-9a-f]{24}$")
RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
RAW_REQUIRED = {
    "schema_version",
    "layer",
    "record_id",
    "article_id",
    "source_account",
    "source_article_id",
    "title",
    "body",
    "published_at",
    "original_url",
    "captured_at",
}
DERIVED_REQUIRED = {
    "schema_version",
    "layer",
    "record_id",
    "article_id",
    "source_record_id",
    "derivation",
    "content",
}
RAW_FORBIDDEN = {"source_record_id", "derivation", "content"}
DERIVED_FORBIDDEN = {
    "source_account",
    "source_article_id",
    "title",
    "body",
    "published_at",
    "original_url",
    "captured_at",
}
LAYERED_CORPUS_DIRS = (
    "l0_raw",
    "l1_index",
    "l2_article_maps",
    "l3_cards",
    "l4_clusters",
    "l5_routes",
    "l6_prior_packs",
)
LAYERED_CONTROL_DIRS = {"access", "audit", "schemas", "snapshots"}


@dataclass(frozen=True)
class LocatedRecord:
    file: str
    line: int
    value: dict[str, Any]


@dataclass(frozen=True)
class Issue:
    code: str
    severity: str
    file: str
    line: int
    record_id: str
    article_id: str | None = None
    related_records: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "code": self.code,
            "severity": self.severity,
            "location": {"file": self.file, "line": self.line},
            "record_id": self.record_id,
        }
        if self.article_id:
            result["article_id"] = self.article_id
        if self.related_records:
            result["related_records"] = list(self.related_records)
        return result


def normalize_scalar(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def normalize_identifier(value: str) -> str:
    """Normalize identifier width and surrounding space without changing case."""
    return unicodedata.normalize("NFKC", value).strip()


def normalize_title(value: str) -> str:
    return " ".join(normalize_scalar(value).split())


def normalize_body(value: str) -> str:
    return value.replace("\r\n", "\n").replace("\r", "\n").strip()


def body_sha256(value: str) -> str:
    return hashlib.sha256(normalize_body(value).encode("utf-8")).hexdigest()


def stable_article_id(platform: str, account_id: str, source_article_id: str) -> str:
    identity = "\x1f".join(
        (
            normalize_scalar(platform),
            normalize_identifier(account_id),
            normalize_identifier(source_article_id),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"art_{digest}"


def parse_iso_temporal(value: Any) -> tuple[datetime, bool]:
    """Return an aware datetime and whether a datetime omitted its timezone."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("not a non-empty string")
    raw = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return datetime.combine(date.fromisoformat(raw), time.min, timezone.utc), False
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    missing_timezone = parsed.tzinfo is None
    if missing_timezone:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc), missing_timezone


def _safe_record_id(value: Any, line: int) -> str:
    if isinstance(value, dict) and isinstance(value.get("record_id"), str):
        return value["record_id"]
    return f"LINE-{line}"


def _safe_article_id(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("article_id"), str):
        return value["article_id"]
    return None


def discover_jsonl(input_path: Path) -> tuple[list[Path], str]:
    if input_path.is_file():
        if input_path.suffix.casefold() != ".jsonl":
            raise ValueError("Input file must use the .jsonl extension.")
        return [input_path], "file"
    if input_path.is_dir():
        files = sorted(path for path in input_path.rglob("*.jsonl") if path.is_file())
        if not files:
            raise ValueError("Input directory contains no JSONL files.")
        return files, "directory"
    raise ValueError("Input path does not exist or is not a regular file/directory.")


def structure_dry_run(input_path: Path, *, layout: str = "auto") -> dict[str, Any]:
    """Inspect only directory/file names; never open a corpus file."""
    if not input_path.is_dir():
        raise ValueError("--dry-run requires a directory input.")
    if layout not in {"auto", "raw-derived", "layered"}:
        raise ValueError("Unknown dry-run layout.")

    top_level_dirs = {
        path.name.casefold()
        for path in input_path.iterdir()
        if path.is_dir() and not path.is_symlink()
    }
    layered_present = top_level_dirs.intersection(LAYERED_CORPUS_DIRS)
    raw_derived_present = top_level_dirs.intersection({"raw", "derived"})
    if layout == "auto":
        if layered_present:
            detected_layout = "layered_l0_l6"
        elif raw_derived_present:
            detected_layout = "raw_derived"
        else:
            detected_layout = "unknown"
    else:
        detected_layout = "layered_l0_l6" if layout == "layered" else "raw_derived"

    raw_count = 0
    derived_count = 0
    control_count = 0
    unclassified_count = 0
    ambiguous_count = 0
    symlink_count = 0
    file_count = 0
    for path in sorted(input_path.rglob("*.jsonl")):
        if path.is_symlink():
            symlink_count += 1
            continue
        if not path.is_file():
            continue
        file_count += 1
        relative_parts = tuple(part.casefold() for part in path.relative_to(input_path).parts[:-1])
        part_set = set(relative_parts)
        if detected_layout == "layered_l0_l6":
            is_raw = "l0_raw" in part_set
            is_derived = bool(part_set.intersection(LAYERED_CORPUS_DIRS[1:]))
            is_control = bool(part_set.intersection(LAYERED_CONTROL_DIRS))
        else:
            is_raw = "raw" in part_set
            is_derived = "derived" in part_set
            is_control = False
        if is_raw and is_derived:
            ambiguous_count += 1
        elif is_raw:
            raw_count += 1
        elif is_derived:
            derived_count += 1
        elif is_control:
            control_count += 1
        else:
            unclassified_count += 1

    issues: list[dict[str, Any]] = []
    if detected_layout == "unknown":
        issues.append({"code": "LAYOUT_NOT_RECOGNIZED", "severity": "error", "count": 1})
    elif detected_layout == "layered_l0_l6":
        missing = set(LAYERED_CORPUS_DIRS).difference(top_level_dirs)
        if missing:
            issues.append({"code": "LAYERED_DIRECTORIES_MISSING", "severity": "error", "count": len(missing)})
    else:
        missing = {"raw", "derived"}.difference(top_level_dirs)
        if missing:
            issues.append({"code": "RAW_DERIVED_DIRECTORIES_MISSING", "severity": "error", "count": len(missing)})
    if not file_count:
        issues.append({"code": "EMPTY_CORPUS_SKELETON", "severity": "warning", "count": 1})
    if ambiguous_count:
        issues.append({"code": "AMBIGUOUS_LAYER_PATH", "severity": "error", "count": ambiguous_count})
    if derived_count and not raw_count:
        issues.append({"code": "DERIVED_WITHOUT_RAW_DIRECTORY", "severity": "error", "count": derived_count})
    if unclassified_count:
        issues.append({"code": "UNCLASSIFIED_LAYER_PATH", "severity": "warning", "count": unclassified_count})
    if symlink_count:
        issues.append({"code": "SYMLINK_JSONL_SKIPPED", "severity": "warning", "count": symlink_count})

    error_count = sum(issue["severity"] == "error" for issue in issues)
    warning_count = sum(issue["severity"] == "warning" for issue in issues)
    status = "invalid" if error_count else ("valid_with_warnings" if warning_count else "valid")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "validate_article_corpus",
        "mode": "structure_only_dry_run",
        "status": status,
        "summary": {
            "jsonl_file_count": file_count,
            "raw_file_count": raw_count,
            "derived_file_count": derived_count,
            "control_file_count": control_count,
            "unclassified_file_count": unclassified_count,
            "ambiguous_file_count": ambiguous_count,
            "symlink_file_count": symlink_count,
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "checks": {
            "content_files_opened": 0,
            "content_fields_read": False,
            "file_names_emitted": False,
            "detected_layout": detected_layout,
            "layer_path_segments": (
                list(LAYERED_CORPUS_DIRS)
                if detected_layout == "layered_l0_l6"
                else ["raw", "derived"]
            ),
        },
        "issues": issues,
        "limitations": [
            "Dry-run validates directory separation only.",
            "It does not validate records, required fields, body hashes, dates, source accounts, or article IDs.",
        ],
        "privacy": "no_jsonl_file_opened_and_no_file_name_or_content_emitted",
    }


def _display_name(path: Path, input_path: Path, input_kind: str) -> str:
    if input_kind == "file":
        return path.name
    return path.relative_to(input_path).as_posix()


def load_records(
    files: Iterable[Path], input_path: Path, input_kind: str
) -> tuple[list[LocatedRecord], list[Issue]]:
    records: list[LocatedRecord] = []
    issues: list[Issue] = []
    for path in files:
        display = _display_name(path, input_path, input_kind)
        try:
            lines = path.read_text(encoding="utf-8-sig").splitlines()
        except (OSError, UnicodeError):
            issues.append(Issue("FILE_UNREADABLE_OR_NOT_UTF8", "error", display, 0, "FILE"))
            continue
        for line_number, raw_line in enumerate(lines, start=1):
            if not raw_line.strip():
                continue
            try:
                value = json.loads(raw_line)
            except json.JSONDecodeError:
                issues.append(Issue("INVALID_JSON", "error", display, line_number, f"LINE-{line_number}"))
                continue
            if not isinstance(value, dict):
                issues.append(Issue("RECORD_NOT_OBJECT", "error", display, line_number, f"LINE-{line_number}"))
                continue
            records.append(LocatedRecord(display, line_number, value))
    return records, issues


class CorpusValidator:
    def __init__(self, *, as_of: date, min_date: date) -> None:
        self.as_of = as_of
        self.min_date = min_date
        self.issues: list[Issue] = []
        self.raw_body_hashes: dict[str, str] = {}
        self.raw_by_record: dict[str, LocatedRecord] = {}
        self.duplicate_title_groups: list[list[str]] = []
        self.duplicate_body_groups: list[list[str]] = []

    def add(
        self,
        located: LocatedRecord,
        code: str,
        severity: str = "error",
        *,
        related: Iterable[str] = (),
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity=severity,
                file=located.file,
                line=located.line,
                record_id=_safe_record_id(located.value, located.line),
                article_id=_safe_article_id(located.value),
                related_records=tuple(sorted(set(related))),
            )
        )

    def validate(self, records: list[LocatedRecord]) -> None:
        for located in records:
            self._validate_record(located)
        self._validate_global_ids(records)
        self._validate_duplicates(records)
        self._validate_source_accounts(records)
        self._validate_derived_links(records)

    def _validate_record(self, located: LocatedRecord) -> None:
        record = located.value
        if record.get("schema_version") != SCHEMA_VERSION:
            self.add(located, "SCHEMA_VERSION")
        layer = record.get("layer")
        if layer not in {"raw", "derived"}:
            self.add(located, "LAYER_INVALID")
            return
        required = RAW_REQUIRED if layer == "raw" else DERIVED_REQUIRED
        for key in sorted(required):
            if key not in record:
                self.add(located, f"MISSING_{key.upper()}")
        forbidden = RAW_FORBIDDEN if layer == "raw" else DERIVED_FORBIDDEN
        for key in sorted(forbidden.intersection(record)):
            self.add(located, f"LAYER_BOUNDARY_{layer.upper()}_HAS_{key.upper()}")

        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not RECORD_ID_RE.fullmatch(record_id):
            self.add(located, "RECORD_ID_INVALID")
        article_id = record.get("article_id")
        if not isinstance(article_id, str) or not ARTICLE_ID_RE.fullmatch(article_id):
            self.add(located, "ARTICLE_ID_FORMAT")

        path_parts = {part.casefold() for part in Path(located.file).parts[:-1]}
        if "raw" in path_parts and layer != "raw":
            self.add(located, "LAYER_PATH_MISMATCH")
        if "derived" in path_parts and layer != "derived":
            self.add(located, "LAYER_PATH_MISMATCH")

        if layer == "raw":
            self._validate_raw(located)
        else:
            self._validate_derived(located)

    def _validate_raw(self, located: LocatedRecord) -> None:
        record = located.value
        for key in ("source_article_id", "title", "body"):
            if not isinstance(record.get(key), str) or not record[key].strip():
                self.add(located, f"{key.upper()}_EMPTY")

        account = record.get("source_account")
        account_valid = isinstance(account, dict)
        if not account_valid:
            self.add(located, "SOURCE_ACCOUNT_INVALID")
        else:
            for key in ("platform", "account_id", "name"):
                if not isinstance(account.get(key), str) or not account[key].strip():
                    self.add(located, f"SOURCE_ACCOUNT_{key.upper()}_EMPTY")
                    account_valid = False

        url = record.get("original_url")
        if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"} or not urlparse(url).netloc:
            self.add(located, "ORIGINAL_URL_INVALID")

        published = self._check_date(located, "published_at", record.get("published_at"))
        captured = self._check_date(located, "captured_at", record.get("captured_at"))
        if published and captured and captured < published:
            self.add(located, "CAPTURED_BEFORE_PUBLISHED")

        native_id = record.get("source_article_id")
        if account_valid and isinstance(native_id, str) and native_id.strip():
            expected = stable_article_id(account["platform"], account["account_id"], native_id)
            if record.get("article_id") != expected:
                self.add(located, "ARTICLE_ID_NOT_STABLE")

        record_id = record.get("record_id")
        body = record.get("body")
        if isinstance(record_id, str) and isinstance(body, str) and body.strip():
            self.raw_body_hashes[record_id] = body_sha256(body)
            declared = record.get("body_sha256")
            if declared is not None and declared != self.raw_body_hashes[record_id]:
                self.add(located, "BODY_SHA256_MISMATCH")

    def _validate_derived(self, located: LocatedRecord) -> None:
        record = located.value
        if not isinstance(record.get("source_record_id"), str) or not record["source_record_id"].strip():
            self.add(located, "SOURCE_RECORD_ID_EMPTY")
        content = record.get("content")
        if not isinstance(content, dict) or not content or not any(
            isinstance(value, str) and value.strip() for value in content.values()
        ):
            self.add(located, "DERIVED_CONTENT_EMPTY")

        derivation = record.get("derivation")
        if not isinstance(derivation, dict):
            self.add(located, "DERIVATION_INVALID")
            return
        for key in ("type", "method", "method_version", "created_at", "input_body_sha256"):
            if not isinstance(derivation.get(key), str) or not derivation[key].strip():
                self.add(located, f"DERIVATION_{key.upper()}_EMPTY")
        self._check_date(located, "derivation.created_at", derivation.get("created_at"))
        digest = derivation.get("input_body_sha256")
        if isinstance(digest, str) and not re.fullmatch(r"[0-9a-f]{64}", digest):
            self.add(located, "DERIVATION_INPUT_HASH_INVALID")

    def _check_date(self, located: LocatedRecord, field: str, value: Any) -> datetime | None:
        code_name = field.upper().replace(".", "_")
        try:
            parsed, missing_timezone = parse_iso_temporal(value)
        except (TypeError, ValueError):
            self.add(located, f"{code_name}_INVALID")
            return None
        if missing_timezone:
            self.add(located, f"{code_name}_TIMEZONE_MISSING", "warning")
        if parsed.date() < self.min_date:
            self.add(located, f"{code_name}_TOO_EARLY")
        if parsed.date() > self.as_of + timedelta(days=1):
            self.add(located, f"{code_name}_IN_FUTURE")
        return parsed

    def _validate_global_ids(self, records: list[LocatedRecord]) -> None:
        by_record_id: dict[str, list[LocatedRecord]] = defaultdict(list)
        raw_by_identity: dict[tuple[str, str, str], list[LocatedRecord]] = defaultdict(list)
        article_identities: dict[str, set[tuple[str, str, str]]] = defaultdict(set)

        for located in records:
            record = located.value
            record_id = record.get("record_id")
            if isinstance(record_id, str):
                by_record_id[record_id].append(located)
            if record.get("layer") != "raw":
                continue
            if isinstance(record_id, str) and record_id not in self.raw_by_record:
                self.raw_by_record[record_id] = located
            account = record.get("source_account")
            native_id = record.get("source_article_id")
            article_id = record.get("article_id")
            if isinstance(account, dict) and all(isinstance(account.get(k), str) for k in ("platform", "account_id")) and isinstance(native_id, str):
                identity = (
                    normalize_scalar(account["platform"]),
                    normalize_identifier(account["account_id"]),
                    normalize_identifier(native_id),
                )
                raw_by_identity[identity].append(located)
                if isinstance(article_id, str):
                    article_identities[article_id].add(identity)

        for locations in by_record_id.values():
            if len(locations) > 1:
                ids = [_safe_record_id(item.value, item.line) for item in locations]
                for item in locations:
                    self.add(item, "DUPLICATE_RECORD_ID", related=ids)
        for locations in raw_by_identity.values():
            if len(locations) > 1:
                ids = [_safe_record_id(item.value, item.line) for item in locations]
                for item in locations:
                    self.add(item, "DUPLICATE_SOURCE_IDENTITY", related=ids)
        for article_id, identities in article_identities.items():
            if len(identities) > 1:
                for located in records:
                    if located.value.get("layer") == "raw" and located.value.get("article_id") == article_id:
                        self.add(located, "ARTICLE_ID_COLLISION")

    def _validate_duplicates(self, records: list[LocatedRecord]) -> None:
        titles: dict[str, list[LocatedRecord]] = defaultdict(list)
        bodies: dict[str, list[LocatedRecord]] = defaultdict(list)
        for located in records:
            record = located.value
            if record.get("layer") != "raw":
                continue
            title = record.get("title")
            body = record.get("body")
            if isinstance(title, str) and title.strip():
                titles[normalize_title(title)].append(located)
            if isinstance(body, str) and body.strip():
                bodies[body_sha256(body)].append(located)

        for mapping, code, severity, output in (
            (titles, "DUPLICATE_TITLE", "warning", self.duplicate_title_groups),
            (bodies, "DUPLICATE_BODY_SHA256", "error", self.duplicate_body_groups),
        ):
            for locations in mapping.values():
                if len(locations) < 2:
                    continue
                ids = sorted(_safe_record_id(item.value, item.line) for item in locations)
                output.append(ids)
                for item in locations:
                    self.add(item, code, severity, related=ids)

    def _validate_source_accounts(self, records: list[LocatedRecord]) -> None:
        names: dict[tuple[str, str], dict[str, list[LocatedRecord]]] = defaultdict(lambda: defaultdict(list))
        for located in records:
            record = located.value
            if record.get("layer") != "raw" or not isinstance(record.get("source_account"), dict):
                continue
            account = record["source_account"]
            if not all(isinstance(account.get(key), str) and account[key].strip() for key in ("platform", "account_id", "name")):
                continue
            key = (normalize_scalar(account["platform"]), normalize_identifier(account["account_id"]))
            names[key][normalize_scalar(account["name"])].append(located)
        for variants in names.values():
            if len(variants) > 1:
                locations = [item for group in variants.values() for item in group]
                ids = [_safe_record_id(item.value, item.line) for item in locations]
                for item in locations:
                    self.add(item, "SOURCE_ACCOUNT_NAME_CONFLICT", "warning", related=ids)

    def _validate_derived_links(self, records: list[LocatedRecord]) -> None:
        for located in records:
            record = located.value
            if record.get("layer") != "derived":
                continue
            source_record_id = record.get("source_record_id")
            if not isinstance(source_record_id, str):
                continue
            parent = self.raw_by_record.get(source_record_id)
            if parent is None:
                self.add(located, "DERIVED_SOURCE_NOT_FOUND")
                continue
            if record.get("article_id") != parent.value.get("article_id"):
                self.add(located, "DERIVED_ARTICLE_ID_MISMATCH", related=(source_record_id,))
            derivation = record.get("derivation")
            if isinstance(derivation, dict):
                expected_hash = self.raw_body_hashes.get(source_record_id)
                if expected_hash and derivation.get("input_body_sha256") != expected_hash:
                    self.add(located, "DERIVED_INPUT_HASH_MISMATCH", related=(source_record_id,))
                created = self._parse_without_issue(derivation.get("created_at"))
                captured = self._parse_without_issue(parent.value.get("captured_at"))
                if created and captured and created < captured:
                    self.add(located, "DERIVED_BEFORE_CAPTURE", related=(source_record_id,))

    @staticmethod
    def _parse_without_issue(value: Any) -> datetime | None:
        try:
            return parse_iso_temporal(value)[0]
        except (TypeError, ValueError):
            return None


def validate_corpus(input_path: Path, *, as_of: date, min_date: date) -> dict[str, Any]:
    files, input_kind = discover_jsonl(input_path)
    records, load_issues = load_records(files, input_path, input_kind)
    validator = CorpusValidator(as_of=as_of, min_date=min_date)
    validator.issues.extend(load_issues)
    validator.validate(records)
    issues = sorted(
        set(validator.issues),
        key=lambda issue: (issue.file, issue.line, issue.code, issue.record_id),
    )
    error_count = sum(issue.severity == "error" for issue in issues)
    warning_count = sum(issue.severity == "warning" for issue in issues)
    raw_count = sum(item.value.get("layer") == "raw" for item in records)
    derived_count = sum(item.value.get("layer") == "derived" for item in records)
    status = "invalid" if error_count else ("valid_with_warnings" if warning_count else "valid")
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": "validate_article_corpus",
        "status": status,
        "input": {"kind": input_kind, "file_count": len(files)},
        "summary": {
            "record_count": len(records),
            "raw_count": raw_count,
            "derived_count": derived_count,
            "error_count": error_count,
            "warning_count": warning_count,
            "duplicate_title_group_count": len(validator.duplicate_title_groups),
            "duplicate_body_group_count": len(validator.duplicate_body_groups),
        },
        "checks": {
            "required_fields": True,
            "empty_body": True,
            "duplicate_title": {"normalization": "NFKC_casefold_whitespace", "severity": "warning"},
            "duplicate_body": {"algorithm": "sha256_normalized_newlines_trimmed", "severity": "error"},
            "dates": {"minimum": min_date.isoformat(), "as_of": as_of.isoformat(), "future_tolerance_days": 1},
            "source_account": {"required": ["platform", "account_id", "name"]},
            "article_id": {
                "algorithm": "art_ + first_24_hex(sha256(casefolded platform, case-preserved account_id, case-preserved source_article_id))",
                "mutable_fields_excluded": ["title", "body", "original_url"],
            },
            "layer_boundary": {"raw_and_derived_separated": True, "derived_parent_hash_checked": True},
        },
        "duplicate_groups": {
            "titles": [
                {"group_id": f"TITLE-DUP-{index:03d}", "record_ids": group}
                for index, group in enumerate(sorted(validator.duplicate_title_groups), start=1)
            ],
            "bodies": [
                {"group_id": f"BODY-DUP-{index:03d}", "record_ids": group}
                for index, group in enumerate(sorted(validator.duplicate_body_groups), start=1)
            ],
        },
        "issues": [issue.as_dict() for issue in issues],
        "privacy": "article_titles_bodies_urls_hashes_and_absolute_paths_omitted",
    }


def parse_date_arg(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected YYYY-MM-DD.") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="A JSONL file or directory containing JSONL files.")
    parser.add_argument("--as-of", type=parse_date_arg, default=date.today())
    parser.add_argument("--min-date", type=parse_date_arg, default=date(2000, 1, 1))
    parser.add_argument("--output", type=Path, help="Optional path for the JSON report.")
    parser.add_argument("--pretty", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect directory layer structure without opening JSONL files; cannot be combined with --output.",
    )
    parser.add_argument(
        "--layout",
        choices=("auto", "raw-derived", "layered"),
        default="auto",
        help="Directory layout used by --dry-run; auto recognizes raw/derived and l0-l6 structures.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.dry_run and args.output:
        print(json.dumps({"status": "error", "error": "--dry-run cannot be combined with --output"}, sort_keys=True))
        return 2
    if args.dry_run:
        try:
            payload = structure_dry_run(args.input, layout=args.layout)
            exit_code = 1 if payload["status"] == "invalid" else 0
        except OSError:
            payload = {"schema_version": SCHEMA_VERSION, "tool": "validate_article_corpus", "status": "error", "error": "Corpus directory could not be inspected."}
            exit_code = 2
        except ValueError as exc:
            payload = {"schema_version": SCHEMA_VERSION, "tool": "validate_article_corpus", "status": "error", "error": str(exc)}
            exit_code = 2
    elif args.min_date > args.as_of:
        payload = {"schema_version": SCHEMA_VERSION, "tool": "validate_article_corpus", "status": "error", "error": "min-date must not be after as-of"}
        exit_code = 2
    else:
        try:
            payload = validate_corpus(args.input, as_of=args.as_of, min_date=args.min_date)
            exit_code = 1 if payload["status"] == "invalid" else 0
        except OSError:
            payload = {"schema_version": SCHEMA_VERSION, "tool": "validate_article_corpus", "status": "error", "error": "Corpus input could not be inspected."}
            exit_code = 2
        except ValueError as exc:
            payload = {"schema_version": SCHEMA_VERSION, "tool": "validate_article_corpus", "status": "error", "error": str(exc)}
            exit_code = 2
    rendered = json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True) + "\n"
    if args.output:
        try:
            args.output.write_text(rendered, encoding="utf-8")
        except OSError:
            print(json.dumps({"status": "error", "error": "Report output could not be written."}, sort_keys=True))
            return 2
    else:
        print(rendered, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
