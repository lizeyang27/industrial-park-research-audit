from __future__ import annotations

import hashlib
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


DRAFT = "甲园区2025年出租率为80%，原文据此建议企业核对厂房适配条件。\n"


REPORT_STATUS_TEXT = {
    "ready": "可以发布",
    "revise": "修改后发布",
    "hold": "暂缓发布",
}


def valid_report(publication_status: str = "ready") -> str:
    bodies = {
        "结论": f"{REPORT_STATUS_TEXT[publication_status]}。核心数字已经核到原始页面。",
        "这篇文章已经做好的地方": "- 决策对象清楚。",
        "发布前需要处理": "### 1. 说明数字口径\n- 原文位置：第一段。\n- 为什么重要：影响判断。\n- 建议处理：补充统计范围。",
        "建议怎么改": "保留原句并补充时间和范围。",
        "可继续研究的新观点": "证据足够时可研究租约结构；本次不额外扩写。",
        "如果还要继续核验": "可取得项目租约或请行业专家判断长期机制。",
        "本次审阅没有验证什么": "没有取得非公开租约和企业经营资料。",
    }
    sections = []
    for name in validate_user_report.REQUIRED_SECTIONS:
        sections.append(f"## {name}\n{bodies.get(name, '本节暂无补充。')}")
    return "# 审阅报告\n\n" + "\n\n".join(sections) + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def runtime_receipt() -> dict[str, object]:
    paths = sorted(validate_review_bundle.REQUIRED_RUNTIME_RESOURCES)
    resources = []
    for path in paths:
        resource_path = ROOT / Path(*path.split("/"))
        resources.append(
            {
                "path": path,
                "sha256": hashlib.sha256(resource_path.read_bytes()).hexdigest(),
            }
        )
    return {
        "skill_version": (ROOT / "VERSION").read_text(encoding="utf-8").strip(),
        "commit": "0123456789abcdef0123456789abcdef01234567",
        "mode": "public_full_evidence",
        "precommit_possible": False,
        "loaded_resources": resources,
    }


def valid_ledger(
    report: str | None = None, publication_status: str = "ready"
) -> dict[str, object]:
    report = valid_report(publication_status) if report is None else report
    return {
        "draft_sha256": digest(DRAFT),
        "report_sha256": digest(report),
        "runtime": runtime_receipt(),
        "framework_timing": {
            "status": "retrospective_framework_first_pass",
            "draft_seen_before_framework": True,
            "causal_claim_allowed": False,
            "framework_artifact_ref": None,
        },
        "web_trace": {
            "network_available": True,
            "search_count": 1,
            "opened_pages": ["https://example.gov.cn/not-a-real-case"],
            "stop_reason": "合成核心主张已达到发布判断",
        },
        "publication_status": publication_status,
        "claim_register": [
            {
                "claim_id": "CLM-001",
                "source_anchor": "2025年出租率为80%",
                "importance": "core",
                "requires_external_evidence": True,
                "evidence_status": "supported",
                "evidence_refs": ["EV-001"],
            }
        ],
        "discovery_hits": [],
        "verification_actions": [
            {
                "action_id": "ACT-001",
                "issue_refs": ["ISS-001"],
                "evidence_refs": ["EV-001"],
            }
        ],
        "closure_decisions": [
            {
                "closure_id": "CLS-001",
                "issue_refs": ["ISS-001"],
                "evidence_refs": ["EV-001"],
            }
        ],
        "evidence_records": [
            {
                "evidence_id": "EV-001",
                "acquisition": "opened_page",
                "url": "https://example.gov.cn/not-a-real-case",
                "locator": "合成页面中的统计表第一行",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            }
        ],
        "issues": [
            {
                "issue_id": "ISS-001",
                "risk_level": "R3",
                "source_anchor": "2025年出租率为80%",
                "workflow_state": "closed",
                "verification_status": "supported",
                "verification_level": "V2",
                "closure_evidence_refs": ["EV-001"],
                "verification_action_refs": ["ACT-001"],
                "closure_decision_ref": "CLS-001",
            }
        ],
        "new_viewpoints": [
            {
                "actor": "拟入园企业",
                "decision": "是否在签约前追加租约稳定性核验",
                "mechanism": "出租率口径可能掩盖短租与免租期差异",
                "evidence_refs": ["EV-001"],
                "boundary": "只适用于租约结构会影响持续经营判断的项目",
                "falsifier": "若逐份租约显示期限和付租状态一致，该机制不成立",
            }
        ],
    }


