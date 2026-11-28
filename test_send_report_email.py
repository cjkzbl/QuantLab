import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from allocation_strategy import STRATEGY_ID
from send_report_email import build_message, send_email


class DailyEmailTests(unittest.TestCase):
    def test_dry_run_emails_only_the_target_action_and_signal_date(self):
        summary = {
            "strategy_id": STRATEGY_ID, "end_date": "2026-11-25", "next_action": "按新目标调仓",
            "current_weights": {"TQQQ": .2, "BIL": .8},
            "target_weights": {"TQQQ": .25, "BIL": .75},
            "total_contributions": 50000., "final_value": 100000., "annualized_return": .2828, "max_drawdown": -.4149,
        }
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / "summary.json").write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")
            (directory / "trade_details.csv").write_text("asset\nBIL\n", encoding="utf-8")
            with patch.dict("os.environ", {"BUILD_RESULT": "success", "DEPLOY_RESULT": "success"}, clear=True):
                with patch("send_report_email.smtplib.SMTP_SSL") as smtp:
                    message = send_email(directory, dry_run=True)
                    smtp.assert_not_called()

        self.assertIn("50/50 BIL", message["Subject"])
        self.assertIn("TQQQ 25.00% / BIL 75.00%", message["Subject"])
        self.assertEqual(message.get_content().splitlines(), [
            "TQQQ 25.00% / BIL 75.00%", "下一交易日开盘 · 按新目标调仓", "信号来自美东 2026-11-25 收盘",
        ])
        self.assertEqual(list(message.iter_attachments()), [])

    def test_unchanged_target_still_includes_both_weights_and_sums_to_100_percent(self):
        data = {
            "strategy_id": STRATEGY_ID, "end_date": "2026-11-27", "next_action": "延续目标仓位",
            "target_weights": {"TQQQ": .22395, "BIL": .77605},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "summary.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            msg = build_message(path, "from@example.com", ["to@example.com"], build_result="success", deploy_result="success")
        self.assertIn("TQQQ 22.40% / BIL 77.60% · 延续目标仓位", msg["Subject"])
        self.assertIn("下一交易日开盘 · 延续目标仓位", msg.get_content())

    def test_failed_build_does_not_reuse_old_report(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = {"strategy_id": STRATEGY_ID, "end_date": "2026-11-25"}
            (path / "summary.json").write_text(json.dumps(data), encoding="utf-8")
            msg = build_message(path, "from@example.com", ["to@example.com"], build_result="failure", deploy_result="skipped")
        self.assertIn("不可用", msg["Subject"])
        self.assertNotIn("2026-11-25", msg.get_body(preferencelist=("plain",)).get_content())
        self.assertEqual(list(msg.iter_attachments()), [])

    def test_gold_version_report_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            data = {"strategy_id": "qqq-tqqq-5050", "end_date": "2026-11-25"}
            (path / "summary.json").write_text(json.dumps(data), encoding="utf-8")
            msg = build_message(path, "from@example.com", ["to@example.com"], build_result="success", deploy_result="success")
        self.assertIn("不可用", msg["Subject"])
        self.assertEqual(list(msg.iter_attachments()), [])

    def test_successful_build_requires_valid_target_in_dry_run(self):
        data = {
            "strategy_id": STRATEGY_ID, "end_date": "2026-11-27", "next_action": "延续目标仓位",
            "target_weights": {"TQQQ": .25, "BIL": .70},
        }
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            (path / "summary.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            with patch.dict("os.environ", {"BUILD_RESULT": "success"}, clear=True):
                with self.assertRaisesRegex(RuntimeError, "目标仓位"):
                    send_email(path, dry_run=True)


if __name__ == "__main__":
    unittest.main()
