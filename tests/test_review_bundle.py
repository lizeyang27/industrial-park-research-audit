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
SUPPLIED_BYTES = b"synthetic supplied source bytes for validator tests\n"


REPORT_STATUS_TEXT = {
    "ready": "可以发布",
    "revise": "修改后发布",
    "hold": "暂缓发布",
}


def valid_report(
    publication_status: str = "ready", viewpoint_count: int = 1, issue_count: int = 0
) -> str:
    if viewpoint_count:
        viewpoints = "\n\n".join(
            f"### {index}. 签约前核验租约稳定性\n"
            "- 适用对象：拟入园企业\n"
            "- 决策问题：是否在签约前追加租约稳定性核验\n"
            "- 可能机制：出租率口径可能掩盖短租与免租期差异\n"
            "- 适用边界：只适用于租约结构会影响持续经营判断的项目\n"
            "- 反证条件：若逐份租约显示期限和付租状态一致，该机制不成立"
            for index in range(1, viewpoint_count + 1)
        )
    else:
        viewpoints = "本稿暂不足以形成可靠的新观点。"
    if issue_count:
        issue_items = "\n\n".join(
            f"### {index}. 说明数字口径\n"
            "- 原文位置：第一段原文“2025年出租率为80%”。\n"
            "- 为什么重要：影响判断。\n"
            "- 建议处理：补充统计范围。"
            for index in range(1, issue_count + 1)
        )
    else:
        issue_items = "本稿没有仍需在发布前处理的问题。"
    bodies = {
        "结论": f"{REPORT_STATUS_TEXT[publication_status]}。核心数字已经核到原始页面。",
        "这篇文章已经做好的地方": "- 决策对象清楚。",
        "发布前需要处理": issue_items,
        "建议怎么改": "保留原句并补充时间和范围。",
        "可继续研究的新观点": viewpoints,
        "如果还要继续核验": "可取得项目租约或请行业专家判断长期机制。",
        "本次审阅没有验证什么": "没有取得非公开租约和企业经营资料。",
    }
    sections = []
    for name in validate_user_report.REQUIRED_SECTIONS:
        sections.append(f"## {name}\n{bodies.get(name, '本节暂无补充。')}")
    return "# 审阅报告\n\n" + "\n\n".join(sections) + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def report_item_hashes(report: str, section: str) -> list[str]:
    reader = validate_user_report.split_reader_layer(report)
    positions = validate_user_report.section_positions(reader)
    return [
        validate_user_report.reader_item_sha256(item)
        for item in validate_user_report.section_items(reader, positions, section)
    ]


def add_supplied_input(ledger: dict[str, object]) -> None:
    ledger["supplied_inputs"] = [
        {
            "input_id": "SUPPLIED-001",
            "sha256": hashlib.sha256(SUPPLIED_BYTES).hexdigest(),
            "media_type": "application/vnd.synthetic.source",
            "captured_at": "2026-09-14T08:30:00+08:00",
        }
    ]


def clear_viewpoints(ledger: dict[str, object]) -> None:
    ledger["new_viewpoints"] = []
    for evidence in ledger.get("evidence_records", []):
        if isinstance(evidence, dict) and isinstance(evidence.get("fit_target_refs"), list):
            evidence["fit_target_refs"] = [
                ref
                for ref in evidence["fit_target_refs"]
                if not (isinstance(ref, str) and ref.startswith("viewpoint:"))
            ]


def bind_first_issue_to_reader(ledger: dict[str, object]) -> None:
    issue = ledger["issues"][0]
    if issue.get("workflow_state") == "closed":
        issue["workflow_state"] = "decision_ready"
        issue["risk_level"] = "R2"
    issue["publication_effect"] = "revise_before_publish"
    issue["reader_binding"] = {
        "item_index": 1,
        "location_excerpt": "2025年出租率为80%",
        "problem_excerpt": "说明数字口径",
        "reason_excerpt": "影响判断",
        "action_excerpt": "补充统计范围",
    }


