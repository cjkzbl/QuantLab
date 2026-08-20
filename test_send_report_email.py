import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from send_report_email import send_email


class DailyEmailTests(unittest.TestCase):
    def test_email_contains_close_indicators_and_next_day_action(self):
        summary = {
            "end_date": "2026-08-19",
            "sma_window": 200,
            "bull_multiplier": 1.04,
            "bear_multiplier": 0.97,
            "dip_threshold": 0.01,
            "latest_signal": "bull",
            "latest_qqq_close": 600.0,
            "latest_sma": 550.0,
            "latest_qqq_daily_change": -0.012,
            "latest_tqqq_daily_change": -0.035,
            "latest_bil_daily_change": 0.0001,
            "latest_daily_profit": -350.0,
            "latest_daily_return": -0.007,
            "today_action_text": "今日未交易",
            "next_action": "buy",
            "next_action_text": "用全部现金买入 TQQQ",
            "next_action_reason": "牛市中 QQQ 单日跌幅达到回调阈值",
            "final_value": 100000.0,
            "qqq_benchmark_final_value": 80000.0,
            "total_contributions": 50000.0,
            "return_rate": 1.0,
            "max_drawdown": -0.25,
            "current_position": "TQQQ",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            report_dir = Path(temp_dir)
            (report_dir / "summary.json").write_text(
                json.dumps(summary, ensure_ascii=False), encoding="utf-8"
            )
            environment = {
                "BUILD_RESULT": "success",
                "DEPLOY_RESULT": "success",
                "WORKFLOW_URL": "https://example.com/workflow",
            }
            with patch.dict(os.environ, environment, clear=False):
                message = send_email(report_dir, dry_run=True)

        plain = message.get_body(preferencelist=("plain",)).get_content()
        rich = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("牛市｜下一交易日：用全部现金买入 TQQQ", message["Subject"])
        self.assertIn("【前一交易日收盘指标】", plain)
        self.assertIn("牛市确认线（SMA×1.04）：572.00", plain)
        self.assertIn("熊市确认线（SMA×0.97）：533.50", plain)
        self.assertIn("BIL 单日涨跌：+0.01%", plain)
        self.assertIn("【下一交易日操作】", plain)
        self.assertIn("用全部现金买入 TQQQ", rich)
        self.assertIn("执行时间：下一交易日开盘", rich)


if __name__ == "__main__":
    unittest.main()
