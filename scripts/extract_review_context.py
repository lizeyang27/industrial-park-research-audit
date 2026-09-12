#!/usr/bin/env python3
"""Extract privacy-preserving review context from common research files.

The default JSON contains only hashes, counts, and structural comment metadata.
Raw body text, reviewer text, and personal identifiers require explicit flags.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

W = f"{{{W_NS}}}"
W14 = f"{{{W14_NS}}}"
W15 = f"{{{W15_NS}}}"

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 350 * 1024 * 1024
MAX_TEXT_CHARS = 3_000_000
SUPPORTED = {".docx", ".pptx", ".xlsx", ".txt", ".md", ".pdf"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def natural_key(value: str) -> list[Any]:
    return [int(piece) if piece.isdigit() else piece for piece in re.split(r"(\d+)", value)]


def normalize_text(value: str) -> str:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = re.sub(r"[ \t\u00a0]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def limited_text(value: str, max_chars: int) -> str:
    if len(value) > max_chars:
        raise ValueError(f"extracted text exceeds limit ({len(value)} > {max_chars})")
    return value


def check_input(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(path)
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError(f"unsupported input type: {path.suffix}")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise ValueError(f"input exceeds {MAX_FILE_BYTES} bytes")


def open_checked_zip(path: Path) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(path)
    total = sum(item.file_size for item in archive.infolist())
    if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        archive.close()
        raise ValueError(
            f"archive expands beyond {MAX_ARCHIVE_UNCOMPRESSED_BYTES} bytes"
        )
    return archive


def xml_text(element: ET.Element, text_tag: str) -> str:
    return "".join(node.text or "" for node in element.iter(text_tag))


def parse_docx(path: Path, max_chars: int) -> dict[str, Any]:
    with open_checked_zip(path) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise ValueError("DOCX has no word/document.xml")
        root = ET.fromstring(archive.read("word/document.xml"))
        image_count = sum(
            1
            for name in names
            if name.startswith("word/media/") and not name.endswith("/")
        )
        table_count = sum(1 for _ in root.iter(f"{W}tbl"))
        hyperlink_count = sum(1 for _ in root.iter(f"{W}hyperlink"))

        active: set[str] = set()
        selected: dict[str, list[str]] = {}
        contexts: dict[str, list[str]] = {}
        anchored_ids: set[str] = set()
        paragraphs: list[str] = []

        for paragraph in root.iter(f"{W}p"):
            paragraph_parts: list[str] = []
            ids_seen: set[str] = set(active)
            for node in paragraph.iter():
                if node.tag == f"{W}commentRangeStart":
                    comment_id = node.attrib.get(f"{W}id", "")
                    if comment_id:
                        active.add(comment_id)
                        anchored_ids.add(comment_id)
                        ids_seen.add(comment_id)
                elif node.tag == f"{W}commentRangeEnd":
                    comment_id = node.attrib.get(f"{W}id", "")
                    if comment_id:
                        ids_seen.add(comment_id)
                        active.discard(comment_id)
                elif node.tag in {f"{W}t", f"{W}delText"}:
                    text = node.text or ""
                    paragraph_parts.append(text)
                    for comment_id in active:
                        selected.setdefault(comment_id, []).append(text)
                        ids_seen.add(comment_id)
                elif node.tag == f"{W}tab":
                    paragraph_parts.append("\t")
                    for comment_id in active:
                        selected.setdefault(comment_id, []).append("\t")
                elif node.tag in {f"{W}br", f"{W}cr"}:
                    paragraph_parts.append("\n")
                    for comment_id in active:
                        selected.setdefault(comment_id, []).append("\n")

            paragraph_text = normalize_text("".join(paragraph_parts))
            if paragraph_text:
                paragraphs.append(paragraph_text)
                for comment_id in ids_seen:
                    contexts.setdefault(comment_id, []).append(paragraph_text)

        comment_meta: dict[str, dict[str, Any]] = {}
        comment_para_ids: dict[str, str] = {}
        if "word/comments.xml" in names:
            comments_root = ET.fromstring(archive.read("word/comments.xml"))
            for comment in comments_root.iter(f"{W}comment"):
                raw_id = comment.attrib.get(f"{W}id", "")
                if not raw_id:
                    continue
                text = normalize_text(xml_text(comment, f"{W}t"))
                first_paragraph = next(iter(comment.iter(f"{W}p")), None)
                para_id = ""
                if first_paragraph is not None:
                    para_id = first_paragraph.attrib.get(f"{W14}paraId", "")
                if para_id:
                    comment_para_ids[raw_id] = para_id
                comment_meta[raw_id] = {
                    "text": text,
                    "author": comment.attrib.get(f"{W}author", ""),
                    "date": comment.attrib.get(f"{W}date", ""),
                }

        resolved_by_para: dict[str, bool] = {}
        if "word/commentsExtended.xml" in names:
            extended_root = ET.fromstring(archive.read("word/commentsExtended.xml"))
            for item in extended_root.iter(f"{W15}commentEx"):
                para_id = item.attrib.get(f"{W15}paraId", "")
                done = item.attrib.get(f"{W15}done")
                if para_id and done is not None:
                    resolved_by_para[para_id] = done in {"1", "true", "True"}

    body_text = limited_text(normalize_text("\n".join(paragraphs)), max_chars)
    all_ids = sorted(
        set(comment_meta) | anchored_ids,
        key=lambda value: (0, int(value)) if value.isdigit() else (1, value),
    )
    comments: list[dict[str, Any]] = []
    for index, raw_id in enumerate(all_ids, start=1):
        meta = comment_meta.get(raw_id, {"text": "", "author": "", "date": ""})
        selected_text = normalize_text("".join(selected.get(raw_id, [])))
        context_text = normalize_text("\n".join(dict.fromkeys(contexts.get(raw_id, []))))
        para_id = comment_para_ids.get(raw_id, "")
        comments.append(
            {
                "comment_id": f"CMT-{index:03d}",
                "anchored": raw_id in anchored_ids,
                "selected_chars": len(selected_text),
                "context_chars": len(context_text),
                "review_chars": len(meta["text"]),
                "resolved": resolved_by_para.get(para_id),
                "_selected_text": selected_text,
                "_context_text": context_text,
                "_review_text": meta["text"],
                "_author": meta["author"],
                "_date": meta["date"],
            }
        )
    return {
        "text": body_text,
        "paragraph_count": len(paragraphs),
        "comments": comments,
        "format_metadata": {
            "comment_count": len(comments),
            "table_count": table_count,
            "hyperlink_count": hyperlink_count,
            "image_count": image_count,
            "image_binary_included": False,
        },
    }


def parse_pptx(path: Path, max_chars: int) -> dict[str, Any]:
    slides: list[str] = []
    with open_checked_zip(path) as archive:
        slide_names = sorted(
            (
                name
                for name in archive.namelist()
                if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
            ),
            key=natural_key,
        )
        for name in slide_names:
            root = ET.fromstring(archive.read(name))
            text = normalize_text("\n".join(node.text or "" for node in root.iter(f"{{{A_NS}}}t")))
            if text:
                slides.append(text)
    combined = limited_text(normalize_text("\n\n".join(slides)), max_chars)
    return {
        "text": combined,
        "paragraph_count": len(slides),
        "comments": [],
        "format_metadata": {"slide_count": len(slide_names)},
    }


def parse_xlsx(path: Path, max_chars: int) -> dict[str, Any]:
    rows: list[str] = []
    formula_count = 0
    with open_checked_zip(path) as archive:
        names = set(archive.namelist())
        shared_strings: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.iter(f"{{{S_NS}}}si"):
                shared_strings.append(xml_text(item, f"{{{S_NS}}}t"))

        sheet_names = sorted(
            (
                name
                for name in names
                if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name)
            ),
            key=natural_key,
        )
        for sheet_index, name in enumerate(sheet_names, start=1):
            root = ET.fromstring(archive.read(name))
            for row in root.iter(f"{{{S_NS}}}row"):
                values: list[str] = []
                for cell in row.iter(f"{{{S_NS}}}c"):
                    formula = cell.find(f"{{{S_NS}}}f")
                    if formula is not None:
                        formula_count += 1
                    cell_type = cell.attrib.get("t", "")
                    value_node = cell.find(f"{{{S_NS}}}v")
                    value = "" if value_node is None else (value_node.text or "")
                    if cell_type == "s" and value.isdigit():
                        position = int(value)
                        value = shared_strings[position] if position < len(shared_strings) else ""
                    elif cell_type == "inlineStr":
                        value = xml_text(cell, f"{{{S_NS}}}t")
                    if value:
                        values.append(value)
                if values:
                    rows.append(f"[sheet-{sheet_index}] " + " | ".join(values))
    combined = limited_text(normalize_text("\n".join(rows)), max_chars)
    return {
        "text": combined,
        "paragraph_count": len(rows),
        "comments": [],
        "format_metadata": {
            "sheet_count": len(sheet_names),
            "formula_count": formula_count,
        },
    }


def parse_pdf(path: Path, max_chars: int) -> dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("PDF extraction requires the optional pypdf package") from exc
    reader = PdfReader(path)
    if reader.is_encrypted and not reader.decrypt(""):
        raise RuntimeError("encrypted PDF requires a password")
    pages = [normalize_text(page.extract_text() or "") for page in reader.pages]
    combined = limited_text(normalize_text("\n\n".join(page for page in pages if page)), max_chars)
    return {
        "text": combined,
        "paragraph_count": sum(bool(page) for page in pages),
        "comments": [],
        "format_metadata": {"page_count": len(pages)},
    }


def parse_text(path: Path, max_chars: int) -> dict[str, Any]:
    raw = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UnicodeDecodeError("unknown", raw, 0, 1, "unsupported text encoding")
    normalized = limited_text(normalize_text(text), max_chars)
    return {
        "text": normalized,
        "paragraph_count": len([part for part in normalized.split("\n") if part.strip()]),
        "comments": [],
        "format_metadata": {},
    }


def extract_material(path: Path, max_chars: int = MAX_TEXT_CHARS) -> dict[str, Any]:
    check_input(path)
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return parse_docx(path, max_chars)
    if suffix == ".pptx":
        return parse_pptx(path, max_chars)
    if suffix == ".xlsx":
        return parse_xlsx(path, max_chars)
    if suffix == ".pdf":
        return parse_pdf(path, max_chars)
    return parse_text(path, max_chars)


def build_payload(
    path: Path,
    extracted: dict[str, Any],
    *,
    include_body_text: bool,
    include_review_text: bool,
    include_identifiers: bool,
) -> dict[str, Any]:
    digest = sha256_file(path)
    body_text = extracted["text"]
    payload: dict[str, Any] = {
        "schema_version": "0.1",
        "document_id": f"DOC-{digest[:12]}",
        "file_type": path.suffix.lower(),
        "bytes": path.stat().st_size,
        "content_sha256": sha256_bytes(body_text.encode("utf-8")),
        "text_chars": len(body_text),
        "paragraph_or_block_count": extracted["paragraph_count"],
        "format_metadata": extracted["format_metadata"],
        "comments": [],
        "privacy": {
            "body_text_included": include_body_text,
            "review_text_included": include_review_text,
            "identifiers_included": include_identifiers,
            "local_path_included": False,
        },
    }
    if include_body_text:
        payload["body_text"] = body_text
    if include_identifiers:
        payload["source_name"] = path.name

    for raw in extracted["comments"]:
        record = {key: value for key, value in raw.items() if not key.startswith("_")}
        if include_review_text:
            record.update(
                {
                    "selected_text": raw["_selected_text"],
                    "containing_paragraph": raw["_context_text"],
                    "review_text": raw["_review_text"],
                }
            )
        if include_identifiers:
            record.update({"author": raw["_author"], "date": raw["_date"]})
        payload["comments"].append(record)
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-body-text", action="store_true")
    parser.add_argument("--include-review-text", action="store_true")
    parser.add_argument("--include-identifiers", action="store_true")
    parser.add_argument("--max-chars", type=int, default=MAX_TEXT_CHARS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.max_chars <= 0:
        raise ValueError("--max-chars must be positive")
    extracted = extract_material(args.input, args.max_chars)
    payload = build_payload(
        args.input,
        extracted,
        include_body_text=args.include_body_text,
        include_review_text=args.include_review_text,
        include_identifiers=args.include_identifiers,
    )
    write_json(args.output, payload)
    if args.include_body_text or args.include_review_text or args.include_identifiers:
        print("warning: output contains explicitly requested sensitive text or identifiers", file=sys.stderr)
    print(json.dumps({"document_id": payload["document_id"], "output_name": args.output.name}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
        raise SystemExit(2)
