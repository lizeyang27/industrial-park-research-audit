from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import compare_versions  # noqa: E402
import extract_review_context  # noqa: E402
import preflight_public  # noqa: E402


DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body><w:p>
    <w:r><w:t>Public prefix. </w:t></w:r>
    <w:commentRangeStart w:id="0"/>
    <w:r><w:t>Selected confidential claim</w:t></w:r>
    <w:commentRangeEnd w:id="0"/>
    <w:r><w:commentReference w:id="0"/></w:r>
  </w:p></w:body>
</w:document>
"""

COMMENTS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:comments xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml">
  <w:comment w:id="0" w:author="Private Reviewer" w:date="2026-01-01T00:00:00Z">
    <w:p w14:paraId="00ABCDEF"><w:r><w:t>Confidential review note</w:t></w:r></w:p>
  </w:comment>
</w:comments>
"""

COMMENTS_EXTENDED_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">
  <w15:commentEx w15:paraId="00ABCDEF" w15:done="0"/>
</w15:commentsEx>
"""


def make_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", DOCUMENT_XML)
        archive.writestr("word/comments.xml", COMMENTS_XML)
        archive.writestr("word/commentsExtended.xml", COMMENTS_EXTENDED_XML)
        archive.writestr("word/media/image1.png", b"synthetic-image-placeholder")


def make_pptx(path: Path) -> None:
    slide = """<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
      xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
      <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Synthetic slide</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
    </p:sld>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", slide)


def make_xlsx(path: Path) -> None:
    shared = """<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <si><t>Synthetic cell</t></si></sst>"""
    sheet = """<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
      <sheetData><row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1"><f>1+1</f><v>2</v></c></row></sheetData>
    </worksheet>"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", shared)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


class ExtractionTests(unittest.TestCase):
    def test_default_payload_omits_private_text_and_identifiers(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.docx"
            make_docx(source)
            extracted = extract_review_context.extract_material(source)
            payload = extract_review_context.build_payload(
                source,
                extracted,
                include_body_text=False,
                include_review_text=False,
                include_identifiers=False,
            )
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("Selected confidential claim", serialized)
            self.assertNotIn("Confidential review note", serialized)
            self.assertNotIn("Private Reviewer", serialized)
            self.assertNotIn(str(source), serialized)
            self.assertEqual(payload["comments"][0]["resolved"], False)
            self.assertTrue(payload["comments"][0]["anchored"])
            self.assertEqual(payload["format_metadata"]["image_count"], 1)
            self.assertFalse(payload["format_metadata"]["image_binary_included"])
            self.assertNotIn("synthetic-image-placeholder", serialized)

    def test_explicit_flags_include_review_context(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "review.docx"
            make_docx(source)
            extracted = extract_review_context.extract_material(source)
            payload = extract_review_context.build_payload(
                source,
                extracted,
                include_body_text=True,
                include_review_text=True,
                include_identifiers=True,
            )
            self.assertIn("Selected confidential claim", payload["body_text"])
            self.assertEqual(payload["comments"][0]["review_text"], "Confidential review note")
            self.assertEqual(payload["comments"][0]["author"], "Private Reviewer")
            self.assertEqual(payload["source_name"], "review.docx")

    def test_minimal_pptx_and_xlsx_are_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            deck = root / "deck.pptx"
            book = root / "book.xlsx"
            make_pptx(deck)
            make_xlsx(book)
            deck_result = extract_review_context.extract_material(deck)
            book_result = extract_review_context.extract_material(book)
            self.assertEqual(deck_result["text"], "Synthetic slide")
            self.assertIn("Synthetic cell", book_result["text"])
            self.assertEqual(book_result["format_metadata"]["formula_count"], 1)


class ComparisonTests(unittest.TestCase):
    def test_default_comparison_retains_metrics_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before.md"
            after = root / "after.md"
            before.write_text("A sensitive thesis\nOld claim\n", encoding="utf-8")
            after.write_text("A sensitive thesis\nQualified claim\n", encoding="utf-8")
            payload = compare_versions.build_payload(before, after, include_diff_text=False)
            serialized = json.dumps(payload, ensure_ascii=False)
            self.assertNotIn("sensitive thesis", serialized)
            self.assertNotIn("Qualified claim", serialized)
            self.assertNotIn(str(before), serialized)
            self.assertGreater(payload["paragraph_similarity"], 0)

    def test_explicit_diff_contains_changed_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before = root / "before.md"
            after = root / "after.md"
            before.write_text("Shared\nOld claim\n", encoding="utf-8")
            after.write_text("Shared\nQualified claim\n", encoding="utf-8")
            payload = compare_versions.build_payload(before, after, include_diff_text=True)
            self.assertTrue(payload["diff_text_included"])
            self.assertTrue(any("Qualified claim" in line for line in payload["unified_diff"]))


class PreflightTests(unittest.TestCase):
    def test_clean_text_repository_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "README.md").write_text("Synthetic public fixture\n", encoding="utf-8")
            findings, complete = preflight_public.scan(root, [])
            self.assertTrue(complete)
            self.assertEqual(findings, [])

    def test_common_private_content_and_binary_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            private_path = "C:" + "\\" + "Us" + "ers" + "\\private\\draft.docx"
            private_email = "analyst" + "@" + "private.test"
            message_marker = "xwe" + "chat"
            (root / "notes.md").write_text(
                "\n".join((private_path, private_email, message_marker)), encoding="utf-8"
            )
            (root / "raw.docx").write_bytes(b"not an office package")
            findings, complete = preflight_public.scan(root, [])
            codes = {finding.code for finding in findings}
            self.assertTrue(complete)
            self.assertIn("WINDOWS_USER_PATH", codes)
            self.assertIn("EMAIL_ADDRESS", codes)
            self.assertIn("MESSAGE_EXPORT_MARKER", codes)
            self.assertIn("RAW_BINARY_NOT_ALLOWED", codes)

    def test_csv_formula_injection_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            payload = "value\n" + "=" + "SUM(A1:A2)\n"
            (root / "unsafe.csv").write_text(payload, encoding="utf-8")
            findings, complete = preflight_public.scan(root, [])
            self.assertTrue(complete)
            self.assertIn("CSV_FORMULA_INJECTION", {finding.code for finding in findings})


if __name__ == "__main__":
    unittest.main()