def run_validation(ledger: dict[str, object], report: str | None = None) -> dict[str, object]:
    if report is None:
        status = ledger.get("publication_status", "ready")
        report = valid_report(status if status in REPORT_STATUS_TEXT else "ready")
    return validate_review_bundle.validate_bundle(
        DRAFT.encode("utf-8"), ledger, report.encode("utf-8")
    )


def set_publication_status(ledger: dict[str, object], status: str) -> str:
    report = valid_report(status)
    ledger["publication_status"] = status
    ledger["report_sha256"] = digest(report)
    return report


class ReviewBundleTests(unittest.TestCase):
    def test_valid_bundle_passes(self):
        result = run_validation(valid_ledger())
        self.assertTrue(result["ok"])
        self.assertEqual(result["error_codes"], [])
        self.assertEqual(result["unresolved_high_risk_count"], 0)

    def test_draft_and_report_hashes_bind_bundle(self):
        ledger = valid_ledger()
        ledger["draft_sha256"] = "0" * 64
        ledger["report_sha256"] = "f" * 64
        result = run_validation(ledger)
        self.assertIn("DRAFT_SHA256_MISMATCH", result["error_codes"])
        self.assertIn("REPORT_SHA256_MISMATCH", result["error_codes"])

    def test_runtime_receipt_prevents_silent_fallback_to_basic_mode(self):
        ledger = valid_ledger()
        ledger["runtime"]["mode"] = "basic"
        ledger["runtime"]["loaded_resources"] = ledger["runtime"]["loaded_resources"][:-1]
        result = run_validation(ledger)
        self.assertIn("FULL_EVIDENCE_MODE_NOT_RECORDED", result["error_codes"])
        self.assertIn("REQUIRED_RUNTIME_RESOURCE_NOT_LOADED", result["error_codes"])

    def test_runtime_resource_hash_must_match_installed_file(self):
        ledger = valid_ledger()
        ledger["runtime"]["loaded_resources"][0]["sha256"] = "0" * 64
        result = run_validation(ledger)
        self.assertIn("LOADED_RESOURCE_SHA256_MISMATCH", result["error_codes"])
        self.assertIn("REQUIRED_RUNTIME_RESOURCE_NOT_LOADED", result["error_codes"])

    def test_runtime_requires_every_full_evidence_resource(self):
        self.assertEqual(len(validate_review_bundle.REQUIRED_RUNTIME_RESOURCES), 9)
        for required_path in sorted(validate_review_bundle.REQUIRED_RUNTIME_RESOURCES):
            with self.subTest(required_path=required_path):
                ledger = valid_ledger()
                ledger["runtime"]["loaded_resources"] = [
                    item
                    for item in ledger["runtime"]["loaded_resources"]
                    if item["path"] != required_path
                ]
                result = run_validation(ledger)
                self.assertIn("REQUIRED_RUNTIME_RESOURCE_NOT_LOADED", result["error_codes"])

    def test_runtime_allows_unavailable_commit_with_reason_and_source_ref(self):
        ledger = valid_ledger()
        runtime = ledger["runtime"]
        runtime["commit"] = None
        runtime["commit_unavailable_reason"] = "安装副本不含 Git 元数据"
        runtime["source_ref"] = "https://github.com/example/project/releases/tag/v1.2.0"
        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_runtime_rejects_unexplained_null_or_nonhex_commit(self):
        ledger = valid_ledger()
        ledger["runtime"]["commit"] = None
        result = run_validation(ledger)
        self.assertIn("RUNTIME_COMMIT_UNAVAILABLE_REASON_MISSING", result["error_codes"])
        self.assertIn("RUNTIME_SOURCE_REF_MISSING", result["error_codes"])

        ledger = valid_ledger()
        ledger["runtime"]["commit"] = "main"
        ledger["runtime"]["commit_unavailable_reason"] = "有解释也不能放宽格式"
        ledger["runtime"]["source_ref"] = "v1.2.0"
        result = run_validation(ledger)
        self.assertIn("RUNTIME_COMMIT_INVALID", result["error_codes"])

    def test_runtime_records_whether_strict_precommit_was_possible(self):
        ledger = valid_ledger()
        ledger["runtime"]["precommit_possible"] = "false"
        result = run_validation(ledger)
        self.assertIn("PRECOMMIT_POSSIBLE_NOT_BOOLEAN", result["error_codes"])

    def test_framework_timing_accepts_consistent_strict_precommit(self):
        ledger = valid_ledger()
        ledger["runtime"]["precommit_possible"] = True
        ledger["framework_timing"] = {
            "status": "strict_precommit",
            "draft_seen_before_framework": False,
            "causal_claim_allowed": True,
            "framework_artifact_ref": "FW-001",
        }
        result = run_validation(ledger)
        self.assertTrue(result["ok"])

    def test_framework_timing_must_match_runtime_and_causal_flags(self):
        ledger = valid_ledger()
        ledger["framework_timing"] = {
            "status": "strict_precommit",
            "draft_seen_before_framework": False,
            "causal_claim_allowed": True,
            "framework_artifact_ref": "FW-001",
        }
        result = run_validation(ledger)
        self.assertIn("RETROSPECTIVE_FRAMEWORK_FLAGS_INVALID", result["error_codes"])

        ledger = valid_ledger()
        ledger["runtime"]["precommit_possible"] = True
        ledger["framework_timing"]["framework_artifact_ref"] = None
        result = run_validation(ledger)
        self.assertIn("STRICT_PRECOMMIT_FLAGS_INVALID", result["error_codes"])
        self.assertIn("STRICT_PRECOMMIT_ARTIFACT_REF_MISSING", result["error_codes"])

    def test_framework_timing_is_required_and_typed(self):
        ledger = valid_ledger()
        del ledger["framework_timing"]
        result = run_validation(ledger)
        self.assertIn("FRAMEWORK_TIMING_MISSING", result["error_codes"])

        ledger = valid_ledger()
        ledger["framework_timing"]["causal_claim_allowed"] = "false"
        ledger["framework_timing"]["framework_artifact_ref"] = []
        result = run_validation(ledger)
        self.assertIn("CAUSAL_CLAIM_ALLOWED_NOT_BOOLEAN", result["error_codes"])
        self.assertIn("FRAMEWORK_ARTIFACT_REF_INVALID", result["error_codes"])

    def test_bundle_envelope_requires_all_collection_fields(self):
        ledger = valid_ledger()
        del ledger["evidence_records"]
        del ledger["issues"]
        del ledger["new_viewpoints"]
        del ledger["claim_register"]
        del ledger["discovery_hits"]
        del ledger["verification_actions"]
        del ledger["closure_decisions"]
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_RECORDS_MISSING", result["error_codes"])
        self.assertIn("ISSUES_MISSING", result["error_codes"])
        self.assertIn("NEW_VIEWPOINTS_MISSING", result["error_codes"])
        self.assertIn("CLAIM_REGISTER_MISSING", result["error_codes"])
        self.assertIn("DISCOVERY_HITS_MISSING", result["error_codes"])
        self.assertIn("VERIFICATION_ACTIONS_MISSING", result["error_codes"])
        self.assertIn("CLOSURE_DECISIONS_MISSING", result["error_codes"])

    def test_nonempty_draft_requires_claim_register(self):
        ledger = valid_ledger()
        ledger["claim_register"] = []
        result = run_validation(ledger)
        self.assertIn("NONEMPTY_DRAFT_WITHOUT_CLAIM_REGISTER", result["error_codes"])

    def test_important_external_claim_requires_evidence_or_unresolved_plan(self):
        ledger = valid_ledger()
        claim = ledger["claim_register"][0]
        claim.pop("evidence_status")
        claim.pop("evidence_refs")
        result = run_validation(ledger)
        self.assertIn("IMPORTANT_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN", result["error_codes"])

        claim["unresolved_reason"] = "原始统计表尚未取得"
        claim["next_action"] = "向发布机构索取统计表并核对口径"
        report = set_publication_status(ledger, "revise")
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"])

    def test_unresolved_material_claim_blocks_ready_but_allows_revise_or_hold(self):
        for publication_status in ("ready", "revise", "hold"):
            with self.subTest(publication_status=publication_status):
                ledger = valid_ledger()
                report = set_publication_status(ledger, publication_status)
                claim = ledger["claim_register"][0]
                claim.pop("evidence_status")
                claim.pop("evidence_refs")
                claim["unresolved_reason"] = "原始统计表尚未取得"
                claim["next_action"] = "向发布机构索取统计表并核对口径"

                result = run_validation(ledger, report)

                self.assertEqual(result["unresolved_material_claim_count"], 1)
                if publication_status == "ready":
                    self.assertFalse(result["ok"])
                    self.assertIn(
                        "MATERIAL_CLAIM_UNRESOLVED_WHILE_READY",
                        result["error_codes"],
                    )
                else:
                    self.assertTrue(result["ok"], result["error_codes"])
                    self.assertNotIn(
                        "MATERIAL_CLAIM_UNRESOLVED_WHILE_READY",
                        result["error_codes"],
                    )

    def test_claim_evidence_fields_and_references_are_validated(self):
        ledger = valid_ledger()
        claim = ledger["claim_register"][0]
        claim["evidence_status"] = "certain"
        claim["evidence_refs"] = ["EV-404"]
        result = run_validation(ledger)
        self.assertIn("CLAIM_EVIDENCE_STATUS_INVALID", result["error_codes"])
        self.assertIn("UNKNOWN_CLAIM_EVIDENCE_REF", result["error_codes"])

    def test_contradicted_is_a_valid_claim_evidence_status(self):
        ledger = valid_ledger()
        ledger["claim_register"][0]["evidence_status"] = "contradicted"
        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_claim_ids_and_anchors_are_bound_to_draft(self):
        ledger = valid_ledger()
        duplicate = dict(ledger["claim_register"][0])
        duplicate["source_anchor"] = "不存在的原文概括"
        ledger["claim_register"].append(duplicate)
        result = run_validation(ledger)
        self.assertIn("DUPLICATE_CLAIM_ID", result["error_codes"])
        self.assertIn("CLAIM_SOURCE_ANCHOR_NOT_FOUND", result["error_codes"])

    def test_web_trace_is_required_and_typed(self):
        ledger = valid_ledger()
        del ledger["web_trace"]
        result = run_validation(ledger)
        self.assertIn("WEB_TRACE_MISSING", result["error_codes"])

        ledger = valid_ledger()
        ledger["web_trace"]["network_available"] = "yes"
        ledger["web_trace"]["search_count"] = -1
        ledger["web_trace"]["stop_reason"] = ""
        result = run_validation(ledger)
        self.assertIn("NETWORK_AVAILABLE_NOT_BOOLEAN", result["error_codes"])
        self.assertIn("SEARCH_COUNT_INVALID", result["error_codes"])
        self.assertIn("WEB_STOP_REASON_MISSING", result["error_codes"])

    def test_opened_page_urls_must_be_unique_http_urls(self):
        ledger = valid_ledger()
        ledger["web_trace"]["opened_pages"] = [
            "https://example.gov.cn/not-a-real-case",
            "https://example.gov.cn/not-a-real-case",
            "file:///private/source",
        ]
        result = run_validation(ledger)
        self.assertIn("DUPLICATE_OPENED_PAGE_URL", result["error_codes"])
        self.assertIn("OPENED_PAGE_URL_INVALID", result["error_codes"])

    def test_online_closure_and_viewpoint_sources_must_have_been_opened(self):
        ledger = valid_ledger()
        ledger["web_trace"]["opened_pages"] = ["https://example.gov.cn/a-different-page"]
        result = run_validation(ledger)
        self.assertIn("OPENED_PAGE_EVIDENCE_NOT_IN_WEB_TRACE", result["error_codes"])
        self.assertIn("CLOSURE_EVIDENCE_NOT_ADMISSIBLE", result["error_codes"])
        self.assertIn("NEW_VIEWPOINT_EVIDENCE_NOT_ADMISSIBLE", result["error_codes"])

    def test_offline_trace_requires_skip_reason_and_cannot_close_external_issue(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        ledger["web_trace"] = {
            "network_available": False,
            "search_count": 0,
            "opened_pages": [],
            "stop_reason": "网络不可用",
            "skip_reason": "宿主未提供网络工具",
        }
        result = run_validation(ledger, report)
        self.assertIn("EXTERNAL_ISSUE_CLOSED_OFFLINE", result["error_codes"])
        self.assertIn("NEW_VIEWPOINT_EXTERNAL_EVIDENCE_OFFLINE", result["error_codes"])

    def test_offline_run_can_leave_external_issue_open(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        ledger["web_trace"] = {
            "network_available": False,
            "search_count": 0,
            "opened_pages": [],
            "stop_reason": "网络不可用，保留问题",
            "skip_reason": "宿主未提供网络工具",
        }
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        ledger["new_viewpoints"] = []
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["SUPPLIED-001"]
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"])
        self.assertEqual(result["unresolved_high_risk_count"], 1)

    def test_offline_trace_rejects_opened_pages_and_missing_skip_reason(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        ledger["issues"] = []
        ledger["new_viewpoints"] = []
        ledger["web_trace"] = {
            "network_available": False,
            "search_count": 0,
            "opened_pages": ["https://example.gov.cn/not-a-real-case"],
            "stop_reason": "网络不可用",
        }
        result = run_validation(ledger, report)
        self.assertIn("OFFLINE_OPENED_PAGES_NOT_EMPTY", result["error_codes"])
        self.assertIn("OFFLINE_SKIP_REASON_MISSING", result["error_codes"])

    def test_duplicate_issue_id_and_nonexact_anchor_fail(self):
        ledger = valid_ledger()
        first = ledger["issues"][0]
        duplicate = dict(first)
        duplicate["source_anchor"] = "出租率大约八成"
        ledger["issues"].append(duplicate)
        result = run_validation(ledger)
        self.assertIn("DUPLICATE_ISSUE_ID", result["error_codes"])
        self.assertIn("SOURCE_ANCHOR_NOT_FOUND", result["error_codes"])

    def test_issue_workflow_and_verification_status_are_required_and_legal(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue.pop("workflow_state")
        issue["verification_status"] = "certain"
        result = run_validation(ledger)
        self.assertIn("INVALID_WORKFLOW_STATE", result["error_codes"])
        self.assertIn("INVALID_VERIFICATION_STATUS", result["error_codes"])

    def test_closed_issue_requires_action_and_closure_decision_cross_refs(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue.pop("verification_action_refs")
        issue.pop("closure_decision_ref")
        result = run_validation(ledger)
        self.assertIn("CLOSED_ISSUE_WITHOUT_VERIFICATION_ACTION", result["error_codes"])
        self.assertIn("CLOSED_ISSUE_WITHOUT_CLOSURE_DECISION", result["error_codes"])

    def test_trace_objects_require_unique_ids_and_valid_issue_evidence_refs(self):
        ledger = valid_ledger()
        ledger["verification_actions"].append(dict(ledger["verification_actions"][0]))
        ledger["closure_decisions"][0]["issue_refs"] = ["ISS-404"]
        ledger["closure_decisions"][0]["evidence_refs"] = ["EV-404"]
        result = run_validation(ledger)
        self.assertIn("DUPLICATE_VERIFICATION_ACTION_ID", result["error_codes"])
        self.assertIn("UNKNOWN_CLOSURE_DECISION_ISSUE_REF", result["error_codes"])
        self.assertIn("UNKNOWN_CLOSURE_DECISION_EVIDENCE_REF", result["error_codes"])

    def test_issue_cross_refs_must_point_back_to_same_issue_and_closure_evidence(self):
        ledger = valid_ledger()
        ledger["verification_actions"][0]["issue_refs"] = ["ISS-OTHER"]
        ledger["verification_actions"][0]["evidence_refs"] = ["EV-OTHER"]
        result = run_validation(ledger)
        self.assertIn("UNKNOWN_VERIFICATION_ACTION_ISSUE_REF", result["error_codes"])
        self.assertIn("UNKNOWN_VERIFICATION_ACTION_EVIDENCE_REF", result["error_codes"])
        self.assertIn("VERIFICATION_ACTION_DOES_NOT_REFERENCE_ISSUE", result["error_codes"])
        self.assertIn("VERIFICATION_ACTION_EVIDENCE_NOT_IN_CLOSURE", result["error_codes"])

    def test_search_summary_cannot_close_verified_issue(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "search_summary"
        result = run_validation(ledger)
        self.assertIn("SEARCH_SUMMARY_USED_FOR_CLOSURE", result["error_codes"])
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_opened_page_closure_requires_url_locator_and_full_fit(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["url"] = "search result only"
        evidence["locator"] = ""
        evidence["fit"]["unit"] = False
        result = run_validation(ledger)
        self.assertIn("OPENED_PAGE_EVIDENCE_URL_INVALID", result["error_codes"])
        self.assertIn("EVIDENCE_LOCATOR_MISSING", result["error_codes"])
        self.assertIn("EVIDENCE_FIT_INVALID", result["error_codes"])

    def test_all_four_evidence_acquisition_types_can_close_an_issue(self):
        for acquisition in (
            "opened_page",
            "supplied_source",
            "reproducible_calculation",
            "draft_internal",
        ):
            with self.subTest(acquisition=acquisition):
                ledger = valid_ledger()
                evidence = ledger["evidence_records"][0]
                evidence["acquisition"] = acquisition
                if acquisition != "opened_page":
                    evidence.pop("url")
                    evidence["input_refs"] = ["INPUT-001"]
                result = run_validation(ledger)
                self.assertTrue(result["ok"], result["error_codes"])

    def test_local_evidence_types_require_input_refs(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "reproducible_calculation"
        evidence.pop("url")
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_INPUT_REFS_MISSING", result["error_codes"])

    def test_not_applicable_fit_requires_dimension_explanation(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["fit"]["unit"] = "not_applicable"
        evidence["fit_explanation"] = {"unit": "该政策适用判断没有计量单位"}
        result = run_validation(ledger)
        self.assertTrue(result["ok"])

        evidence.pop("fit_explanation")
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_FIT_NA_EXPLANATION_MISSING", result["error_codes"])

    def test_ready_fails_when_high_risk_issue_is_open(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        result = run_validation(ledger)
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_ready_also_fails_when_r2_issue_is_open(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue["risk_level"] = "R2"
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        result = run_validation(ledger)
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])
        self.assertEqual(result["unresolved_material_risk_count"], 1)

    def test_publication_status_matches_chinese_report_conclusion(self):
        for status in ("ready", "revise", "hold"):
            with self.subTest(status=status):
                report = valid_report(status)
                ledger = valid_ledger(report, publication_status=status)
                result = run_validation(ledger, report)
                self.assertTrue(result["ok"], result["error_codes"])

        mismatched_report = valid_report("revise")
        ledger = valid_ledger(mismatched_report, publication_status="ready")
        result = run_validation(ledger, mismatched_report)
        self.assertIn("PUBLICATION_STATUS_REPORT_MISMATCH", result["error_codes"])

    def test_supported_evidence_does_not_replace_workflow_closure(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "supported"
        result = run_validation(ledger)
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_revise_allows_open_high_risk_issue(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"])
        self.assertEqual(result["unresolved_high_risk_count"], 1)

    def test_new_viewpoints_are_capped_at_two(self):
        ledger = valid_ledger()
        ledger["new_viewpoints"] = ledger["new_viewpoints"] * 3
        result = run_validation(ledger)
        self.assertIn("TOO_MANY_NEW_VIEWPOINTS", result["error_codes"])

    def test_new_viewpoint_requires_decision_mechanism_evidence_and_boundary(self):
        ledger = valid_ledger()
        ledger["new_viewpoints"] = [
            {
                "actor": "",
                "decision": "",
                "mechanism": "",
                "evidence_refs": ["EV-404"],
                "boundary": "",
                "falsifier": "",
            }
        ]
        result = run_validation(ledger)
        for code in (
            "NEW_VIEWPOINT_ACTOR_MISSING",
            "NEW_VIEWPOINT_DECISION_MISSING",
            "NEW_VIEWPOINT_MECHANISM_MISSING",
            "NEW_VIEWPOINT_BOUNDARY_MISSING",
            "NEW_VIEWPOINT_FALSIFIER_MISSING",
            "UNKNOWN_NEW_VIEWPOINT_EVIDENCE_REF",
        ):
            self.assertIn(code, result["error_codes"])

    def test_new_viewpoint_cannot_rely_on_search_summary(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        ledger["issues"] = []
        ledger["evidence_records"][0]["acquisition"] = "search_snippet"
        result = run_validation(ledger, report)
        self.assertIn("NEW_VIEWPOINT_EVIDENCE_NOT_ADMISSIBLE", result["error_codes"])

    def test_invalid_user_report_fails_bundle(self):
        report = "# 只有标题\n"
        ledger = valid_ledger(report)
        result = run_validation(ledger, report)
        self.assertIn("USER_REPORT_INVALID", result["error_codes"])
        self.assertGreater(result["report_error_count"], 0)

    def test_cli_output_does_not_echo_private_content_or_paths(self):
        private_marker = "private-bundle-canary"
        draft = DRAFT + private_marker
        report = valid_report()
        ledger = valid_ledger(report)
        ledger["draft_sha256"] = digest(draft)
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            draft_path = temp / f"{private_marker}-draft.md"
            ledger_path = temp / f"{private_marker}-ledger.json"
            report_path = temp / f"{private_marker}-report.md"
            draft_path.write_bytes(draft.encode("utf-8"))
            ledger_path.write_bytes(json.dumps(ledger, ensure_ascii=False).encode("utf-8"))
            report_path.write_bytes(report.encode("utf-8"))
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
        self.assertEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertNotIn(private_marker, completed.stdout)


if __name__ == "__main__":
    unittest.main()