def set_active_publication_status(ledger: dict[str, object], status: str) -> str:
    report = valid_report(status, issue_count=1)
    ledger["publication_status"] = status
    ledger["report_sha256"] = digest(report)
    bind_first_issue_to_reader(ledger)
    if status == "hold":
        ledger["issues"][0]["publication_effect"] = "block_publication"
    return report


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
    viewpoint_hashes = report_item_hashes(report, "可继续研究的新观点")
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
        "supplied_inputs": [],
        "claim_register": [
            {
                "claim_id": "CLM-001",
                "source_anchor": "2025年出租率为80%",
                "source_locator": "第一段第一句",
                "claim": "甲园区2025年出租率为80%",
                "claim_type": "fact",
                "scope": "甲园区；2025年；出租率；百分比",
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
                "action_type": "open_primary_source",
                "result": "原始统计表与主张的主体、时点和口径一致",
                "performed_by": "researcher",
                "performed_at": "2026-09-14T09:00:00+08:00",
            }
        ],
        "closure_decisions": [
            {
                "closure_id": "CLS-001",
                "issue_refs": ["ISS-001"],
                "evidence_refs": ["EV-001"],
                "predicate_results": {
                    "evidence_admissible": True,
                    "claim_resolved": True,
                    "scope_fit_confirmed": True,
                    "human_review_completed": True,
                },
                "disposition": "evidence_added",
                "resolution_action": "补入已核对的原始统计表证据",
                "reviewer_role": "industry_research_reviewer",
                "reviewed_at": "2026-09-14T09:15:00+08:00",
                "residual_limitations": "未取得逐份租约，只能确认公开统计口径",
                "reopen_conditions": ["来源页面修订、撤回或统计口径变化"],
                "human_reviewed": True,
            }
        ],
        "evidence_records": [
            {
                "evidence_id": "EV-001",
                "acquisition": "opened_page",
                "url": "https://example.gov.cn/not-a-real-case",
                "locator": "合成页面中的统计表第一行",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
                "fit_target_refs": ["claim:CLM-001", "viewpoint:VP-001"],
            }
        ],
        "issues": [
            {
                "issue_id": "ISS-001",
                "claim_refs": ["CLM-001"],
                "risk_level": "R3",
                "requires_external_evidence": True,
                "source_anchor": "2025年出租率为80%",
                "source_locator": "第一段第一句",
                "problem": "需要说明数字口径并确认时间范围",
                "reason": "统计范围不清会影响判断",
                "suggested_action": "核对原始统计表并补充统计范围",
                "workflow_state": "closed",
                "verification_status": "supported",
                "verification_level": "V2",
                "publication_effect": "none",
                "closure_evidence_refs": ["EV-001"],
                "verification_action_refs": ["ACT-001"],
                "closure_decision_ref": "CLS-001",
            }
        ],
        "new_viewpoints": [
            {
                "viewpoint_id": "VP-001",
                "actor": "拟入园企业",
                "decision": "是否在签约前追加租约稳定性核验",
                "mechanism": "出租率口径可能掩盖短租与免租期差异",
                "evidence_refs": ["EV-001"],
                "boundary": "只适用于租约结构会影响持续经营判断的项目",
                "falsifier": "若逐份租约显示期限和付租状态一致，该机制不成立",
                "reader_item_sha256": viewpoint_hashes[0] if viewpoint_hashes else "0" * 64,
            }
        ],
    }


