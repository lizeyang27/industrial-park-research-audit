from __future__ import annotations

import json
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import score_factorial_eval  # noqa: E402


FIXTURE = ROOT / "examples" / "synthetic" / "factorial-eval-input.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class FactorialEvaluationTests(unittest.TestCase):
    def test_synthetic_fixture_validates_and_scores_paired_effects(self) -> None:
        result = score_factorial_eval.score_factorial_eval(load_fixture())

        self.assertTrue(result["validation"]["valid"])
        self.assertEqual(result["validation"]["case_count"], 3)
        self.assertEqual(result["protocol_id"], score_factorial_eval.PROTOCOL_ID)
        self.assertEqual(
            result["arithmetic_scope"]["missingness"],
            "pairwise_complete_per_case_no_zero_imputation",
        )
        self.assertFalse(result["identifier_policy"]["case_ids_included"])
        self.assertNotIn("case_id", recall_case := result["metrics"]["r3_recall"]["per_case_values"][0])
        self.assertEqual(recall_case["case_ordinal"], 1)

        recall = result["metrics"]["r3_recall"]
        self.assertAlmostEqual(recall["effects"]["support_direct"]["mean"], 1 / 6)
        self.assertAlmostEqual(recall["effects"]["support_when_framed"]["mean"], 0.15)
        self.assertAlmostEqual(recall["effects"]["frame_without_support"]["mean"], 0.15)
        self.assertAlmostEqual(recall["effects"]["frame_with_support"]["mean"], 2 / 15)
        self.assertAlmostEqual(recall["effects"]["support_main_effect"]["mean"], 19 / 120)
        self.assertAlmostEqual(recall["effects"]["frame_main_effect"]["mean"], 17 / 120)
        self.assertAlmostEqual(recall["effects"]["interaction"]["mean"], -1 / 60)

    def test_protocol_id_and_hash_invariants_fail_closed(self) -> None:
        wrong_protocol = load_fixture()
        wrong_protocol["protocol_id"] = "IRA-ABCDEF-V1.0"
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "protocol_id"):
            score_factorial_eval.score_factorial_eval(wrong_protocol)

        mutations = [
            (
                "prior_manifest_hash",
                "C/D/E/F",
                lambda payload: payload["cases"][0]["groups"]["D"]["artifact_hashes"].update(
                    {"prior_manifest_hash": "a" * 64}
                ),
            ),
            (
                "support_bundle_hash",
                "D and F",
                lambda payload: payload["cases"][0]["groups"]["F"]["artifact_hashes"].update(
                    {"support_bundle_hash": "a" * 64}
                ),
            ),
            (
                "framework_hash",
                "group E",
                lambda payload: payload["cases"][0]["groups"]["E"]["artifact_hashes"].update(
                    {"framework_hash": None}
                ),
            ),
        ]
        for label, message, mutate in mutations:
            with self.subTest(label=label):
                payload = load_fixture()
                mutate(payload)
                with self.assertRaisesRegex(score_factorial_eval.ProtocolError, message):
                    score_factorial_eval.score_factorial_eval(payload)

    def test_missing_metric_is_not_zero_imputed(self) -> None:
        payload = load_fixture()
        payload["cases"][1]["groups"]["D"]["metrics"]["r3_recall"] = None

        result = score_factorial_eval.score_factorial_eval(payload)
        recall = result["metrics"]["r3_recall"]
        direct = recall["effects"]["support_direct"]
        main = recall["effects"]["support_main_effect"]

        self.assertEqual(recall["groups"]["D"]["n_observed"], 2)
        self.assertEqual(recall["groups"]["D"]["n_missing"], 1)
        self.assertEqual(direct["n_pairs"], 2)
        self.assertAlmostEqual(direct["mean"], 0.125)
        self.assertIsNone(direct["per_case"][1]["value"])
        self.assertEqual(direct["per_case"][1]["missing_groups"], ["D"])
        self.assertEqual(main["n_pairs"], 2)

    def test_reported_token_fields_are_only_reported_when_supplied(self) -> None:
        result = score_factorial_eval.score_factorial_eval(load_fixture())
        usage = result["reported_token_usage"]

        self.assertEqual(usage["provenance"], "caller_reported")
        self.assertFalse(usage["derived_from_characters_or_bytes"])
        self.assertFalse(usage["unreported_token_fields_inferred"])
        self.assertEqual(set(usage["fields"]), {"input_tokens", "output_tokens", "tool_tokens"})
        self.assertNotIn("total_tokens", usage["fields"])
        self.assertEqual(usage["fields"]["input_tokens"]["groups"]["C"]["n_observed"], 2)
        self.assertEqual(usage["fields"]["output_tokens"]["groups"]["F"]["observed_sum"], 240)
        self.assertIsNone(usage["fields"]["tool_tokens"]["groups"]["C"]["observed_sum"])

        no_usage = load_fixture()
        for case in no_usage["cases"]:
            for run in case["groups"].values():
                run.pop("reported_token_usage", None)
        no_usage_result = score_factorial_eval.score_factorial_eval(no_usage)
        self.assertEqual(no_usage_result["reported_token_usage"]["fields"], {})

    def test_non_token_usage_and_non_numeric_metrics_are_rejected(self) -> None:
        bad_usage = load_fixture()
        bad_usage["cases"][0]["groups"]["C"]["reported_token_usage"]["input_bytes"] = 1234
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "reported token field"):
            score_factorial_eval.score_factorial_eval(bad_usage)

        estimated_usage = load_fixture()
        estimated_usage["cases"][0]["groups"]["C"]["reported_token_usage"]["estimated_tokens"] = 1234
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "reported token field"):
            score_factorial_eval.score_factorial_eval(estimated_usage)

        bad_metric = load_fixture()
        bad_metric["cases"][0]["groups"]["C"]["metrics"]["r3_recall"] = True
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "finite number or null"):
            score_factorial_eval.score_factorial_eval(bad_metric)

    def test_metric_definitions_enforce_range_and_group_applicability(self) -> None:
        result = score_factorial_eval.score_factorial_eval(load_fixture())
        source_role = result["metrics"]["source_role_accuracy"]
        framework = result["metrics"]["framework_structure_score"]

        self.assertEqual(result["metric_sets"]["common_outcomes"], ["false_positives", "r3_recall"])
        self.assertEqual(
            result["metric_sets"]["group_process"],
            ["framework_structure_score", "source_role_accuracy"],
        )
        self.assertFalse(source_role["effects"]["support_main_effect"]["applicable"])
        self.assertEqual(source_role["effects"]["support_main_effect"]["n_missing"], 0)
        self.assertTrue(source_role["effects"]["frame_with_support"]["applicable"])
        self.assertTrue(framework["effects"]["support_when_framed"]["applicable"])
        self.assertFalse(framework["effects"]["interaction"]["applicable"])

        out_of_range = load_fixture()
        out_of_range["cases"][0]["groups"]["C"]["metrics"]["r3_recall"] = 1.1
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "declared range"):
            score_factorial_eval.score_factorial_eval(out_of_range)

        wrong_group = load_fixture()
        wrong_group["cases"][0]["groups"]["C"]["metrics"]["source_role_accuracy"] = 0.5
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "not applicable"):
            score_factorial_eval.score_factorial_eval(wrong_group)

    def test_execution_controls_and_reported_costs_are_explicit(self) -> None:
        result = score_factorial_eval.score_factorial_eval(load_fixture())
        costs = result["reported_operational_costs"]

        self.assertEqual(costs["provenance"], "caller_reported")
        self.assertEqual(set(costs["fields"]), {"external_calls", "human_review_minutes"})
        self.assertEqual(result["execution_control"]["run_counts"][0]["run_count_per_group"], 1)
        self.assertIn("bootstrap_interval", result["arithmetic_scope"]["not_computed"])

        bad_run_count = load_fixture()
        bad_run_count["cases"][0]["groups"]["F"]["run_count"] = 2
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "run_count invariant"):
            score_factorial_eval.score_factorial_eval(bad_run_count)

        bad_configuration = load_fixture()
        bad_configuration["cases"][0]["groups"]["F"]["artifact_hashes"][
            "execution_configuration_hash"
        ] = "a" * 64
        with self.assertRaisesRegex(score_factorial_eval.ProtocolError, "execution_configuration_hash"):
            score_factorial_eval.score_factorial_eval(bad_configuration)

    def test_cli_writes_scored_json_without_input_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scored.json"
            exit_code = score_factorial_eval.main([str(FIXTURE), "--output", str(output)])
            self.assertEqual(exit_code, 0)
            payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(payload["evaluation_id"], "EVAL-SYNTHETIC-V11-001")
            self.assertNotIn(str(FIXTURE), json.dumps(payload, ensure_ascii=False))

    def test_output_refuses_overwrite_except_explicit_identical_idempotence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "scored.json"
            first_stdout = io.StringIO()
            with redirect_stdout(first_stdout):
                self.assertEqual(
                    score_factorial_eval.main([str(FIXTURE), "--output", str(output)]),
                    0,
                )
            original = output.read_bytes()

            overwrite_stderr = io.StringIO()
            with redirect_stderr(overwrite_stderr):
                self.assertEqual(
                    score_factorial_eval.main([str(FIXTURE), "--output", str(output)]),
                    2,
                )
            self.assertEqual(output.read_bytes(), original)
            self.assertIn("OUTPUT_ALREADY_EXISTS", overwrite_stderr.getvalue())

            identical_stdout = io.StringIO()
            with redirect_stdout(identical_stdout):
                self.assertEqual(
                    score_factorial_eval.main(
                        [
                            str(FIXTURE),
                            "--output",
                            str(output),
                            "--allow-identical-existing",
                        ]
                    ),
                    0,
                )
            self.assertIn("unchanged_identical", identical_stdout.getvalue())
            self.assertEqual(output.read_bytes(), original)

    def test_private_cli_requires_private_output_and_omits_identifiers(self) -> None:
        private_payload = load_fixture()
        private_payload.pop("fixture")
        private_payload["evaluation_id"] = "EVAL-PRIVATE-CANARY-991"
        private_payload["cases"][0]["case_id"] = "CASE-PRIVATE-CANARY-992"

        with tempfile.TemporaryDirectory() as directory:
            input_path = Path(directory) / "private-input.json"
            output_path = Path(directory) / "private-output.json"
            input_path.write_text(json.dumps(private_payload), encoding="utf-8")

            no_output_stderr = io.StringIO()
            with redirect_stderr(no_output_stderr):
                no_output_code = score_factorial_eval.main([str(input_path)])
            self.assertEqual(no_output_code, 2)
            self.assertNotIn("EVAL-PRIVATE-CANARY-991", no_output_stderr.getvalue())
            self.assertNotIn("CASE-PRIVATE-CANARY-992", no_output_stderr.getvalue())

            unsafe_stderr = io.StringIO()
            with redirect_stderr(unsafe_stderr):
                unsafe_code = score_factorial_eval.main(
                    [str(input_path), "--output", str(ROOT / "examples" / "private-score.json")]
                )
            self.assertEqual(unsafe_code, 2)
            self.assertFalse((ROOT / "examples" / "private-score.json").exists())
            self.assertNotIn("EVAL-PRIVATE-CANARY-991", unsafe_stderr.getvalue())

            safe_stdout = io.StringIO()
            with redirect_stdout(safe_stdout):
                safe_code = score_factorial_eval.main(
                    [str(input_path), "--output", str(output_path)]
                )
            self.assertEqual(safe_code, 0)
            self.assertTrue(output_path.exists())
            self.assertNotIn("EVAL-PRIVATE-CANARY-991", safe_stdout.getvalue())
            self.assertNotIn("CASE-PRIVATE-CANARY-992", safe_stdout.getvalue())
            self.assertNotIn(str(output_path), safe_stdout.getvalue())
            self.assertNotIn(str(input_path), safe_stdout.getvalue())
            private_result = output_path.read_text(encoding="utf-8")
            self.assertNotIn("CASE-PRIVATE-CANARY-992", private_result)
            self.assertIn('"case_ordinal": 1', private_result)


if __name__ == "__main__":
    unittest.main()
