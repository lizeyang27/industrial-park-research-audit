from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import validate_review_bundle  # noqa: E402
import validate_user_report  # noqa: E402
from tests.test_review_bundle import (  # noqa: E402
    DRAFT,
    SUPPLIED_BYTES,
    add_supplied_input,
    bind_first_issue_to_reader,
    clear_viewpoints,
    digest,
    run_validation,
    set_active_publication_status,
    valid_ledger,
    valid_report,
)


def derived_calculation_ledger(
    expression: str,
    calculation_result: str = "80%",
    source_value: object = 999,
) -> dict[str, object]:
    ledger = valid_ledger()
    claim = ledger["claim_register"][0]
    claim["evidence_status"] = "derived"
    claim["derived_result"] = "80%"
    claim["calculation_evidence_ref"] = "EV-001"
    ledger["issues"][0]["verification_status"] = "derived"
    calculation = ledger["evidence_records"][0]
    calculation["acquisition"] = "reproducible_calculation"
    calculation.pop("url")
    calculation["input_refs"] = ["formula:edge-case"]
    calculation["input_evidence_refs"] = ["EV-002"]
    calculation["calculation_expression"] = expression
    calculation["calculation_result"] = calculation_result
    calculation["calculation_operands"] = [
        {"name": "source_value", "value": source_value, "evidence_ref": "EV-002"}
    ]
    ledger["evidence_records"].append(
        {
            "evidence_id": "EV-002",
            "acquisition": "opened_page",
            "url": "https://example.gov.cn/not-a-real-case",
            "locator": "合成页面中的输入值",
            "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            "fit_target_refs": ["claim:CLM-001", "viewpoint:VP-001"],
        }
    )
    return ledger


