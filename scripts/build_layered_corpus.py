#!/usr/bin/env python3
"""Promote authorized staging captures into private L0/L1 corpus layers.

The tool never prints titles, article text, URLs, hashes, or absolute paths.
It uses deterministic content hashes, preserves the captured document as an
immutable L0 object, and builds a reproducible paragraph index in L1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "0.1"
METHOD_VERSION = "layered-corpus-builder-v0.1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_document(value: str) -> str:
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip() + "\n"


def clean_for_index(document: str, title: str) -> str:
    """Remove capture chrome while retaining the article's textual structure."""
    lines = normalize_document(document).splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == title.strip()), 0)
    lines = lines[start:]

    body_start = 1
    time_pattern = re.compile(
        r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) "
        r"\d{1,2}, 20\d{2}, \d{1,2}:\d{2} [AP]M$"
    )
    for index, line in enumerate(lines[:12]):
        if time_pattern.match(line.strip()):
            body_start = index + 1
            while body_start < len(lines) and (
                not lines[body_start].strip()
                or "listened" in lines[body_start].casefold()
                or lines[body_start].strip() == "￼"
            ):
                body_start += 1
            break
    lines = lines[body_start:]

    stop = len(lines)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "Read more" or re.fullmatch(r"Reads\d+", stripped):
            stop = index
            break
    lines = lines[:stop]

    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped in {"￼", "END"}:
            continue
        if stripped:
            cleaned.append(stripped)
    return "\n".join(cleaned).strip() + "\n"


def parse_published_at(raw: str | None, day: str | None) -> str | None:
    if raw:
        parsed = datetime.strptime(raw, "%b %d, %Y, %I:%M %p")
        return parsed.replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    if day:
        return f"{day}T00:00:00+08:00"
    return None


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"record at line {line_number} is not an object")
        rows.append(value)
    return rows


def write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(temp, path)


def common_envelope(
    *, record_id: str, layer: str, parent_ids: list[str], input_hashes: list[dict[str, str]],
    created_at: str, authorization_id: str, corpus_id: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "record_id": record_id,
        "corpus_id": corpus_id,
        "layer": layer,
        "parent_ids": parent_ids,
        "input_hashes": input_hashes,
        "created_at": created_at,
        "method_version": METHOD_VERSION,
        "rights_ref": authorization_id,
        "access_class": "private",
        "lifecycle": {"status": "active", "review_by": None, "supersedes": [], "superseded_by": []},
        "human_review": {"status": "unreviewed", "reviewed_at": None, "reviewer_role": None, "notes": []},
    }


def validate_staging(rows: list[dict[str, Any]], snapshot_id: str, authorization_id: str) -> None:
    seen: set[tuple[str, str]] = set()
    for index, row in enumerate(rows, start=1):
        required = {
            "record_type", "snapshot_id", "article_id", "title", "publisher_name",
            "published_date_local", "document_text", "document_text_sha256", "captured_at",
            "capture_status", "rights_ref", "instructions_treated_as_data",
        }
        missing = sorted(required - row.keys())
        if missing:
            raise ValueError(f"staging record {index} is missing required fields")
        if row["record_type"] != "staging_article_capture" or row["snapshot_id"] != snapshot_id:
            raise ValueError(f"staging record {index} has the wrong type or snapshot")
        if row["rights_ref"] != authorization_id or row["instructions_treated_as_data"] is not True:
            raise ValueError(f"staging record {index} fails the authorization boundary")
        if row["capture_status"] != "captured" or not row["document_text"].strip():
            raise ValueError(f"staging record {index} is not a complete captured record")
        document = normalize_document(row["document_text"])
        if sha256_text(document.rstrip("\n")) != row["document_text_sha256"]:
            # Capture hashes were computed before the canonical trailing newline.
            if sha256_text(row["document_text"]) != row["document_text_sha256"]:
                raise ValueError(f"staging record {index} has a document hash mismatch")
        key = (row["published_date_local"], row["title"])
        if key in seen:
            raise ValueError(f"staging record {index} duplicates a date/title key")
        seen.add(key)


