from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import estimate_verification_cost  # noqa: E402


class CostEstimatorTests(unittest.TestCase):
    def test_stages_are_incremental_and_total_is_additive(self):
        result = estimate_verification_cost.estimate_cost(
            document_chars=4000,
            material_claims=20,
            provided_sources=3,
            v1_issues=5,
            v2_issues=2,
            v3_issues=1,
            live_search_issues=1,
            retrieved_kb_cards=4,
        )
        bands = result["estimated_model_tokens"]
        for edge in ("low", "high"):
            expected = (
                bands["initial_triage_and_v1"][edge]
                + bands["optional_v2_increment"][edge]
                + bands["optional_v3_increment"][edge]
            )
            self.assertEqual(bands["all_levels_total"][edge], expected)

    def test_more_deep_issues_cost_more_than_local_only(self):
        local = estimate_verification_cost.estimate_cost(
            document_chars=3000,
            material_claims=12,
            provided_sources=1,
            v1_issues=3,
            v2_issues=0,
            v3_issues=0,
        )
        deep = estimate_verification_cost.estimate_cost(
            document_chars=3000,
            material_claims=12,
            provided_sources=1,
            v1_issues=3,
            v2_issues=2,
            v3_issues=1,
            live_search_issues=1,
            retrieved_kb_cards=2,
        )
        self.assertGreater(
            deep["estimated_model_tokens"]["all_levels_total"]["low"],
            local["estimated_model_tokens"]["all_levels_total"]["low"],
        )

    def test_invalid_live_search_count_fails(self):
        with self.assertRaises(ValueError):
            estimate_verification_cost.estimate_cost(
                document_chars=100,
                material_claims=1,
                provided_sources=0,
                v1_issues=0,
                v2_issues=0,
                v3_issues=0,
                live_search_issues=1,
            )


if __name__ == "__main__":
    unittest.main()