class ReleaseGateEdgeCaseTests(unittest.TestCase):
    def test_encoded_and_additional_search_engines_are_not_evidence(self):
        for search_url in (
            "https://search.brave.com/search?q=industrial+park",
            "https://www.ecosia.org/search?q=industrial+park",
            "https://www.qwant.com/?q=industrial+park&t=web",
            "https://www.google.com/%73earch?q=industrial+park",
            "https://search.example/search?q=industrial+park",
            "https://example.gov.cn/search?q=industrial+park",
            "https://www.mojeek.com/search?q=industrial+park",
        ):
            with self.subTest(search_url=search_url):
                ledger = valid_ledger()
                ledger["web_trace"]["opened_pages"] = [search_url]
                ledger["evidence_records"][0]["url"] = search_url
                result = run_validation(ledger)
                self.assertIn("SEARCH_RESULT_URL_NOT_ADMISSIBLE", result["error_codes"])

    def test_supplied_source_cannot_rely_on_a_self_declared_hash(self):
        ledger = valid_ledger()
        add_supplied_input(ledger)
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["SUPPLIED-001"]

        result = run_validation(ledger, verify_supplied_inputs=False)
        self.assertIn("SUPPLIED_INPUT_BYTES_NOT_VERIFIED", result["error_codes"])
        self.assertIn("SUPPLIED_INPUT_NOT_BYTE_VERIFIED", result["error_codes"])

        result = validate_review_bundle.validate_bundle(
            DRAFT.encode("utf-8"),
            ledger,
            valid_report().encode("utf-8"),
            supplied_input_bytes={"SUPPLIED-001": b"different source bytes"},
        )
        self.assertIn("SUPPLIED_INPUT_SHA256_MISMATCH", result["error_codes"])

        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])
        self.assertEqual(result["verified_supplied_input_count"], 1)

    def test_supplied_input_ids_cannot_contain_local_paths(self):
        for unsafe_id in (
            "Q:" + "\\synthetic\\private.docx",
            "FI" + "LE:///Q:/synthetic/private.docx",
            "folder\\private.docx",
            "folder/private.docx",
            "../private.docx",
        ):
            with self.subTest(unsafe_id=unsafe_id):
                ledger = valid_ledger()
                add_supplied_input(ledger)
                ledger["supplied_inputs"][0]["input_id"] = unsafe_id
                result = validate_review_bundle.validate_bundle(
                    DRAFT.encode("utf-8"),
                    ledger,
                    valid_report().encode("utf-8"),
                    supplied_input_bytes={unsafe_id: SUPPLIED_BYTES},
                )
                self.assertIn("SUPPLIED_INPUT_ID_UNSAFE", result["error_codes"])

                ledger["evidence_records"][0].update(
                    {
                        "acquisition": "supplied_source",
                        "input_refs": [unsafe_id],
                    }
                )
                ledger["evidence_records"][0].pop("url", None)
                result = validate_review_bundle.validate_bundle(
                    DRAFT.encode("utf-8"),
                    ledger,
                    valid_report().encode("utf-8"),
                    supplied_input_bytes={unsafe_id: SUPPLIED_BYTES},
                )
                self.assertIn("SUPPLIED_INPUT_ID_UNSAFE", result["error_codes"])

    def test_cli_rejects_path_like_supplied_input_ids(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            draft_path = temp / "draft.md"
            report_path = temp / "report.md"
            ledger_path = temp / "ledger.json"
            source_path = temp / "source.bin"
            draft_path.write_bytes(DRAFT.encode("utf-8"))
            report_path.write_bytes(valid_report().encode("utf-8"))
            ledger_path.write_text(
                json.dumps(valid_ledger(), ensure_ascii=False), encoding="utf-8"
            )
            source_path.write_bytes(SUPPLIED_BYTES)
            unsafe_id = "Q:" + "\\synthetic\\private.docx"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_review_bundle.py"),
                    str(draft_path),
                    str(ledger_path),
                    str(report_path),
                    "--supplied-input",
                    f"{unsafe_id}={source_path}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            "SUPPLIED_INPUT_ARGUMENT_ID_UNSAFE",
            json.loads(completed.stdout)["error_codes"],
        )

    def test_cli_rehashes_the_supplied_source_file(self):
        report = valid_report()
        ledger = valid_ledger(report)
        add_supplied_input(ledger)
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["SUPPLIED-001"]

        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            draft_path = temp / "draft.md"
            report_path = temp / "report.md"
            ledger_path = temp / "ledger.json"
            source_path = temp / "source.bin"
            draft_path.write_bytes(DRAFT.encode("utf-8"))
            report_path.write_bytes(report.encode("utf-8"))
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
            source_path.write_bytes(SUPPLIED_BYTES)
            completed = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "scripts" / "validate_review_bundle.py"),
                    str(draft_path),
                    str(ledger_path),
                    str(report_path),
                    "--supplied-input",
                    f"SUPPLIED-001={source_path}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 0, completed.stdout)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["verified_supplied_input_count"], 1)
        self.assertNotIn(str(source_path), completed.stdout)

    def test_supplied_input_cannot_be_used_before_it_was_captured(self):
        ledger = valid_ledger()
        add_supplied_input(ledger)
        ledger["supplied_inputs"][0]["captured_at"] = "2026-09-14T10:00:00+08:00"
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["SUPPLIED-001"]
        result = run_validation(ledger)
        self.assertIn(
            "VERIFICATION_ACTION_BEFORE_SUPPLIED_INPUT_CAPTURE", result["error_codes"]
        )
        self.assertIn(
            "CLOSURE_DECISION_BEFORE_SUPPLIED_INPUT_CAPTURE", result["error_codes"]
        )

        ledger["verification_actions"][0]["performed_at"] = (
            "2026-09-14T10:05:00+08:00"
        )
        ledger["closure_decisions"][0]["reviewed_at"] = (
            "2026-09-14T10:10:00+08:00"
        )
        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_reader_issue_requires_four_literal_cross_anchors(self):
        report = valid_report("revise", issue_count=1)
        ledger = valid_ledger(report, publication_status="revise")
        bind_first_issue_to_reader(ledger)
        self.assertTrue(run_validation(ledger, report)["ok"])

        unrelated = report.replace(
            "说明数字口径", "核验园区食堂菜单"
        ).replace(
            "第一段原文“2025年出租率为80%”", "园区食堂本周菜单"
        ).replace(
            "影响判断", "看看吃得好不好"
        ).replace(
            "补充统计范围", "补充本周菜谱"
        )
        ledger["report_sha256"] = digest(unrelated)
        result = run_validation(ledger, unrelated)
        self.assertIn("ISSUE_READER_LOCATION_BINDING_INVALID", result["error_codes"])
        self.assertIn("ISSUE_READER_PROBLEM_BINDING_INVALID", result["error_codes"])
        self.assertIn("ISSUE_READER_REASON_BINDING_INVALID", result["error_codes"])
        self.assertIn("ISSUE_READER_ACTION_BINDING_INVALID", result["error_codes"])

    def test_reader_issue_bindings_reject_obvious_semantic_reversals(self):
        report = valid_report("revise", issue_count=1)
        report = report.replace("### 1. 说明数字口径", "### 1. 无需说明数字口径")
        report = report.replace(
            "第一段原文“2025年出租率为80%”", "无需说明2025年出租率为80%"
        )
        report = report.replace("影响判断。", "不影响判断。")
        report = report.replace("补充统计范围。", "禁止补充统计范围。")
        ledger = valid_ledger(report, publication_status="revise")
        bind_first_issue_to_reader(ledger)
        result = run_validation(ledger, report)
        for code in (
            "ISSUE_READER_LOCATION_BINDING_INVALID",
            "ISSUE_READER_PROBLEM_BINDING_INVALID",
            "ISSUE_READER_REASON_BINDING_INVALID",
            "ISSUE_READER_ACTION_BINDING_INVALID",
        ):
            self.assertIn(code, result["error_codes"])

    def test_every_reader_index_needs_a_ledger_binding(self):
        report = valid_report("revise", issue_count=2)
        ledger = valid_ledger(report, publication_status="revise")
        bind_first_issue_to_reader(ledger)
        result = run_validation(ledger, report)
        self.assertIn("READER_ISSUE_ITEM_MAPPING_INCOMPLETE", result["error_codes"])

    def test_grouped_issues_need_an_explicit_reason(self):
        report = valid_report("revise", issue_count=1)
        ledger = valid_ledger(report, publication_status="revise")
        bind_first_issue_to_reader(ledger)
        second = copy.deepcopy(ledger["issues"][0])
        second["issue_id"] = "ISS-002"
        second["risk_level"] = "R1"
        second["workflow_state"] = "candidate"
        second["verification_status"] = "pending"
        second["closure_evidence_refs"] = []
        second.pop("verification_action_refs")
        second.pop("closure_decision_ref")
        ledger["issues"].append(second)

        result = run_validation(ledger, report)
        self.assertIn("GROUPED_READER_ISSUE_REASON_MISSING", result["error_codes"])

        for issue in ledger["issues"]:
            issue["reader_grouping_reason"] = "两项都属于同一出租率统计口径问题"
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_publication_effect_cannot_be_weaker_than_release_status(self):
        report = valid_report("ready", issue_count=1)
        ledger = valid_ledger(report, publication_status="ready")
        bind_first_issue_to_reader(ledger)
        result = run_validation(ledger, report)
        self.assertIn("READY_STATUS_CONTRADICTS_PUBLICATION_EFFECT", result["error_codes"])

        report = valid_report("revise", issue_count=1)
        ledger = valid_ledger(report, publication_status="revise")
        bind_first_issue_to_reader(ledger)
        ledger["issues"][0]["publication_effect"] = "block_publication"
        result = run_validation(ledger, report)
        self.assertIn("BLOCKING_ISSUE_REQUIRES_HOLD_STATUS", result["error_codes"])

    def test_unresolved_r3_requires_block_and_hold(self):
        ledger = valid_ledger()
        report = set_active_publication_status(ledger, "revise")
        issue = ledger["issues"][0]
        issue.update(
            {
                "risk_level": "R3",
                "workflow_state": "verifying",
                "verification_status": "missing",
                "publication_effect": "revise_before_publish",
            }
        )
        result = run_validation(ledger, report)
        self.assertIn("UNRESOLVED_R3_MUST_BLOCK_PUBLICATION", result["error_codes"])

        report = set_active_publication_status(ledger, "hold")
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_new_reader_binding_fields_cannot_leak(self):
        text = valid_report("revise", issue_count=1).replace(
            "保留原句并补充时间和范围。",
            "reader_binding item_index location_excerpt problem_excerpt reason_excerpt action_excerpt",
        )
        result = validate_user_report.validate_report(text)
        self.assertIn("MACHINE_FIELD_IN_READER_LAYER", result["error_codes"])

    def test_new_viewpoint_cannot_be_supported_only_by_the_draft(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "draft_internal"
        evidence.pop("url")
        evidence["input_refs"] = ["draft:first-paragraph"]
        ledger["claim_register"][0]["claim_type"] = "judgment"
        ledger["claim_register"][0]["requires_external_evidence"] = False
        ledger["issues"][0]["requires_external_evidence"] = False
        result = run_validation(ledger)
        self.assertIn(
            "NEW_VIEWPOINT_MATCHED_EXTERNAL_EVIDENCE_MISSING", result["error_codes"]
        )

    def test_supporting_external_claim_needs_evidence_or_an_active_plan(self):
        ledger = valid_ledger()
        supporting = copy.deepcopy(ledger["claim_register"][0])
        supporting.update(
            {
                "claim_id": "CLM-002",
                "claim": "出租率是文章中的辅助事实",
                "importance": "supporting",
            }
        )
        supporting.pop("evidence_status")
        supporting.pop("evidence_refs")
        ledger["claim_register"].append(supporting)

        result = run_validation(ledger)
        self.assertIn(
            "SUPPORTING_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN",
            result["error_codes"],
        )
        self.assertIn("EXTERNAL_CLAIM_UNRESOLVED_WHILE_READY", result["error_codes"])
        self.assertIn("UNRESOLVED_CLAIM_WITHOUT_ACTIVE_ISSUE", result["error_codes"])

        supporting["unresolved_reason"] = "没有取得支持该辅助事实的原始披露"
        supporting["next_action"] = "取得原始披露后重新核对该辅助事实"
        ledger["issues"][0]["claim_refs"].append("CLM-002")
        report = set_active_publication_status(ledger, "revise")
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"], result["error_codes"])
        self.assertEqual(result["unresolved_external_claim_count"], 1)

    def test_calculation_inputs_must_influence_the_result(self):
        for expression in (
            "source_value * 0 + 80",
            "source_value - source_value + 80",
            "source_value ** 0 * 80",
        ):
            with self.subTest(expression=expression):
                result = run_validation(derived_calculation_ledger(expression))
                self.assertIn(
                    "CALCULATION_OPERAND_NOT_INFLUENTIAL", result["error_codes"]
                )

    def test_calculation_rejects_float_literals_and_oversized_scalars(self):
        for expression in ("source_value + 0.1", "source_value + 1e-1"):
            with self.subTest(expression=expression):
                result = run_validation(
                    derived_calculation_ledger(expression, source_value="79.9")
                )
                self.assertIn("CALCULATION_EXPRESSION_INVALID", result["error_codes"])

        result = run_validation(
            derived_calculation_ledger("source_value", source_value="9" * 300)
        )
        self.assertIn("CALCULATION_OPERAND_VALUE_INVALID", result["error_codes"])

        for extreme in ("1e1000000", "-1e1000000"):
            with self.subTest(extreme=extreme):
                result = run_validation(
                    derived_calculation_ledger(
                        "source_value",
                        calculation_result=extreme,
                        source_value=extreme,
                    )
                )
                self.assertFalse(result["ok"])
                self.assertIn("CALCULATION_OPERAND_VALUE_INVALID", result["error_codes"])

    def test_derived_claim_result_is_bound_to_the_draft_number(self):
        ledger = derived_calculation_ledger(
            "source_value + 1", calculation_result="2%", source_value=1
        )
        result = run_validation(ledger)
        self.assertIn("DERIVED_CLAIM_RESULT_MISMATCH", result["error_codes"])

        ledger = derived_calculation_ledger("source_value", source_value=80)
        ledger["claim_register"][0]["derived_result"] = "2%"
        result = run_validation(ledger)
        self.assertIn("DERIVED_CLAIM_RESULT_MISMATCH", result["error_codes"])
        self.assertIn("DERIVED_CLAIM_RESULT_NOT_ANCHORED", result["error_codes"])

        for expression, result_value, source_value in (
            ("source_value * 2", "8", 4),
            ("source_value", "80", 80),
        ):
            with self.subTest(result_value=result_value):
                ledger = derived_calculation_ledger(
                    expression,
                    calculation_result=result_value,
                    source_value=source_value,
                )
                ledger["claim_register"][0]["derived_result"] = result_value
                result = run_validation(ledger)
                self.assertIn(
                    "DERIVED_CLAIM_RESULT_NOT_ANCHORED", result["error_codes"]
                )

    def test_derived_number_preserves_fullwidth_percent_unit(self):
        draft = DRAFT.replace("80%", "80％")
        ledger = derived_calculation_ledger(
            "source_value * 2", calculation_result="80", source_value=40
        )
        ledger["draft_sha256"] = digest(draft)
        ledger["claim_register"][0]["source_anchor"] = "2025年出租率为80％"
        ledger["claim_register"][0]["claim"] = "甲园区2025年出租率为80％"
        ledger["claim_register"][0]["derived_result"] = "80"
        ledger["issues"][0]["source_anchor"] = "2025年出租率为80％"
        result = validate_review_bundle.validate_bundle(
            draft.encode("utf-8"),
            ledger,
            valid_report().encode("utf-8"),
            supplied_input_bytes={},
        )
        self.assertIn("DERIVED_CLAIM_RESULT_NOT_ANCHORED", result["error_codes"])

        ledger["evidence_records"][0]["calculation_result"] = "80%"
        ledger["claim_register"][0]["derived_result"] = "80%"
        result = validate_review_bundle.validate_bundle(
            draft.encode("utf-8"),
            ledger,
            valid_report().encode("utf-8"),
            supplied_input_bytes={},
        )
        self.assertTrue(result["ok"], result["error_codes"])

    def test_calculation_lineage_depth_and_record_count_are_bounded(self):
        ledger = derived_calculation_ledger("source_value", source_value="80%")
        calculation_template = copy.deepcopy(ledger["evidence_records"][0])
        for index in range(2, 68):
            evidence_id = f"EV-{index:03d}"
            next_id = f"EV-{index + 1:03d}"
            calculation = copy.deepcopy(calculation_template)
            calculation["evidence_id"] = evidence_id
            calculation["input_evidence_refs"] = [next_id]
            calculation["calculation_operands"] = [
                {"name": "value", "value": "80%", "evidence_ref": next_id}
            ]
            calculation["calculation_expression"] = "value"
            calculation["calculation_result"] = "80%"
            if index == 2:
                ledger["evidence_records"][1] = calculation
            else:
                ledger["evidence_records"].append(calculation)
        terminal = copy.deepcopy(ledger["evidence_records"][1])
        terminal.update(
            {
                "evidence_id": "EV-068",
                "acquisition": "opened_page",
                "url": "https://example.gov.cn/terminal-source",
                "locator": "合成链末端输入值",
            }
        )
        for field in (
            "input_refs",
            "input_evidence_refs",
            "calculation_expression",
            "calculation_result",
            "calculation_operands",
        ):
            terminal.pop(field, None)
        ledger["evidence_records"].append(terminal)
        ledger["web_trace"]["opened_pages"].append(
            "https://example.gov.cn/terminal-source"
        )
        result = run_validation(ledger)
        self.assertIn("CALCULATION_LINEAGE_TOO_DEEP", result["error_codes"])

        oversized = valid_ledger()
        template = copy.deepcopy(oversized["evidence_records"][0])
        for index in range(2, validate_review_bundle.MAX_EVIDENCE_RECORDS + 2):
            record = copy.deepcopy(template)
            record["evidence_id"] = f"EV-{index:03d}"
            oversized["evidence_records"].append(record)
        result = run_validation(oversized)
        self.assertIn("TOO_MANY_EVIDENCE_RECORDS", result["error_codes"])

    def test_external_lineage_keeps_the_same_fit_target_recursively(self):
        ledger = derived_calculation_ledger("source_value", source_value=80)
        ledger["evidence_records"][1]["fit_target_refs"] = ["viewpoint:VP-001"]
        result = run_validation(ledger)
        self.assertIn("EXTERNAL_CLAIM_EVIDENCE_SCOPE_FIT_INSUFFICIENT", result["error_codes"])

    def test_every_calculation_input_branch_keeps_target_and_material_fit(self):
        for failure_mode in ("wrong_target", "scope_not_fit"):
            with self.subTest(failure_mode=failure_mode):
                ledger = derived_calculation_ledger("source_value", source_value=4)
                calculation = ledger["evidence_records"][0]
                calculation["calculation_expression"] = "left * 10 + right * 10"
                calculation["input_evidence_refs"] = ["EV-002", "EV-003"]
                calculation["calculation_operands"] = [
                    {"name": "left", "value": 4, "evidence_ref": "EV-002"},
                    {"name": "right", "value": 4, "evidence_ref": "EV-003"},
                ]
                second_input = copy.deepcopy(ledger["evidence_records"][1])
                second_input["evidence_id"] = "EV-003"
                second_input["locator"] = "合成页面中的第二个输入值"
                if failure_mode == "wrong_target":
                    second_input["fit_target_refs"] = ["viewpoint:VP-001"]
                else:
                    second_input["fit"]["scope"] = False
                ledger["evidence_records"].append(second_input)

                result = run_validation(ledger)
                expected = (
                    "EXTERNAL_CLAIM_EVIDENCE_SCOPE_FIT_INSUFFICIENT"
                    if failure_mode == "wrong_target"
                    else "EVIDENCE_FIT_INVALID"
                )
                self.assertIn(expected, result["error_codes"])

    def test_unhashable_issue_id_fails_closed(self):
        for malformed_id in ([], {}, {"bad"}, ["ISS-001"]):
            with self.subTest(malformed_id=repr(malformed_id)):
                ledger = valid_ledger()
                ledger["issues"][0]["issue_id"] = malformed_id
                result = validate_review_bundle.validate_bundle(
                    DRAFT.encode("utf-8"),
                    ledger,
                    valid_report().encode("utf-8"),
                    supplied_input_bytes={},
                )
                self.assertFalse(result["ok"])
                self.assertIn("MISSING_ISSUE_ID", result["error_codes"])

    def test_claim_evidence_fit_cannot_be_reused_for_another_claim(self):
        ledger = valid_ledger()
        second = copy.deepcopy(ledger["claim_register"][0])
        second["claim_id"] = "CLM-002"
        second["claim"] = "同一证据被声明支持另一条主张"
        second["importance"] = "important"
        ledger["claim_register"].append(second)
        result = run_validation(ledger)
        self.assertIn("CLAIM_EVIDENCE_FIT_TARGET_MISSING", result["error_codes"])

    def test_evidence_fit_targets_cannot_be_empty(self):
        ledger = valid_ledger()
        ledger["evidence_records"][0]["fit_target_refs"] = []
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_FIT_TARGET_REFS_EMPTY", result["error_codes"])

    def test_closure_evidence_covers_every_linked_claim(self):
        ledger = valid_ledger()
        second = copy.deepcopy(ledger["claim_register"][0])
        second.update(
            {
                "claim_id": "CLM-002",
                "claim": "同一问题还连接另一项园区事实",
                "importance": "supporting",
                "requires_external_evidence": False,
            }
        )
        ledger["claim_register"].append(second)
        ledger["issues"][0]["claim_refs"].append("CLM-002")
        result = run_validation(ledger)
        self.assertIn("CLOSURE_CLAIM_NOT_COVERED", result["error_codes"])

    def test_derived_issue_requires_every_linked_claim_to_be_derived(self):
        ledger = derived_calculation_ledger("source_value", source_value=80)
        second = copy.deepcopy(ledger["claim_register"][0])
        second.update(
            {
                "claim_id": "CLM-002",
                "claim": "同一问题还连接一项直接支持的事实",
                "importance": "supporting",
                "requires_external_evidence": True,
                "evidence_status": "supported",
                "evidence_refs": ["EV-001"],
            }
        )
        second.pop("derived_result", None)
        second.pop("calculation_evidence_ref", None)
        ledger["claim_register"].append(second)
        ledger["issues"][0]["claim_refs"].append("CLM-002")
        ledger["evidence_records"][0]["fit_target_refs"].append("claim:CLM-002")
        result = run_validation(ledger)
        self.assertIn(
            "CLOSED_DERIVED_ISSUE_CLAIM_BINDING_MISSING", result["error_codes"]
        )

    def test_viewpoint_fields_are_bound_to_the_reader_item(self):
        ledger = valid_ledger()
        ledger["new_viewpoints"][0].update(
            {
                "actor": "地方政府",
                "decision": "是否为重资产项目单独供地",
                "mechanism": "项目投资强度可能改变供地谈判",
                "boundary": "只适用于必须独立供地的项目",
                "falsifier": "若标准厂房可直接承载则不成立",
            }
        )
        result = run_validation(ledger)
        for code in (
            "NEW_VIEWPOINT_READER_ACTOR_BINDING_INVALID",
            "NEW_VIEWPOINT_READER_DECISION_BINDING_INVALID",
            "NEW_VIEWPOINT_READER_MECHANISM_BINDING_INVALID",
            "NEW_VIEWPOINT_READER_BOUNDARY_BINDING_INVALID",
            "NEW_VIEWPOINT_READER_FALSIFIER_BINDING_INVALID",
        ):
            self.assertIn(code, result["error_codes"])

    def test_viewpoint_binding_rejects_negation_and_draft_copying(self):
        report = valid_report().replace(
            "出租率口径可能掩盖短租与免租期差异",
            "并不存在出租率口径可能掩盖短租与免租期差异",
        )
        ledger = valid_ledger(report)
        result = run_validation(ledger, report)
        self.assertIn(
            "NEW_VIEWPOINT_READER_MECHANISM_BINDING_INVALID", result["error_codes"]
        )

        copied_mechanism = DRAFT.strip()
        report = valid_report().replace(
            "出租率口径可能掩盖短租与免租期差异", copied_mechanism
        )
        ledger = valid_ledger(report)
        ledger["new_viewpoints"][0]["mechanism"] = copied_mechanism
        result = run_validation(ledger, report)
        self.assertIn("NEW_VIEWPOINT_MECHANISM_COPIES_DRAFT", result["error_codes"])

    def test_discovery_hits_are_real_typed_trace_objects(self):
        ledger = valid_ledger()
        ledger["discovery_hits"] = [
            {
                "hit_id": "HIT-001",
                "issue_id": "ISS-001",
                "channel": "web search",
                "candidate_locator": "https://example.gov.cn/candidate",
                "captured_at": "2026-09-14T08:45:00+08:00",
                "role": "候选来源",
                "promotion_status": "candidate",
            }
        ]
        self.assertTrue(run_validation(ledger)["ok"])

        ledger["discovery_hits"] = [7, {}, {"hit_id": "HIT-001", "issue_id": "missing"}]
        result = run_validation(ledger)
        self.assertIn("DISCOVERY_HIT_NOT_OBJECT", result["error_codes"])
        self.assertIn("MISSING_DISCOVERY_HIT_ID", result["error_codes"])
        self.assertIn("UNKNOWN_DISCOVERY_HIT_ISSUE_REF", result["error_codes"])
        self.assertIn("DISCOVERY_HIT_CAPTURED_AT_INVALID", result["error_codes"])

    def test_malformed_types_and_urls_fail_closed_without_exceptions(self):
        mutations = (
            ("publication_status", lambda x: x.__setitem__("publication_status", [])),
            ("claim_type", lambda x: x["claim_register"][0].__setitem__("claim_type", [])),
            ("importance", lambda x: x["claim_register"][0].__setitem__("importance", [])),
            ("evidence_status", lambda x: x["claim_register"][0].__setitem__("evidence_status", [])),
            ("risk_level", lambda x: x["issues"][0].__setitem__("risk_level", [])),
            ("workflow_state", lambda x: x["issues"][0].__setitem__("workflow_state", [])),
            ("verification_status", lambda x: x["issues"][0].__setitem__("verification_status", [])),
            ("publication_effect", lambda x: x["issues"][0].__setitem__("publication_effect", [])),
            ("disposition", lambda x: x["closure_decisions"][0].__setitem__("disposition", [])),
            ("action_evidence_refs", lambda x: x["verification_actions"][0].__setitem__("evidence_refs", 7)),
        )
        report = valid_report()
        for label, mutate in mutations:
            with self.subTest(label=label):
                ledger = valid_ledger(report)
                mutate(ledger)
                result = validate_review_bundle.validate_bundle(
                    DRAFT.encode("utf-8"), ledger, report.encode("utf-8")
                )
                self.assertFalse(result["ok"])

        ledger = valid_ledger()
        for malformed_url in (
            "https://[bad",
            "https:/" + "/.",
            "https:/" + "/..",
            "https://-",
            "https://exa mple.com/x",
            "https://%zz/x",
            "https:/" + "/user@:443/x",
            "https:/" + "/:443/x",
            "https://example.com:99999/x",
        ):
            with self.subTest(malformed_url=malformed_url):
                ledger = valid_ledger()
                ledger["web_trace"]["opened_pages"] = [malformed_url]
                ledger["evidence_records"][0]["url"] = malformed_url
                result = run_validation(ledger)
                self.assertIn("OPENED_PAGE_URL_INVALID", result["error_codes"])
                self.assertIn("OPENED_PAGE_EVIDENCE_URL_INVALID", result["error_codes"])

    def test_closure_shells_timeline_and_self_reported_edits_are_rejected(self):
        ledger = valid_ledger()
        ledger["verification_actions"][0].update(
            {"action_type": "....", "result": "....", "performed_by": "..."}
        )
        ledger["closure_decisions"][0].update(
            {
                "resolution_action": "....",
                "reviewer_role": "...",
                "residual_limitations": "....",
                "reopen_conditions": ".",
            }
        )
        result = run_validation(ledger)
        self.assertIn("VERIFICATION_ACTION_ACTION_TYPE_MISSING", result["error_codes"])
        self.assertIn("CLOSURE_DECISION_REOPEN_CONDITIONS_INVALID", result["error_codes"])

        ledger = valid_ledger()
        ledger["issues"][0]["problem"] = "____"
        ledger["verification_actions"][0]["result"] = "____"
        result = run_validation(ledger)
        self.assertIn("ISSUE_PROBLEM_MISSING", result["error_codes"])
        self.assertIn("VERIFICATION_ACTION_RESULT_MISSING", result["error_codes"])

        ledger = valid_ledger()
        ledger["verification_actions"][0]["performed_at"] = "2030-01-01T00:00:00+08:00"
        ledger["closure_decisions"][0]["reviewed_at"] = "2020-01-01T00:00:00+08:00"
        result = run_validation(ledger)
        self.assertIn("VERIFICATION_ACTION_AFTER_CLOSURE_DECISION", result["error_codes"])

        for disposition in ("revised", "deleted", "narrowed"):
            with self.subTest(disposition=disposition):
                ledger = valid_ledger()
                ledger["closure_decisions"][0]["disposition"] = disposition
                result = run_validation(ledger)
                self.assertIn(
                    "CLOSED_ISSUE_SELF_REPORTED_EDIT_UNVERIFIABLE",
                    result["error_codes"],
                )

    def test_release_status_requires_a_matching_active_issue(self):
        for status, code in (
            ("revise", "REVISE_STATUS_WITHOUT_ACTIVE_REVISION_ISSUE"),
            ("hold", "HOLD_STATUS_WITHOUT_ACTIVE_BLOCKING_ISSUE"),
        ):
            with self.subTest(status=status):
                report = valid_report(status)
                ledger = valid_ledger(report, publication_status=status)
                result = run_validation(ledger, report)
                self.assertIn(code, result["error_codes"])

        report = valid_report("hold", issue_count=1)
        ledger = valid_ledger(report, publication_status="hold")
        bind_first_issue_to_reader(ledger)
        ledger["issues"][0]["workflow_state"] = "closed"
        ledger["issues"][0]["publication_effect"] = "block_publication"
        result = run_validation(ledger, report)
        self.assertIn("CLOSED_ISSUE_PUBLICATION_EFFECT_INVALID", result["error_codes"])
        self.assertIn("HOLD_STATUS_WITHOUT_ACTIVE_BLOCKING_ISSUE", result["error_codes"])

    def test_short_source_anchors_are_not_a_valid_binding(self):
        ledger = valid_ledger()
        ledger["claim_register"][0]["source_anchor"] = "年"
        ledger["issues"][0]["source_anchor"] = "年"
        result = run_validation(ledger)
        self.assertIn("CLAIM_SOURCE_ANCHOR_MISSING", result["error_codes"])
        self.assertIn("SOURCE_ANCHOR_MISSING", result["error_codes"])

    def test_cli_rejects_duplicate_json_keys_at_any_depth(self):
        report = valid_report()
        for needle, replacement in (
            (
                '"publication_status": "ready"',
                '"publication_status": "hold", "publication_status": "ready"',
            ),
            (
                '"evidence_status": "supported"',
                '"evidence_status": "missing", "evidence_status": "supported"',
            ),
        ):
            with self.subTest(needle=needle):
                raw_ledger = json.dumps(valid_ledger(report), ensure_ascii=False)
                self.assertIn(needle, raw_ledger)
                raw_ledger = raw_ledger.replace(needle, replacement, 1)
                with tempfile.TemporaryDirectory() as temp_dir:
                    temp = Path(temp_dir)
                    draft_path = temp / "draft.md"
                    report_path = temp / "report.md"
                    ledger_path = temp / "ledger.json"
                    draft_path.write_text(DRAFT, encoding="utf-8")
                    report_path.write_text(report, encoding="utf-8")
                    ledger_path.write_text(raw_ledger, encoding="utf-8")
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(ROOT / "scripts" / "validate_review_bundle.py"),
                            str(draft_path),
                            str(ledger_path),
                            str(report_path),
                        ],
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                self.assertEqual(completed.returncode, 2, completed.stdout)
                payload = json.loads(completed.stdout)
                self.assertEqual(payload["error_codes"], ["LEDGER_DUPLICATE_KEY"])


if __name__ == "__main__":
    unittest.main()