def build_layers(
    rows: list[dict[str, Any]], corpus_root: Path, snapshot_id: str, authorization_id: str,
    corpus_id: str, merge_existing: bool,
) -> dict[str, Any]:
    l0_path = corpus_root / "l0_raw" / "records.jsonl"
    l1_path = corpus_root / "l1_index" / "records.jsonl"
    l0_records = load_jsonl(l0_path) if merge_existing and l0_path.exists() else []
    l1_records = load_jsonl(l1_path) if merge_existing and l1_path.exists() else []
    if any(record.get("corpus_id") != corpus_id for record in (*l0_records, *l1_records)):
        raise ValueError("existing corpus records do not match --corpus-id")
    existing_source_versions = {
        record.get("source_version_id"): record
        for record in l0_records
        if record.get("source_version_id")
    }
    existing_versions_by_article: dict[str, list[dict[str, Any]]] = {}
    for record in l0_records:
        article_id = record.get("article_id")
        if isinstance(article_id, str) and article_id:
            existing_versions_by_article.setdefault(article_id, []).append(record)
    content_first_seen: dict[str, str] = {}
    for record in l1_records:
        clean_hash = record.get("text_object", {}).get("sha256")
        article_id = record.get("article_id")
        if clean_hash and article_id and clean_hash not in content_first_seen:
            content_first_seen[clean_hash] = article_id
    duplicate_content_count = 0
    new_source_record_count = 0

    for row in sorted(
        rows,
        key=lambda item: (str(item.get("published_date_local") or ""), item["title"]),
        reverse=True,
    ):
        document = normalize_document(row["document_text"])
        raw_hash = sha256_text(document)
        clean_text = clean_for_index(document, row["title"])
        clean_hash = sha256_text(clean_text)
        source_version_id = f"SRCV-{raw_hash[:24]}"
        l0_record_id = f"L0-{raw_hash[:24]}"
        raw_relative = Path("l0_raw") / "objects" / f"{source_version_id}.txt"
        raw_path = corpus_root / raw_relative
        if raw_path.exists():
            if sha256_text(raw_path.read_text(encoding="utf-8")) != raw_hash:
                raise ValueError("immutable L0 object collision")
        else:
            write_atomic(raw_path, document)

        existing = existing_source_versions.get(source_version_id)
        if existing is not None:
            if existing.get("article_id") != row["article_id"]:
                raise ValueError("immutable source version belongs to another article")
            existing["snapshot_ids"] = sorted(set(existing.get("snapshot_ids", [])) | {snapshot_id})
            continue

        prior_versions = existing_versions_by_article.get(row["article_id"], [])
        superseded_ids = {
            record.get("supersedes_source_version_id")
            for record in prior_versions
            if isinstance(record.get("supersedes_source_version_id"), str)
        }
        tips = [
            record for record in prior_versions
            if record.get("source_version_id") not in superseded_ids
        ]
        if prior_versions and len(tips) != 1:
            raise ValueError("existing article version history has no unique latest version")
        supersedes_source_version_id = tips[0]["source_version_id"] if tips else None

        input_digest = sha256_text(canonical_json({k: v for k, v in row.items() if k != "document_text"}))
        created_at = row["captured_at"].replace("Z", "+00:00")
        duplicate_of = content_first_seen.get(clean_hash)
        if duplicate_of:
            duplicate_content_count += 1
        else:
            content_first_seen[clean_hash] = row["article_id"]

        l0 = common_envelope(
            record_id=l0_record_id,
            layer="L0",
            parent_ids=[],
            input_hashes=[{"object_id": f"STAGING-{row['article_id']}", "algorithm": "sha256", "digest": input_digest}],
            created_at=created_at,
            authorization_id=authorization_id,
            corpus_id=corpus_id,
        )
        l0.update({
            "article_id": row["article_id"],
            "source_version_id": source_version_id,
            "snapshot_ids": [snapshot_id],
            "canonical_url": row.get("source_locator", {}).get("original_url"),
            "platform_article_id": row.get("source_locator", {}).get("native_article_id"),
            "source_locator_note": row.get("source_locator_note") or "Authorized local source; the staging record did not provide a public canonical locator.",
            "title": row["title"],
            "publisher": row["publisher_name"],
            "published_at": parse_published_at(row.get("published_at_raw"), row.get("published_date_local")),
            "source_updated_at": None,
            "captured_at": created_at,
            "source_status": "published",
            "raw_object": {
                "relative_path": raw_relative.as_posix(),
                "mime_type": "text/plain; charset=utf-8",
                "sha256": raw_hash,
                "bytes": len(document.encode("utf-8")),
                "capture_status": "captured",
                "rights_status": "authorized_local",
            },
            "supersedes_source_version_id": supersedes_source_version_id,
        })
        if duplicate_of:
            l0["human_review"]["notes"].append("Exact captured-text duplicate of another publication occurrence; publication identity remains separate.")
        l0_records.append(l0)
        existing_source_versions[source_version_id] = l0
        existing_versions_by_article.setdefault(row["article_id"], []).append(l0)
        new_source_record_count += 1

        clean_relative = Path("l1_index") / "text" / f"{source_version_id}.txt"
        write_atomic(corpus_root / clean_relative, clean_text)
        offset = 0
        section_path: list[str] = []
        for paragraph_index, paragraph in enumerate(clean_text.splitlines()):
            if re.fullmatch(r"\d{2}", paragraph.strip()):
                section_path = [paragraph.strip()]
            start = offset
            end = start + len(paragraph)
            chunk_digest = sha256_text(f"{source_version_id}\x1f{paragraph_index}\x1f{paragraph}")[:24]
            chunk_id = f"CHK-{chunk_digest}"
            l1 = common_envelope(
                record_id=f"L1-{chunk_digest}",
                layer="L1",
                parent_ids=[l0_record_id],
                input_hashes=[{"object_id": source_version_id, "algorithm": "sha256", "digest": raw_hash}],
                created_at=created_at,
                authorization_id=authorization_id,
                corpus_id=corpus_id,
            )
            l1.update({
                "article_id": row["article_id"],
                "source_version_id": source_version_id,
                "chunk_id": chunk_id,
                "locator": {
                    "source_version_id": source_version_id,
                    "chunk_id": chunk_id,
                    "section_path": list(section_path),
                    "paragraph_index": paragraph_index,
                    "char_start": start,
                    "char_end": end,
                },
                "text_object": {
                    "relative_path": clean_relative.as_posix(),
                    "sha256": clean_hash,
                    "character_count": len(clean_text),
                    "contains_source_text": True,
                },
                "language": "zh-CN",
                "parser": {"name": "staging-article-text-cleaner", "version": "0.1"},
                "parsed_at": created_at,
                "index_status": "active",
            })
            l1_records.append(l1)
            offset = end + 1

    l0_text = "".join(canonical_json(row) + "\n" for row in l0_records)
    l1_text = "".join(canonical_json(row) + "\n" for row in l1_records)
    write_atomic(corpus_root / "l0_raw" / "records.jsonl", l0_text)
    write_atomic(corpus_root / "l1_index" / "records.jsonl", l1_text)
    return {
        "source_record_count": len(l0_records),
        "chunk_record_count": len(l1_records),
        "new_source_record_count": new_source_record_count,
        "existing_source_record_count": len(l0_records) - new_source_record_count,
        "duplicate_content_count": duplicate_content_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staging", required=True, type=Path)
    parser.add_argument("--corpus-root", required=True, type=Path)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--corpus-id", required=True)
    parser.add_argument("--authorization-id", required=True)
    parser.add_argument("--merge-existing", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        rows = load_jsonl(args.staging)
        if not rows:
            raise ValueError("staging input is empty")
        validate_staging(rows, args.snapshot_id, args.authorization_id)
        if args.dry_run:
            result = {"status": "valid", "mode": "dry_run", "record_count": len(rows), "writes": 0}
        else:
            built = build_layers(
                rows,
                args.corpus_root,
                args.snapshot_id,
                args.authorization_id,
                args.corpus_id,
                args.merge_existing,
            )
            result = {"status": "built", "mode": "write", **built}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "error", "error_code": error.__class__.__name__}, sort_keys=True))
        return 2


if __name__ == "__main__":
    sys.exit(main())
