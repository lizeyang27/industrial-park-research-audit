from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_user_report  # noqa: E402


def report_with_items(count: int = 1) -> str:
    items = "\n".join(
        f"### {index}. 问题事项{index}\n- 原文位置：文章第{index}段。\n- 为什么重要：会影响判断。\n- 建议处理：补充依据。"
        for index in range(1, count + 1)
    )
    return f"""# 审阅报告

## 结论
修改后发布。核心数字需要补充来源。

## 这篇文章已经做好的地方
- 结构清楚。

## 发布前需要处理
{items}

## 建议怎么改
先补证，再缩窄结论。

## 可继续研究的新观点
本稿暂不足以形成可靠的新观点。

## 如果还要继续核验
可以停在当前结论，也可以只查关键数字，或请行业专家判断机制。

## 本次审阅没有验证什么
没有联网，也没有取得原始项目资料。
"""


class UserFacingOutputTests(unittest.TestCase):
    def test_readable_report_passes(self):
        result = validate_user_report.validate_report(report_with_items())
        self.assertTrue(result["ok"])
        self.assertEqual(result["error_codes"], [])

    def test_internal_code_in_reader_layer_fails(self):
        text = report_with_items().replace("会影响判断", "属于 R2 问题，会影响判断")
        result = validate_user_report.validate_report(text)
        self.assertFalse(result["ok"])
        self.assertIn("INTERNAL_CODE_IN_READER_LAYER", result["error_codes"])

    def test_v12_machine_fields_and_ids_do_not_leak_into_reader_layer(self):
        text = report_with_items().replace(
            "先补证，再缩窄结论。",
            "evidence_refs: EV-001 / verification_action_refs: ACT-001 / "
            "closure_decision_ref: CLS-001",
        )
        result = validate_user_report.validate_report(text)
        self.assertFalse(result["ok"])
        self.assertIn("MACHINE_FIELD_IN_READER_LAYER", result["error_codes"])
        self.assertIn("INTERNAL_ID_IN_READER_LAYER", result["error_codes"])

    def test_internal_code_in_technical_appendix_is_allowed(self):
        text = report_with_items() + "\n## 技术附录\n\nrisk_level: R2\nverification_level: V3\n"
        result = validate_user_report.validate_report(text)
        self.assertTrue(result["ok"])
        self.assertTrue(result["technical_appendix_present"])

        wrong_level = report_with_items() + "\n### 技术附录\n\nrisk_level: R2\n"
        result = validate_user_report.validate_report(wrong_level)
        self.assertFalse(result["ok"])
        self.assertIn("MACHINE_FIELD_IN_READER_LAYER", result["error_codes"])

    def test_missing_section_fails(self):
        text = report_with_items().replace("## 建议怎么改", "## 修改说明")
        result = validate_user_report.validate_report(text)
        self.assertFalse(result["ok"])
        self.assertIn("MISSING_REQUIRED_SECTION", result["error_codes"])

    def test_more_than_eight_priority_items_fails(self):
        result = validate_user_report.validate_report(report_with_items(9))
        self.assertFalse(result["ok"])
        self.assertIn("TOO_MANY_PRIORITY_ITEMS", result["error_codes"])

    def test_publication_status_must_lead_without_negation(self):
        for replacement in (
            "无需修改后发布，建议直接上线。",
            "不可以发布，仍需补证。",
            "目前尚不能说可以发布。",
            "修改后发布，但该结论不成立。",
        ):
            with self.subTest(replacement=replacement):
                text = report_with_items().replace("修改后发布。核心数字需要补充来源。", replacement)
                result = validate_user_report.validate_report(text)
                self.assertFalse(result["ok"])
                self.assertIn("PUBLICATION_STATUS_NOT_LEADING", result["error_codes"])

        duplicated = report_with_items() + "\n## 结论\n可以发布。\n"
        result = validate_user_report.validate_report(duplicated)
        self.assertFalse(result["ok"])
        self.assertIn("DUPLICATE_REQUIRED_SECTION", result["error_codes"])

        contradicted = report_with_items().replace(
            "修改后发布。核心数字需要补充来源。",
            "可以发布。这个结论不成立，文章不得上线。",
        )
        result = validate_user_report.validate_report(contradicted)
        self.assertFalse(result["ok"])
        self.assertIn("READY_CONCLUSION_CONTRADICTED", result["error_codes"])

    def test_ready_conclusion_rejects_common_release_reversal_language(self):
        for conclusion in (
            "可以发布。请勿上线。",
            "可以发布。禁止上线。",
            "可以发布。文章仍不适合公开。",
            "可以发布。上线后请立即下线。",
            "可以发布。先暂不发布。",
            "可以发布。勿上线。",
            "可以发布。取消发稿。",
            "可以发布。请先搁置。",
            "可以发布。当前版本只供内部流转，完成修改后再对外。",
            "可以发布。待补证后再公开。",
        ):
            with self.subTest(conclusion=conclusion):
                text = report_with_items().replace(
                    "修改后发布。核心数字需要补充来源。", conclusion
                )
                result = validate_user_report.validate_report(text)
                self.assertFalse(result["ok"])
                self.assertIn("READY_CONCLUSION_CONTRADICTED", result["error_codes"])

    def test_hidden_or_code_fenced_reader_report_fails(self):
        for wrapped in (
            f"<!--\n{report_with_items()}\n-->\n文章目前不得上线。",
            f"```markdown\n{report_with_items()}\n```\n文章目前不得上线。",
        ):
            with self.subTest(prefix=wrapped[:4]):
                result = validate_user_report.validate_report(wrapped)
                self.assertFalse(result["ok"])
                self.assertIn("HIDDEN_OR_CODE_FENCED_READER_CONTENT", result["error_codes"])

    def test_reader_viewpoints_are_counted_and_capped(self):
        text = report_with_items().replace(
            "本稿暂不足以形成可靠的新观点。",
            "### 1. 观点一\n"
            "- 适用对象：园区运营方\n"
            "- 决策问题：是否调整招商顺序\n"
            "- 可能机制：服务能力可能影响企业留存\n"
            "- 适用边界：只适用于同类园区比较\n"
            "- 反证条件：若留存率不随服务变化则不成立\n\n"
            "### 2. 观点二\n"
            "- 适用对象：入园企业\n"
            "- 决策问题：是否核验长期租约\n"
            "- 可能机制：免租期可能抬高表面出租率\n"
            "- 适用边界：只适用于存在免租安排的项目\n"
            "- 反证条件：若租约均正常付租则不成立",
        )
        result = validate_user_report.validate_report(text)
        self.assertTrue(result["ok"], result["error_codes"])
        self.assertEqual(result["new_viewpoint_count"], 2)

        third_viewpoint = (
            "\n### 3. 观点三\n"
            "- 适用对象：地方政府\n"
            "- 决策问题：是否调整产业政策\n"
            "- 可能机制：政策门槛可能改变企业选择\n"
            "- 适用边界：只适用于政策稳定地区\n"
            "- 反证条件：若企业选择不变则不成立\n"
        )
        too_many = text.replace(
            "\n## 如果还要继续核验", third_viewpoint + "\n## 如果还要继续核验"
        )
        result = validate_user_report.validate_report(too_many)
        self.assertFalse(result["ok"])
        self.assertIn("TOO_MANY_READER_VIEWPOINTS", result["error_codes"])

    def test_each_priority_item_keeps_location_reason_and_action_labels(self):
        for label in ("原文位置", "为什么重要", "建议处理"):
            with self.subTest(label=label):
                result = validate_user_report.validate_report(
                    report_with_items().replace(label, "说明")
                )
                self.assertFalse(result["ok"])

    def test_issue_and_viewpoint_titles_need_substantive_text(self):
        issue_title = report_with_items().replace("问题事项1", "....")
        result = validate_user_report.validate_report(issue_title)
        self.assertIn("PRIORITY_ITEM_HEADING_MISSING", result["error_codes"])

        viewpoint_title = report_with_items().replace(
            "本稿暂不足以形成可靠的新观点。",
            "### 1. ....\n"
            "- 适用对象：园区运营方\n"
            "- 决策问题：是否调整招商顺序\n"
            "- 可能机制：服务能力可能影响企业留存\n"
            "- 适用边界：只适用于同类园区比较\n"
            "- 反证条件：若留存率不随服务变化则不成立",
        )
        result = validate_user_report.validate_report(viewpoint_title)
        self.assertIn("NEW_VIEWPOINT_HEADING_MISSING", result["error_codes"])

    def test_priority_item_values_cannot_be_punctuation_shells(self):
        replacements = {
            "文章第1段。": "....",
            "会影响判断。": "……！！",
            "补充依据。": "----",
        }
        for original, punctuation_only in replacements.items():
            with self.subTest(original=original):
                result = validate_user_report.validate_report(
                    report_with_items().replace(original, punctuation_only)
                )
                self.assertFalse(result["ok"])
                self.assertTrue(
                    any(code.startswith("PRIORITY_ITEM_") for code in result["error_codes"]),
                    result["error_codes"],
                )

        for short_value in ("首段", "数字", "补证"):
            with self.subTest(short_value=short_value):
                text = report_with_items()
                text = text.replace("文章第1段。", short_value, 1)
                result = validate_user_report.validate_report(text)
                self.assertIn("PRIORITY_ITEM_LOCATION_MISSING", result["error_codes"])

    def test_required_sections_cannot_be_heading_only(self):
        for body in (
            "- 结构清楚。",
            "先补证，再缩窄结论。",
            "可以停在当前结论，也可以只查关键数字，或请行业专家判断机制。",
            "没有联网，也没有取得原始项目资料。",
        ):
            with self.subTest(body=body):
                text = report_with_items().replace(body, "")
                result = validate_user_report.validate_report(text)
                self.assertIn("REQUIRED_SECTION_BODY_EMPTY", result["error_codes"])

    def test_html_and_entities_cannot_fake_visible_section_content(self):
        for fake_body in ("<span></span>", "<div></div>", "&nbsp;&nbsp;"):
            with self.subTest(fake_body=fake_body):
                text = report_with_items().replace("- 结构清楚。", fake_body)
                result = validate_user_report.validate_report(text)
                self.assertFalse(result["ok"])
                self.assertIn("REQUIRED_SECTION_BODY_EMPTY", result["error_codes"])
                if fake_body.startswith("<"):
                    self.assertIn("RAW_HTML_IN_READER_CONTENT", result["error_codes"])

    def test_viewpoint_items_require_five_readable_labels(self):
        viewpoint = (
            "### 1. 服务能力可能影响续租\n"
            "- 适用对象：园区运营方\n"
            "- 决策问题：是否优先投资企业服务\n"
            "- 可能机制：深度服务可能提高企业留存\n"
            "- 适用边界：只适用于服务可形成切换成本的园区\n"
            "- 反证条件：若服务变化后续租率不变则不成立"
        )
        text = report_with_items().replace("本稿暂不足以形成可靠的新观点。", viewpoint)
        self.assertTrue(validate_user_report.validate_report(text)["ok"])

        for label, code in (
            ("适用对象", "NEW_VIEWPOINT_ACTOR_LABEL_MISSING"),
            ("决策问题", "NEW_VIEWPOINT_DECISION_LABEL_MISSING"),
            ("可能机制", "NEW_VIEWPOINT_MECHANISM_LABEL_MISSING"),
            ("适用边界", "NEW_VIEWPOINT_BOUNDARY_LABEL_MISSING"),
            ("反证条件", "NEW_VIEWPOINT_FALSIFIER_LABEL_MISSING"),
        ):
            with self.subTest(label=label):
                result = validate_user_report.validate_report(
                    text.replace(f"- {label}：", "- 其他说明：", 1)
                )
                self.assertIn(code, result["error_codes"])

    def test_h3_is_reserved_for_issue_and_viewpoint_items(self):
        text = report_with_items().replace(
            "- 结构清楚。", "- 结构清楚。\n\n### 不应出现的子标题\n补充说明。"
        )
        result = validate_user_report.validate_report(text)
        self.assertIn("UNEXPECTED_H3_OUTSIDE_ITEM_SECTIONS", result["error_codes"])

    def test_loose_label_mentions_cannot_fake_priority_fields(self):
        text = report_with_items().replace(
            "- 原文位置：文章第1段。", "备注：原文位置在文章第1段，但这里不是规范标签。"
        )
        result = validate_user_report.validate_report(text)
        self.assertIn("PRIORITY_ITEM_LOCATION_MISSING", result["error_codes"])

    def test_zero_viewpoint_marker_is_required_and_cannot_conflict(self):
        missing = report_with_items().replace("本稿暂不足以形成可靠的新观点。", "暂无。")
        result = validate_user_report.validate_report(missing)
        self.assertIn("NEW_VIEWPOINT_ZERO_MARKER_MISSING", result["error_codes"])

        conflicting = report_with_items().replace(
            "本稿暂不足以形成可靠的新观点。",
            "本稿暂不足以形成可靠的新观点。\n\n### 1. 观点一\n说明。",
        )
        result = validate_user_report.validate_report(conflicting)
        self.assertIn("NEW_VIEWPOINT_ZERO_MARKER_CONFLICT", result["error_codes"])

    def test_zero_item_markers_cannot_be_quoted_or_negated(self):
        issue_block = (
            "### 1. 问题事项1\n"
            "- 原文位置：文章第1段。\n"
            "- 为什么重要：会影响判断。\n"
            "- 建议处理：补充依据。"
        )
        zero_issue_report = report_with_items().replace(
            issue_block, "本稿没有仍需在发布前处理的问题。"
        )
        negated_issue = zero_issue_report.replace(
            "本稿没有仍需在发布前处理的问题。",
            "并不是“本稿没有仍需在发布前处理的问题”，实际仍有问题。",
        )
        result = validate_user_report.validate_report(negated_issue)
        self.assertIn("PRIORITY_ISSUE_ZERO_MARKER_MISSING", result["error_codes"])

        negated_viewpoint = report_with_items().replace(
            "本稿暂不足以形成可靠的新观点。",
            "并非“本稿暂不足以形成可靠的新观点”，实际已有观点。",
        )
        result = validate_user_report.validate_report(negated_viewpoint)
        self.assertIn("NEW_VIEWPOINT_ZERO_MARKER_MISSING", result["error_codes"])

    def test_cli_output_is_privacy_minimized(self):
        private_marker = "private-report-canary"
        with tempfile.TemporaryDirectory() as temp_dir:
            report_path = Path(temp_dir) / f"{private_marker}.md"
            report_path.write_text(report_with_items() + f"\n{private_marker}\n", encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "validate_user_report.py"), str(report_path)],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertNotIn(private_marker, completed.stdout)


if __name__ == "__main__":
    unittest.main()
