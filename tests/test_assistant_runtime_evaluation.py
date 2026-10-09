"""Evaluation conclusions must describe the actual covered user outcome."""
import importlib.util
from pathlib import Path
import unittest
import sqlite3

spec = importlib.util.spec_from_file_location("runtime_evaluation", Path(__file__).resolve().parents[1] / "scripts/evaluate-assistant-runtime.py")
evaluation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluation)


class RuntimeEvaluationTests(unittest.TestCase):
    def test_expiry_readback_compares_the_same_instant_across_timezones(self):
        stored = evaluation.utc_instant("2026-10-16 03:41:30.215177")
        self.assertEqual(stored, evaluation.utc_instant("2026-10-16T03:41:30.215177+00:00"))
        self.assertEqual(stored, evaluation.utc_instant("2026-10-16T11:41:30.215177+08:00"))
        self.assertNotEqual(stored, evaluation.utc_instant("2026-10-16T03:41:30.215177+08:00"))

    def test_closing_claim_check_distinguishes_positive_and_negative_claims(self):
        self.assertTrue(evaluation.unsupported_closing_claim("该报价是 11:30 收盘前最后一次盘面价格；可视为上午收盘价。"))
        self.assertFalse(evaluation.unsupported_closing_claim("这是供应商快照，不能视为上午收盘价，也不证明最后成交价。"))

    def test_rule_identity_comes_from_its_related_stock(self):
        with sqlite3.connect(":memory:") as db:
            db.row_factory = sqlite3.Row
            db.executescript("CREATE TABLE stocks(id INTEGER, symbol TEXT, market TEXT); CREATE TABLE price_alert_rules(id INTEGER, stock_id INTEGER, name TEXT); INSERT INTO stocks VALUES(1,'600519','CN'),(2,'600519','US'); INSERT INTO price_alert_rules VALUES(8,2,'QA rule');")
            instance = object.__new__(evaluation.RuntimeEvaluation)
            instance.sql = lambda query, args: [dict(row) for row in db.execute(query, args)]
            row = instance.rules_named("QA rule")[0]
            self.assertEqual((row["id"], row["symbol"], row["market"]), (8, "600519", "US"))

    def test_guardrail_success_does_not_claim_alert_fulfillment(self):
        self.assertEqual(evaluation.case_outcome("unsupported")["user_goal"], "not_fulfilled")
        self.assertEqual(evaluation.case_outcome("rejection")["user_goal"], "cancelled_by_user")
        self.assertEqual(evaluation.case_outcome("combo_approval")["user_goal"], "fulfilled_for_case")

    def test_missing_failed_and_replayed_coverage_cannot_pass(self):
        cases = list(evaluation.REQUIRED_CASES)
        passed = [{"id": name, "status": "PASS"} for name in cases]
        self.assertEqual(evaluation.report_status(cases, passed, "live-model"), "PASS")
        self.assertEqual(evaluation.report_status(cases, passed[:-1], "live-model"), "INCOMPLETE")
        self.assertEqual(evaluation.report_status(cases[:-1], passed[:-1], "live-model"), "INCOMPLETE")
        self.assertEqual(evaluation.report_status(cases, passed, "replay"), "INCOMPLETE")
        self.assertEqual(evaluation.report_status(cases, [{"id": cases[0], "status": "FAIL"}], "live-model"), "FAIL")

    def test_actual_pending_approval_blocks_readonly_acceptance(self):
        instance = object.__new__(evaluation.RuntimeEvaluation)
        instance.wait = lambda _: {"status": "completed", "model": "actual-model", "pending_approvals": [{"id": 1}]}
        instance.invocations = lambda _: []
        instance.sql = lambda *_: []
        with self.assertRaisesRegex(AssertionError, "approval"):
            instance.readonly_completed(1)


if __name__ == "__main__":
    unittest.main()
