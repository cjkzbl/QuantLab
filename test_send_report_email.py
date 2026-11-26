import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from allocation_strategy import STRATEGY_ID
from send_report_email import build_message, send_email


class DailyEmailTests(unittest.TestCase):
    def test_dry_run_uses_the_shared_allocation_and_attaches_the_ledger(self):
        summary = {
            "strategy_id": STRATEGY_ID, "end_date": "2026-11-25", "next_action": "按新目标调仓",
            "current_weights": {"TQQQ": .2, "BIL": .8},
            "target_weights": {"TQQQ": .25, "BIL": .75},
            "latest_changes": {"QQQ": .01, "TQQQ": .03, "BIL": .0001},
            "trend_label": "防守", "qqq_close": 500., "qqq_sma": 525., "qqq_entry_line": 546.,
            "active_volatility": .6, "volatility_sample_date": "2026-11-02", "volatility_tqqq_ratio": .50,
            "latest_daily_return": -.007, "latest_daily_profit": -350., "latest_trade_count": 2,
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

        plain = message.get_body(preferencelist=("plain",)).get_content()
        rich = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("50/50 BIL", message["Subject"])
        self.assertIn("BIL | 80.00% | 75.00% | +0.01%", plain)
        self.assertIn("取自 2026-11-02 收盘", plain)
        self.assertIn("开盘目标", rich)
        self.assertIn("75.00%", rich)
        attachment = next(message.iter_attachments())
        with zipfile.ZipFile(io.BytesIO(attachment.get_payload(decode=True))) as archive:
            self.assertEqual(set(archive.namelist()), {"summary.json", "trade_details.csv"})

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


if __name__ == "__main__":
    unittest.main()
