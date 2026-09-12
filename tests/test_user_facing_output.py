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
        f"### {index}. 问题{index}\n- 原文位置：第{index}段。\n- 为什么重要：会影响判断。\n- 建议处理：补充依据。"
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

    def test_internal_code_in_technical_appendix_is_allowed(self):
        text = report_with_items() + "\n## 技术附录\n\nrisk_level: R2\nverification_level: V3\n"
        result = validate_user_report.validate_report(text)
        self.assertTrue(result["ok"])
        self.assertTrue(result["technical_appendix_present"])

    def test_missing_section_fails(self):
        text = report_with_items().replace("## 建议怎么改", "## 修改说明")
        result = validate_user_report.validate_report(text)
        self.assertFalse(result["ok"])
        self.assertIn("MISSING_REQUIRED_SECTION", result["error_codes"])

    def test_more_than_eight_priority_items_fails(self):
        result = validate_user_report.validate_report(report_with_items(9))
        self.assertFalse(result["ok"])
        self.assertIn("TOO_MANY_PRIORITY_ITEMS", result["error_codes"])

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