def run_validation(
    ledger: dict[str, object],
    report: str | None = None,
    *,
    verify_supplied_inputs: bool = True,
) -> dict[str, object]:
    if report is None:
        status = ledger.get("publication_status", "ready")
        report = valid_report(status if status in REPORT_STATUS_TEXT else "ready")
    supplied_input_bytes: dict[str, bytes] = {}
    if verify_supplied_inputs:
        for supplied in ledger.get("supplied_inputs", []):
            if (
                isinstance(supplied, dict)
                and supplied.get("input_id") == "SUPPLIED-001"
                and supplied.get("sha256") == hashlib.sha256(SUPPLIED_BYTES).hexdigest()
            ):
                supplied_input_bytes["SUPPLIED-001"] = SUPPLIED_BYTES
    return validate_review_bundle.validate_bundle(
        DRAFT.encode("utf-8"),
        ledger,
        report.encode("utf-8"),
        supplied_input_bytes=supplied_input_bytes,
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
        report = set_active_publication_status(ledger, "revise")
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"])

    def test_unresolved_material_claim_blocks_ready_but_allows_revise_or_hold(self):
        for publication_status in ("ready", "revise", "hold"):
            with self.subTest(publication_status=publication_status):
                ledger = valid_ledger()
                report = (
                    set_publication_status(ledger, publication_status)
                    if publication_status == "ready"
                    else set_active_publication_status(ledger, publication_status)
                )
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

    def test_non_ready_material_claim_statuses_require_plan_and_block_ready(self):
        for evidence_status in (
            "partially_supported",
            "contradicted",
            "conflicting",
            "stale",
            "missing",
            "not_verifiable",
        ):
            with self.subTest(evidence_status=evidence_status):
                ledger = valid_ledger()
                claim = ledger["claim_register"][0]
                claim["evidence_status"] = evidence_status
                result = run_validation(ledger)
                self.assertIn(
                    "IMPORTANT_EXTERNAL_CLAIM_WITHOUT_EVIDENCE_OR_PLAN",
                    result["error_codes"],
                )

                claim["unresolved_reason"] = "现有证据不足以关闭该核心主张"
                claim["next_action"] = "取得新的原始材料后重新判断"

                result = run_validation(ledger)
                self.assertIn(
                    "MATERIAL_CLAIM_UNRESOLVED_WHILE_READY", result["error_codes"]
                )
                self.assertEqual(result["unresolved_material_claim_count"], 1)

                report = set_active_publication_status(ledger, "revise")
                result = run_validation(ledger, report)
                self.assertTrue(result["ok"], result["error_codes"])

    def test_negative_material_internal_claim_status_blocks_ready(self):
        ledger = valid_ledger()
        claim = ledger["claim_register"][0]
        claim["claim_type"] = "judgment"
        claim["requires_external_evidence"] = False
        claim["evidence_status"] = "contradicted"
        result = run_validation(ledger)
        self.assertIn("MATERIAL_CLAIM_NEGATIVE_STATUS_WITHOUT_PLAN", result["error_codes"])
        self.assertIn("MATERIAL_CLAIM_UNRESOLVED_WHILE_READY", result["error_codes"])

        claim["unresolved_reason"] = "现有证据与核心判断冲突"
        claim["next_action"] = "缩窄判断并重新审阅"
        report = set_active_publication_status(ledger, "revise")
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"], result["error_codes"])

    def test_claim_ids_and_anchors_are_bound_to_draft(self):
        ledger = valid_ledger()
        duplicate = dict(ledger["claim_register"][0])
        duplicate["source_anchor"] = "不存在的原文概括"
        ledger["claim_register"].append(duplicate)
        result = run_validation(ledger)
        self.assertIn("DUPLICATE_CLAIM_ID", result["error_codes"])
        self.assertIn("CLAIM_SOURCE_ANCHOR_NOT_FOUND", result["error_codes"])

    def test_claim_placeholders_and_core_coverage_are_rejected(self):
        ledger = valid_ledger()
        claim = ledger["claim_register"][0]
        claim["claim"] = " "
        claim["source_locator"] = ""
        claim["scope"] = None
        claim["claim_type"] = "opinion"
        claim["importance"] = "supporting"
        result = run_validation(ledger)
        for code in (
            "CLAIM_TEXT_MISSING",
            "CLAIM_SOURCE_LOCATOR_MISSING",
            "CLAIM_SCOPE_MISSING",
            "CLAIM_TYPE_INVALID",
            "NONEMPTY_DRAFT_WITHOUT_CORE_CLAIM",
        ):
            self.assertIn(code, result["error_codes"])

        ledger = valid_ledger()
        ledger["claim_register"][0]["claim"] = "占"
        result = run_validation(ledger)
        self.assertIn("CLAIM_TEXT_MISSING", result["error_codes"])

        for claim_type in ("fact", "calculation", "forecast", "inference"):
            with self.subTest(claim_type=claim_type):
                ledger = valid_ledger()
                ledger["claim_register"][0]["claim_type"] = claim_type
                ledger["claim_register"][0]["requires_external_evidence"] = False
                result = run_validation(ledger)
                self.assertIn("CLAIM_EXTERNAL_FLAG_CONTRADICTS_TYPE", result["error_codes"])

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

    def test_search_result_page_cannot_be_admitted_as_opened_evidence(self):
        for search_url in (
            "https://www.google.com/search?q=synthetic",
            "https://www.google.co.jp/search?q=synthetic",
            "https://www.baidu.com/link?url=synthetic",
            "https://www.bing.com/ck/a?synthetic",
            "https://duckduckgo.com/?q=synthetic",
        ):
            with self.subTest(search_url=search_url):
                ledger = valid_ledger()
                ledger["web_trace"]["opened_pages"] = [search_url]
                ledger["evidence_records"][0]["url"] = search_url
                result = run_validation(ledger)
                self.assertIn("SEARCH_RESULT_URL_NOT_ADMISSIBLE", result["error_codes"])

    def test_material_external_claim_requires_real_scope_fit(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["fit"] = {dimension: "not_applicable" for dimension in validate_review_bundle.FIT_DIMENSIONS}
        evidence["fit_explanation"] = {
            dimension: "合成解释" for dimension in validate_review_bundle.FIT_DIMENSIONS
        }
        ledger["claim_register"][0]["unresolved_reason"] = "来源与主体和期间尚未匹配"
        ledger["claim_register"][0]["next_action"] = "打开匹配主体与期间的原始来源"
        result = run_validation(ledger)
        self.assertIn("EXTERNAL_CLAIM_EVIDENCE_SCOPE_FIT_INSUFFICIENT", result["error_codes"])
        self.assertIn("MATERIAL_CLAIM_UNRESOLVED_WHILE_READY", result["error_codes"])

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

    def test_offline_run_can_leave_external_r2_issue_open(self):
        ledger = valid_ledger()
        add_supplied_input(ledger)
        clear_viewpoints(ledger)
        report = valid_report("revise", viewpoint_count=0, issue_count=1)
        ledger["publication_status"] = "revise"
        ledger["report_sha256"] = digest(report)
        ledger["web_trace"] = {
            "network_available": False,
            "search_count": 0,
            "opened_pages": [],
            "stop_reason": "网络不可用，保留问题",
            "skip_reason": "宿主未提供网络工具",
        }
        issue = ledger["issues"][0]
        issue["risk_level"] = "R2"
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        bind_first_issue_to_reader(ledger)
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["SUPPLIED-001"]
        result = run_validation(ledger, report)
        self.assertTrue(result["ok"])
        self.assertEqual(result["unresolved_high_risk_count"], 0)
        self.assertEqual(result["unresolved_material_risk_count"], 1)

    def test_offline_trace_rejects_opened_pages_and_missing_skip_reason(self):
        ledger = valid_ledger()
        ledger["issues"] = []
        clear_viewpoints(ledger)
        report = valid_report("revise", viewpoint_count=0).replace(
            "### 1. 说明数字口径\n- 原文位置：第一段原文“2025年出租率为80%”。\n- 为什么重要：影响判断。\n- 建议处理：补充统计范围。",
            "",
        )
        ledger["publication_status"] = "revise"
        ledger["report_sha256"] = digest(report)
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

    def test_issue_must_link_to_existing_claim_and_declare_evidence_route(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue["claim_refs"] = ["CLM-404"]
        issue["requires_external_evidence"] = "yes"
        result = run_validation(ledger)
        self.assertIn("UNKNOWN_ISSUE_CLAIM_REF", result["error_codes"])
        self.assertIn("ISSUE_EXTERNAL_EVIDENCE_FLAG_INVALID", result["error_codes"])

        issue.pop("claim_refs")
        result = run_validation(ledger)
        self.assertIn("ISSUE_CLAIM_REFS_INVALID", result["error_codes"])

        ledger = valid_ledger()
        ledger["issues"][0]["requires_external_evidence"] = False
        result = run_validation(ledger)
        self.assertIn("ISSUE_EXTERNAL_FLAG_CONTRADICTS_CLAIM", result["error_codes"])

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

    def test_closed_issue_accepts_only_verified_supported_or_derived(self):
        for verification_status in (
            "partially_supported",
            "contradicted",
            "conflicting",
            "stale",
            "missing",
            "not_verifiable",
        ):
            with self.subTest(verification_status=verification_status):
                ledger = valid_ledger()
                ledger["issues"][0]["verification_status"] = verification_status
                result = run_validation(ledger)
                self.assertIn(
                    "CLOSED_ISSUE_VERIFICATION_STATUS_UNRESOLVED",
                    result["error_codes"],
                )
                self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

        ledger = valid_ledger()
        ledger["issues"][0]["verification_status"] = "derived"
        result = run_validation(ledger)
        self.assertIn(
            "CLOSED_DERIVED_ISSUE_WITHOUT_VALID_CALCULATION", result["error_codes"]
        )
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_trace_objects_cannot_be_empty_shells(self):
        ledger = valid_ledger()
        action = ledger["verification_actions"][0]
        for field in ("action_type", "result", "performed_by", "performed_at"):
            action[field] = ""
        decision = ledger["closure_decisions"][0]
        decision["predicate_results"] = {}
        decision["disposition"] = "accepted_risk"
        for field in (
            "resolution_action",
            "reviewer_role",
            "reviewed_at",
            "residual_limitations",
        ):
            decision[field] = ""
        decision["reopen_conditions"] = []
        result = run_validation(ledger)
        for code in (
            "VERIFICATION_ACTION_ACTION_TYPE_MISSING",
            "VERIFICATION_ACTION_RESULT_MISSING",
            "VERIFICATION_ACTION_PERFORMED_BY_MISSING",
            "VERIFICATION_ACTION_PERFORMED_AT_INVALID",
            "CLOSURE_DECISION_PREDICATE_RESULTS_INVALID",
            "CLOSURE_DECISION_DISPOSITION_INVALID",
            "CLOSURE_DECISION_RESOLUTION_ACTION_MISSING",
            "CLOSURE_DECISION_REVIEWER_ROLE_MISSING",
            "CLOSURE_DECISION_REVIEWED_AT_INVALID",
            "CLOSURE_DECISION_RESIDUAL_LIMITATIONS_MISSING",
            "CLOSURE_DECISION_REOPEN_CONDITIONS_INVALID",
        ):
            self.assertIn(code, result["error_codes"])

        ledger = valid_ledger()
        ledger["closure_decisions"][0]["predicate_results"] = {
            "source_opened": True,
            "scope_matches": False,
        }
        result = run_validation(ledger)
        self.assertIn("CLOSURE_DECISION_PREDICATE_RESULTS_INVALID", result["error_codes"])

    def test_external_issue_cannot_be_closed_with_draft_self_evidence(self):
        ledger = valid_ledger()
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-002",
                "acquisition": "draft_internal",
                "input_refs": ["draft:first-paragraph"],
                "locator": "第一段",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            }
        )
        ledger["issues"][0]["closure_evidence_refs"] = ["EV-002"]
        ledger["verification_actions"][0]["evidence_refs"] = ["EV-002"]
        ledger["closure_decisions"][0]["evidence_refs"] = ["EV-002"]
        result = run_validation(ledger)
        self.assertIn(
            "CLOSED_EXTERNAL_ISSUE_WITHOUT_MATCHED_EXTERNAL_EVIDENCE",
            result["error_codes"],
        )
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_closed_material_issue_requires_human_review(self):
        ledger = valid_ledger()
        ledger["closure_decisions"][0]["human_reviewed"] = False
        result = run_validation(ledger)
        self.assertIn("CLOSED_MATERIAL_ISSUE_NOT_HUMAN_REVIEWED", result["error_codes"])
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

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

        ledger = valid_ledger()
        add_supplied_input(ledger)
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-002",
                "acquisition": "supplied_source",
                "input_refs": ["SUPPLIED-001"],
                "locator": "合成补充材料第一行",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            }
        )
        ledger["issues"][0]["closure_evidence_refs"] = ["EV-001", "EV-002"]
        ledger["closure_decisions"][0]["evidence_refs"] = ["EV-001", "EV-002"]
        result = run_validation(ledger)
        self.assertIn(
            "VERIFICATION_ACTIONS_DO_NOT_COVER_CLOSURE_EVIDENCE", result["error_codes"]
        )

        ledger["verification_actions"][0]["evidence_refs"] = ["EV-001", "EV-002"]
        ledger["closure_decisions"][0]["evidence_refs"] = ["EV-001"]
        result = run_validation(ledger)
        self.assertIn("CLOSURE_DECISION_EVIDENCE_MISMATCH", result["error_codes"])

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
                if acquisition == "supplied_source":
                    add_supplied_input(ledger)
                evidence = ledger["evidence_records"][0]
                evidence["acquisition"] = acquisition
                if acquisition != "opened_page":
                    evidence.pop("url")
                    evidence["input_refs"] = [
                        "draft:first-paragraph"
                        if acquisition == "draft_internal"
                        else "SUPPLIED-001"
                    ]
                if acquisition == "reproducible_calculation":
                    ledger["claim_register"][0]["evidence_status"] = "derived"
                    ledger["claim_register"][0]["derived_result"] = "80%"
                    ledger["claim_register"][0]["calculation_evidence_ref"] = "EV-001"
                    ledger["issues"][0]["verification_status"] = "derived"
                    evidence["input_evidence_refs"] = ["EV-002"]
                    evidence["calculation_expression"] = "occupied / available * 100"
                    evidence["calculation_result"] = "80%"
                    evidence["calculation_operands"] = [
                        {"name": "occupied", "value": "80", "evidence_ref": "EV-002"},
                        {"name": "available", "value": 100, "evidence_ref": "EV-002"},
                    ]
                    ledger["evidence_records"].append(
                        {
                            "evidence_id": "EV-002",
                            "acquisition": "opened_page",
                            "url": "https://example.gov.cn/not-a-real-case",
                            "locator": "合成页面中的计算输入行",
                            "fit": {
                                "entity": True,
                                "time": True,
                                "unit": True,
                                "scope": True,
                            },
                            "fit_target_refs": ["claim:CLM-001", "viewpoint:VP-001"],
                        }
                    )
                if acquisition == "draft_internal":
                    ledger["claim_register"][0]["claim_type"] = "judgment"
                    ledger["claim_register"][0]["requires_external_evidence"] = False
                    ledger["issues"][0]["requires_external_evidence"] = False
                    clear_viewpoints(ledger)
                    report = valid_report(viewpoint_count=0)
                    ledger["report_sha256"] = digest(report)
                    result = run_validation(ledger, report)
                else:
                    result = run_validation(ledger)
                self.assertTrue(result["ok"], result["error_codes"])

    def test_local_evidence_types_require_input_refs(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "reproducible_calculation"
        evidence.pop("url")
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_INPUT_REFS_MISSING", result["error_codes"])
        self.assertIn("CALCULATION_INPUT_EVIDENCE_REFS_INVALID", result["error_codes"])
        self.assertIn("CALCULATION_EXPRESSION_MISSING", result["error_codes"])
        self.assertIn("CALCULATION_RESULT_MISSING", result["error_codes"])
        self.assertIn("CALCULATION_OPERANDS_INVALID", result["error_codes"])

        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["Q:" + "\\synthetic\\private.docx"]
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_INPUT_REF_NOT_PORTABLE", result["error_codes"])
        self.assertIn("UNKNOWN_SUPPLIED_INPUT_REF", result["error_codes"])

        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["FI" + "LE:///Q:/synthetic/private.docx"]
        result = run_validation(ledger)
        self.assertIn("EVIDENCE_INPUT_REF_NOT_PORTABLE", result["error_codes"])

        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "supplied_source"
        evidence.pop("url")
        evidence["input_refs"] = ["draft:first-paragraph"]
        result = run_validation(ledger)
        self.assertIn("DRAFT_REF_USED_AS_SUPPLIED_SOURCE", result["error_codes"])
        self.assertIn("UNKNOWN_SUPPLIED_INPUT_REF", result["error_codes"])

    def test_supplied_input_manifest_is_required_and_hash_bound(self):
        ledger = valid_ledger()
        add_supplied_input(ledger)
        supplied = ledger["supplied_inputs"][0]
        supplied["sha256"] = "not-a-hash"
        supplied["media_type"] = "x"
        supplied["captured_at"] = "sometime"
        result = run_validation(ledger)
        self.assertIn("SUPPLIED_INPUT_SHA256_INVALID", result["error_codes"])
        self.assertIn("SUPPLIED_INPUT_MEDIA_TYPE_INVALID", result["error_codes"])
        self.assertIn("SUPPLIED_INPUT_CAPTURED_AT_INVALID", result["error_codes"])

    def test_draft_internal_cannot_self_support_external_claim(self):
        ledger = valid_ledger()
        evidence = ledger["evidence_records"][0]
        evidence["acquisition"] = "draft_internal"
        evidence.pop("url")
        evidence["input_refs"] = ["draft:first-paragraph"]
        result = run_validation(ledger)
        self.assertIn("EXTERNAL_CLAIM_WITHOUT_EXTERNAL_EVIDENCE", result["error_codes"])
        self.assertIn("MATERIAL_CLAIM_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_derived_claim_requires_calculation_with_external_lineage(self):
        ledger = valid_ledger()
        ledger["claim_register"][0]["evidence_status"] = "derived"
        result = run_validation(ledger)
        self.assertIn("DERIVED_CLAIM_WITHOUT_VALID_CALCULATION", result["error_codes"])

        ledger = valid_ledger()
        ledger["claim_register"][0]["evidence_status"] = "derived"
        ledger["claim_register"][0]["derived_result"] = "80%"
        ledger["claim_register"][0]["calculation_evidence_ref"] = "EV-001"
        add_supplied_input(ledger)
        calculation = ledger["evidence_records"][0]
        calculation["acquisition"] = "reproducible_calculation"
        calculation.pop("url")
        calculation["input_refs"] = ["formula:occupied/available"]
        calculation["input_evidence_refs"] = ["EV-002"]
        calculation["calculation_expression"] = "occupied / available * 100"
        calculation["calculation_result"] = "80%"
        calculation["calculation_operands"] = [
            {"name": "occupied", "value": 80, "evidence_ref": "EV-002"},
            {"name": "available", "value": "100", "evidence_ref": "EV-002"},
        ]
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-002",
                "acquisition": "supplied_source",
                "input_refs": ["SUPPLIED-001"],
                "locator": "用户提供租约表的汇总行",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
                "fit_target_refs": ["claim:CLM-001", "viewpoint:VP-001"],
            }
        )
        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])

        calculation = ledger["evidence_records"][0]
        ledger["claim_register"][0]["evidence_status"] = "supported"
        ledger["claim_register"][0].pop("derived_result")
        ledger["claim_register"][0].pop("calculation_evidence_ref")
        result = run_validation(ledger)
        self.assertIn(
            "SUPPORTED_EXTERNAL_CLAIM_WITHOUT_DIRECT_EVIDENCE", result["error_codes"]
        )

        ledger["claim_register"][0]["evidence_status"] = "derived"
        ledger["claim_register"][0]["derived_result"] = "80%"
        ledger["claim_register"][0]["calculation_evidence_ref"] = "EV-003"
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-003",
                "acquisition": "reproducible_calculation",
                "input_refs": ["formula:normalized-result"],
                "input_evidence_refs": ["EV-001"],
                "calculation_expression": "round(base_result, 1)",
                "calculation_result": "80.0%",
                "calculation_operands": [
                    {"name": "base_result", "value": "80%", "evidence_ref": "EV-001"}
                ],
                "locator": "合成二阶计算结果",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
                "fit_target_refs": ["claim:CLM-001"],
            }
        )
        ledger["claim_register"][0]["evidence_refs"] = ["EV-003"]
        ledger["issues"][0]["verification_status"] = "derived"
        ledger["issues"][0]["closure_evidence_refs"] = ["EV-003"]
        ledger["verification_actions"][0]["evidence_refs"] = ["EV-003"]
        ledger["closure_decisions"][0]["evidence_refs"] = ["EV-003"]
        result = run_validation(ledger)
        self.assertTrue(result["ok"], result["error_codes"])

        ledger["evidence_records"][-1]["calculation_operands"][0]["value"] = "81%"
        result = run_validation(ledger)
        self.assertIn(
            "CALCULATION_NESTED_OPERAND_VALUE_MISMATCH", result["error_codes"]
        )

    def test_calculation_expression_is_safely_recomputed(self):
        ledger = valid_ledger()
        ledger["claim_register"][0]["evidence_status"] = "derived"
        ledger["issues"][0]["verification_status"] = "derived"
        calculation = ledger["evidence_records"][0]
        calculation["acquisition"] = "reproducible_calculation"
        calculation.pop("url")
        calculation["input_refs"] = ["formula:one-plus-one"]
        calculation["input_evidence_refs"] = ["EV-002"]
        calculation["calculation_expression"] = "1 + 1"
        calculation["calculation_result"] = "3"
        calculation["calculation_operands"] = [
            {"name": "source_value", "value": 1, "evidence_ref": "EV-002"}
        ]
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-002",
                "acquisition": "opened_page",
                "url": "https://example.gov.cn/not-a-real-case",
                "locator": "合成页面中的计算输入行",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            }
        )
        result = run_validation(ledger)
        self.assertIn("CALCULATION_RESULT_MISMATCH", result["error_codes"])
        self.assertIn("CALCULATION_OPERAND_UNUSED", result["error_codes"])
        self.assertIn(
            "CALCULATION_INPUT_EVIDENCE_REF_WITHOUT_OPERAND", result["error_codes"]
        )

        calculation["calculation_expression"] = "__import__('os').system('whoami')"
        calculation["calculation_result"] = "1"
        result = run_validation(ledger)
        self.assertIn("CALCULATION_EXPRESSION_INVALID", result["error_codes"])

        calculation["calculation_expression"] = "'1' + '1'"
        calculation["calculation_result"] = "2"
        result = run_validation(ledger)
        self.assertIn("CALCULATION_EXPRESSION_INVALID", result["error_codes"])

    def test_every_calculation_input_requires_a_used_operand(self):
        ledger = valid_ledger()
        ledger["claim_register"][0]["evidence_status"] = "derived"
        ledger["issues"][0]["verification_status"] = "derived"
        calculation = ledger["evidence_records"][0]
        calculation["acquisition"] = "reproducible_calculation"
        calculation.pop("url")
        calculation["input_refs"] = ["formula:left-plus-right"]
        calculation["input_evidence_refs"] = ["EV-002", "EV-003"]
        calculation["calculation_expression"] = "left + right"
        calculation["calculation_result"] = 3
        calculation["calculation_operands"] = [
            {"name": "left", "value": 1, "evidence_ref": "EV-002"},
            {"name": "right", "value": 2, "evidence_ref": "EV-002"},
        ]
        for evidence_id in ("EV-002", "EV-003"):
            ledger["evidence_records"].append(
                {
                    "evidence_id": evidence_id,
                    "acquisition": "opened_page",
                    "url": "https://example.gov.cn/not-a-real-case",
                    "locator": f"合成页面中的输入 {evidence_id}",
                    "fit": {
                        "entity": True,
                        "time": True,
                        "unit": True,
                        "scope": True,
                    },
                }
            )
        result = run_validation(ledger)
        self.assertIn(
            "CALCULATION_INPUT_EVIDENCE_REF_WITHOUT_OPERAND", result["error_codes"]
        )

        calculation["input_evidence_refs"] = ["EV-002"]
        calculation["calculation_operands"][1]["evidence_ref"] = "EV-003"
        result = run_validation(ledger)
        self.assertIn(
            "CALCULATION_OPERAND_EVIDENCE_REF_NOT_INPUT", result["error_codes"]
        )

    def test_calculation_lineage_requires_other_valid_external_evidence(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        calculation = ledger["evidence_records"][0]
        calculation["acquisition"] = "reproducible_calculation"
        calculation.pop("url")
        calculation["input_refs"] = ["formula:occupied/available"]
        calculation["input_evidence_refs"] = ["EV-002"]
        calculation["calculation_expression"] = "occupied / available * 100"
        calculation["calculation_result"] = "80%"
        calculation["calculation_operands"] = [
            {"name": "occupied", "value": 80, "evidence_ref": "EV-002"},
            {"name": "available", "value": 100, "evidence_ref": "EV-002"},
        ]
        ledger["evidence_records"].append(
            {
                "evidence_id": "EV-002",
                "acquisition": "draft_internal",
                "input_refs": ["draft:first-paragraph"],
                "locator": "第一段",
                "fit": {"entity": True, "time": True, "unit": True, "scope": True},
            }
        )
        claim = ledger["claim_register"][0]
        claim["unresolved_reason"] = "计算没有外部输入证据"
        claim["next_action"] = "取得原始出租明细后重新计算"
        result = run_validation(ledger, report)
        self.assertIn("CALCULATION_EXTERNAL_INPUT_MISSING", result["error_codes"])
        self.assertIn("CLAIM_EVIDENCE_NOT_ADMISSIBLE", result["error_codes"])

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
                ledger = valid_ledger()
                report = (
                    set_publication_status(ledger, status)
                    if status == "ready"
                    else set_active_publication_status(ledger, status)
                )
                result = run_validation(ledger, report)
                self.assertTrue(result["ok"], result["error_codes"])

        mismatched_report = valid_report("revise")
        ledger = valid_ledger(mismatched_report, publication_status="ready")
        result = run_validation(ledger, mismatched_report)
        self.assertIn("PUBLICATION_STATUS_REPORT_MISMATCH", result["error_codes"])

        ledger = valid_ledger()
        negated_report = set_active_publication_status(ledger, "hold").replace(
            "暂缓发布。", "无需暂缓发布，"
        )
        ledger["report_sha256"] = digest(negated_report)
        result = run_validation(ledger, negated_report)
        self.assertIn("USER_REPORT_INVALID", result["error_codes"])
        self.assertIn("PUBLICATION_STATUS_REPORT_MISMATCH", result["error_codes"])

    def test_supported_evidence_does_not_replace_workflow_closure(self):
        ledger = valid_ledger()
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "supported"
        result = run_validation(ledger)
        self.assertIn("R2_R3_UNRESOLVED_WHILE_READY", result["error_codes"])

    def test_hold_allows_open_high_risk_issue(self):
        ledger = valid_ledger()
        report = valid_report("hold", issue_count=1)
        ledger["publication_status"] = "hold"
        ledger["report_sha256"] = digest(report)
        issue = ledger["issues"][0]
        issue["workflow_state"] = "verifying"
        issue["verification_status"] = "missing"
        issue["closure_evidence_refs"] = []
        bind_first_issue_to_reader(ledger)
        issue["publication_effect"] = "block_publication"
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

        ledger = valid_ledger()
        viewpoint = ledger["new_viewpoints"][0]
        for field in ("actor", "decision", "mechanism", "boundary", "falsifier"):
            viewpoint[field] = "占"
        result = run_validation(ledger)
        for field in ("actor", "decision", "mechanism", "boundary", "falsifier"):
            self.assertIn(f"NEW_VIEWPOINT_{field.upper()}_MISSING", result["error_codes"])

    def test_new_viewpoint_cannot_rely_on_search_summary(self):
        ledger = valid_ledger()
        report = set_publication_status(ledger, "revise")
        ledger["issues"] = []
        ledger["evidence_records"][0]["acquisition"] = "search_snippet"
        result = run_validation(ledger, report)
        self.assertIn("NEW_VIEWPOINT_EVIDENCE_NOT_ADMISSIBLE", result["error_codes"])

    def test_reader_and_technical_viewpoint_counts_must_match(self):
        report = valid_report(viewpoint_count=0)
        ledger = valid_ledger(report)
        result = run_validation(ledger, report)
        self.assertIn("NEW_VIEWPOINT_REPORT_COUNT_MISMATCH", result["error_codes"])

    def test_reader_issue_section_cannot_invent_or_hide_all_ledger_issues(self):
        report = valid_report(issue_count=1)
        ledger = valid_ledger(report)
        ledger["issues"] = []
        ledger["verification_actions"] = []
        ledger["closure_decisions"] = []
        result = run_validation(ledger, report)
        self.assertIn("READER_REPORT_ISSUES_WITHOUT_LEDGER", result["error_codes"])

        report = valid_report("revise")
        ledger = valid_ledger(report)
        ledger["publication_status"] = "revise"
        ledger["issues"][0]["publication_effect"] = "revise_before_publish"
        result = run_validation(ledger, report)
        self.assertIn("PUBLICATION_EFFECT_READER_MAPPING_MISSING", result["error_codes"])
        self.assertIn("PUBLICATION_EFFECT_NOT_COVERED_IN_READER_REPORT", result["error_codes"])

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
