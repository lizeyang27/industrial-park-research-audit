#!/usr/bin/env python3
"""Fail-closed preflight scan for a clean-room public repository.

The scanner uses only the Python standard library. It reports finding types and
locations without echoing matched sensitive values.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit
from xml.etree import ElementTree as ET


SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "__pycache__",
    "node_modules",
}

OFFICE_SUFFIXES = {
    ".doc",
    ".docx",
    ".docm",
    ".dot",
    ".dotx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".xlt",
    ".xltx",
    ".ppt",
    ".pptx",
    ".pptm",
    ".pot",
    ".potx",
}
OOXML_SUFFIXES = {".docx", ".docm", ".dotx", ".xlsx", ".xlsm", ".xltx", ".pptx", ".pptm", ".potx"}
PDF_SUFFIXES = {".pdf"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"}
RAW_BINARY_SUFFIXES = OFFICE_SUFFIXES | PDF_SUFFIXES | IMAGE_SUFFIXES

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".gitignore",
    ".htm",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".jsonl",
    ".md",
    ".ps1",
    ".py",
    ".rst",
    ".sh",
    ".toml",
    ".ts",
    ".tsv",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

MAX_TEXT_BYTES = 10 * 1024 * 1024
MAX_OOXML_ENTRIES = 20_000
MAX_OOXML_UNCOMPRESSED_BYTES = 100 * 1024 * 1024

WINDOWS_USER_PATH_RE = re.compile(
    r"(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/]" + "Users" + r"[\\/][^\\/\s<>:\"|?*]+"
)
UNC_USER_PATH_RE = re.compile(
    r"(?i)\\\\[^\\/\s]+[\\/][^\\/\s]+[\\/](?:" + "Users" + "|" + "Documents and Settings" + r")[\\/]"
)
MESSAGE_EXPORT_MARKERS = ("xwe" + "chat", "wx" + "id_")
EMAIL_RE = re.compile(r"(?i)(?<![A-Z0-9._%+\-])[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}(?![A-Z0-9.\-])")
MOBILE_RE = re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)")
URL_RE = re.compile(r"https?://[^\s<>\]\[(){}\"']+", re.IGNORECASE)

SAFE_EXAMPLE_DOMAINS = {"example.com", "example.org", "example.net", "localhost", "127.0.0.1"}
PRIVATE_HOST_PATHS = (
    (("dou" + "bao.com"), "/thread/"),
    (("chat" + "gpt.com"), "/share/"),
    (("chat." + "openai.com"), "/share/"),
    (("cla" + "ude.ai"), "/share/"),
    (("sl" + "ack.com"), "/archives/"),
    (("teams." + "microsoft.com"), "/l/message/"),
    (("drive." + "google.com"), "/"),
    (("docs." + "google.com"), "/"),
    (("onedrive." + "live.com"), "/"),
)
SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "authorization",
    "key",
    "secret",
    "sig",
    "signature",
    "token",
}

CORE_METADATA_TAGS = {"creator", "lastModifiedBy", "title", "subject", "keywords", "description", "category"}
COMMENT_ENTRY_MARKERS = (
    "word/comments",
    "word/people.xml",
    "xl/comments",
    "xl/threadedcomments",
    "xl/persons/",
    "ppt/comments/",
    "ppt/commentauthors.xml",
)


@dataclass(frozen=True)
class Finding:
    code: str
    path: str
    line: int | None = None
    detail: str = ""

    def render(self) -> str:
        location = self.path if self.line is None else f"{self.path}:{self.line}"
        suffix = f" ({self.detail})" if self.detail else ""
        return f"[{self.code}] {location}{suffix}"


@dataclass(frozen=True)
class DenyRule:
    source_line: int
    literal: str | None = None
    regex: re.Pattern[str] | None = None

    def matches(self, value: str) -> bool:
        if self.literal is not None:
            return self.literal.casefold() in value.casefold()
        assert self.regex is not None
        return self.regex.search(value) is not None


class ScanConfigurationError(RuntimeError):
    pass


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scan a candidate public repository and fail on release blockers.")
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan (default: current directory).")
    parser.add_argument(
        "--denylist",
        type=Path,
        help="Private file outside the repository: one literal per line, or re:<regular-expression>.",
    )
    return parser.parse_args(argv)


def load_denylist(path: Path | None, root: Path) -> list[DenyRule]:
    if path is None:
        return []
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        pass
    else:
        raise ScanConfigurationError("The denylist must be stored outside the repository being scanned.")
    if not resolved.is_file():
        raise ScanConfigurationError("The denylist path is not a readable file.")

    rules: list[DenyRule] = []
    for line_number, raw_line in enumerate(resolved.read_text(encoding="utf-8-sig").splitlines(), start=1):
        value = raw_line.strip()
        if not value or value.startswith("#"):
            continue
        if value.startswith("re:"):
            expression = value[3:].strip()
            if not expression:
                raise ScanConfigurationError(f"Empty regular expression in denylist line {line_number}.")
            try:
                rules.append(DenyRule(source_line=line_number, regex=re.compile(expression, re.IGNORECASE)))
            except re.error as exc:
                raise ScanConfigurationError(f"Invalid regular expression in denylist line {line_number}: {exc}") from exc
        else:
            rules.append(DenyRule(source_line=line_number, literal=value))
    return rules


def iter_files(root: Path):
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        directories[:] = sorted(name for name in directories if name.casefold() not in SKIP_DIRS)
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if path.is_symlink():
                yield path, True
            elif path.is_file():
                yield path, False


def relative_display(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_safe_example_email(value: str) -> bool:
    domain = value.rsplit("@", 1)[-1].casefold().rstrip(".")
    return domain in SAFE_EXAMPLE_DOMAINS or domain.endswith(".invalid")


def suspicious_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return True
    host = (parsed.hostname or "").casefold().rstrip(".")
    if not host:
        return True
    if host in SAFE_EXAMPLE_DOMAINS or host.endswith(".invalid"):
        return False
    path = parsed.path.casefold()
    for private_host, private_prefix in PRIVATE_HOST_PATHS:
        if (host == private_host or host.endswith("." + private_host)) and path.startswith(private_prefix):
            return True
    query_keys = {key.casefold() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}
    return bool(query_keys & SENSITIVE_QUERY_KEYS)


def text_findings(text: str, display: str, deny_rules: list[DenyRule]) -> list[Finding]:
    findings: list[Finding] = []
    patterns = (
        ("WINDOWS_USER_PATH", WINDOWS_USER_PATH_RE),
        ("UNC_USER_PATH", UNC_USER_PATH_RE),
        ("MOBILE_NUMBER", MOBILE_RE),
    )
    for code, pattern in patterns:
        for match in pattern.finditer(text):
            findings.append(Finding(code, display, line_number(text, match.start())))

    folded = text.casefold()
    for marker in MESSAGE_EXPORT_MARKERS:
        start = 0
        while True:
            offset = folded.find(marker, start)
            if offset < 0:
                break
            findings.append(Finding("MESSAGE_EXPORT_MARKER", display, line_number(text, offset)))
            start = offset + len(marker)

    for match in EMAIL_RE.finditer(text):
        if not is_safe_example_email(match.group(0)):
            findings.append(Finding("EMAIL_ADDRESS", display, line_number(text, match.start())))

    for match in URL_RE.finditer(text):
        if suspicious_url(match.group(0)):
            findings.append(Finding("PRIVATE_OR_TOKENIZED_URL", display, line_number(text, match.start())))

    for rule in deny_rules:
        if rule.matches(text):
            findings.append(Finding("PRIVATE_DENYLIST", display, detail=f"denylist line {rule.source_line}"))
    return findings


def decode_text(path: Path) -> str | None:
    size = path.stat().st_size
    if size > MAX_TEXT_BYTES:
        return None
    raw = path.read_bytes()
    if b"\x00" in raw[:4096] and path.suffix.casefold() not in {".xml"}:
        return None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def csv_findings(path: Path, display: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
            for row_number, row in enumerate(csv.reader(handle, delimiter=delimiter), start=1):
                for column_number, cell in enumerate(row, start=1):
                    candidate = cell.lstrip(" \t\r\n\ufeff")
                    if candidate.startswith(("=", "+", "-", "@")):
                        findings.append(
                            Finding(
                                "CSV_FORMULA_INJECTION",
                                display,
                                row_number,
                                detail=f"column {column_number}",
                            )
                        )
    except (OSError, UnicodeError, csv.Error) as exc:
        findings.append(Finding("CSV_SCAN_ERROR", display, detail=type(exc).__name__))
    return findings


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def ooxml_findings(path: Path, display: str) -> list[Finding]:
    findings: list[Finding] = []
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_OOXML_ENTRIES:
                findings.append(Finding("OOXML_RESOURCE_LIMIT", display, detail="too many package entries"))
                return findings
            total_uncompressed = sum(entry.file_size for entry in entries)
            if total_uncompressed > MAX_OOXML_UNCOMPRESSED_BYTES:
                findings.append(Finding("OOXML_RESOURCE_LIMIT", display, detail="expanded package is too large"))
                return findings

            names = {entry.filename.casefold(): entry for entry in entries}
            if any(any(name.startswith(marker) for marker in COMMENT_ENTRY_MARKERS) for name in names):
                findings.append(Finding("OFFICE_COMMENTS_OR_REVIEWERS", display))

            core_entry = names.get("docprops/core.xml")
            if core_entry is not None:
                root = ET.fromstring(archive.read(core_entry))
                populated = [local_name(node.tag) for node in root.iter() if local_name(node.tag) in CORE_METADATA_TAGS and (node.text or "").strip()]
                if populated:
                    findings.append(Finding("OFFICE_PERSONAL_METADATA", display, detail="non-empty core properties"))

            if "docprops/custom.xml" in names:
                custom_root = ET.fromstring(archive.read(names["docprops/custom.xml"]))
                if any((node.text or "").strip() for node in custom_root.iter()):
                    findings.append(Finding("OFFICE_CUSTOM_METADATA", display))
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        findings.append(Finding("OOXML_SCAN_ERROR", display, detail=type(exc).__name__))
    return findings


def scan(root: Path, deny_rules: list[DenyRule]) -> tuple[list[Finding], bool]:
    findings: list[Finding] = []
    complete = True

    for path, is_link in iter_files(root):
        display = relative_display(path, root)
        if is_link:
            findings.append(Finding("SYMLINK_NOT_ALLOWED", display))
            continue

        path_findings = text_findings(display, display, deny_rules)
        findings.extend(path_findings)

        suffix = path.suffix.casefold()
        if suffix in RAW_BINARY_SUFFIXES:
            findings.append(Finding("RAW_BINARY_NOT_ALLOWED", display, detail=suffix or "binary"))
            if suffix in OOXML_SUFFIXES:
                findings.extend(ooxml_findings(path, display))
            continue

        if suffix not in TEXT_SUFFIXES:
            findings.append(Finding("UNREVIEWED_FILE_TYPE", display, detail=suffix or "no extension"))
            continue

        try:
            text = decode_text(path)
        except OSError as exc:
            findings.append(Finding("FILE_READ_ERROR", display, detail=type(exc).__name__))
            complete = False
            continue
        if text is None:
            findings.append(Finding("TEXT_SCAN_LIMIT", display, detail="file too large or binary-like"))
            complete = False
            continue

        findings.extend(text_findings(text, display, deny_rules))
        if suffix in {".csv", ".tsv"}:
            findings.extend(csv_findings(path, display))

    unique = sorted(set(findings), key=lambda item: (item.path, item.line or 0, item.code, item.detail))
    return unique, complete


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print("PRECHECK ERROR: repository root is not a directory.", file=sys.stderr)
        return 2

    try:
        deny_rules = load_denylist(args.denylist, root)
        findings, complete = scan(root, deny_rules)
    except (OSError, ScanConfigurationError) as exc:
        print(f"PRECHECK ERROR: {exc}", file=sys.stderr)
        return 2

    for finding in findings:
        print(finding.render())

    if not complete:
        print(f"PRECHECK INCOMPLETE: {len(findings)} finding(s); at least one file was not fully scanned.")
        return 2
    if findings:
        print(f"PRECHECK FAIL: {len(findings)} release blocker(s).")
        return 1
    print("PRECHECK PASS: no release blockers detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
