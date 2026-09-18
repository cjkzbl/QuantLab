import unittest

import pandas as pd

from risk_analysis import (
    _run_lump_sum_path,
    _run_strategy_path,
    first_trading_days,
    path_metrics,
    summarize_rolling_results,
    xirr,
)
from strategy import backtest_qqq_sma_tqqq, prepare_backtest_data


def daily_from_values(dates, strategy_values, qqq_values=None):
    qqq_values = qqq_values or strategy_values
    strategy = pd.Series(strategy_values, dtype=float)
    benchmark = pd.Series(qqq_values, dtype=float)
    return pd.DataFrame(
        {
            "trade_date": pd.to_datetime(dates),
            "daily_return": strategy.pct_change().fillna(0.0),
            "qqq_benchmark_daily_return": benchmark.pct_change().fillna(0.0),
        }
    )


class RollingStartRiskTests(unittest.TestCase):
    def test_first_trading_day_for_each_month(self):
        dates = pd.to_datetime(
            ["2024-01-03", "2024-01-02", "2024-01-05", "2024-02-02", "2024-02-01"]
        )
        self.assertEqual(
            first_trading_days(dates),
            [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-02-01")],
        )

    def test_max_drawdown_uses_time_weighted_nav(self):
        daily = daily_from_values(
            pd.date_range("2024-01-01", periods=5, freq="B"),
            [100, 120, 90, 110, 130],
        )
        metrics = path_metrics(daily)
        self.assertAlmostEqual(metrics["max_drawdown"], -0.25)

    def test_metrics_do_not_read_past_end_date(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        daily = daily_from_values(dates, [100, 110, 120, 10, 1000])
        metrics = path_metrics(daily, end_date=dates[2])
        self.assertAlmostEqual(metrics["total_return"], 0.20)
        self.assertAlmostEqual(metrics["max_drawdown"], 0.0)

    def test_recovery_and_underwater_days(self):
        dates = pd.date_range("2024-01-01", periods=5, freq="B")
        daily = daily_from_values(dates, [100, 120, 90, 110, 130])
        metrics = path_metrics(daily)
        self.assertTrue(metrics["recovered_within_window"])
        self.assertEqual(metrics["recovery_days"], 3)
        self.assertEqual(metrics["max_underwater_days"], 2)
        unrecovered = path_metrics(daily.iloc[:-1])
        self.assertFalse(unrecovered["recovered_within_window"])
        self.assertTrue(pd.isna(unrecovered["recovery_days"]))

    def test_xirr_for_one_year_gain(self):
        value = xirr(
            [(pd.Timestamp("2020-01-01"), -100), (pd.Timestamp("2021-01-01"), 110)]
        )
        self.assertAlmostEqual(value, 0.10, places=2)

    def test_summary_reports_probabilities(self):
        results = pd.DataFrame(
            {
                "horizon_years": [1, 1, 1, 1],
                "display_return": [-0.1, 0.1, 0.2, 0.3],
                "max_drawdown": [-0.4, -0.3, -0.2, -0.1],
                "positive_return": [False, True, True, True],
                "outperformed_qqq": [False, False, True, True],
                "recovered_within_window": [False, True, True, True],
                "recovery_days": [float("nan"), 20, 30, 40],
                "max_underwater_days": [100, 20, 30, 40],
                "money_weighted_return": [-0.1, 0.1, 0.2, 0.3],
            }
        )
        summary = summarize_rolling_results(results).iloc[0]
        self.assertEqual(summary["sample_count"], 4)
        self.assertAlmostEqual(summary["positive_rate"], 0.75)
        self.assertAlmostEqual(summary["outperformance_rate"], 0.5)

    def test_fast_lump_sum_path_matches_full_backtest(self):
        dates = pd.date_range("2020-01-01", periods=18, freq="B")
        prices = [100, 100, 100, 90, 88, 120, 121, 118, 116, 80, 78, 115, 116, 110, 108, 70, 72, 112]
        tqqq_prices = [30, 30, 30, 25, 24, 36, 37, 34, 33, 20, 19, 35, 36, 32, 31, 17, 18, 34]
        bil_prices = [100 + index * 0.01 for index in range(len(dates))]
        qqq = pd.DataFrame({"trade_date": dates, "open": prices, "close": prices})
        tqqq = pd.DataFrame({"trade_date": dates, "open": tqqq_prices, "close": tqqq_prices})
        bil = pd.DataFrame({"trade_date": dates, "open": bil_prices, "close": bil_prices})
        ordinary, _, _ = backtest_qqq_sma_tqqq(
            qqq, tqqq, bil, monthly_contribution=0, sma_window=3
        )
        prepared = prepare_backtest_data(qqq, tqqq, bil, sma_window=3)
        fast = _run_lump_sum_path(prepared, dates[0])
        pd.testing.assert_series_equal(
            ordinary["daily_return"].reset_index(drop=True),
            fast["daily_return"],
            check_names=False,
        )

    def test_fast_dca_path_matches_full_backtest(self):
        dates = pd.date_range("2020-01-01", periods=90, freq="B")
        prices = ([100, 100, 100, 90, 88, 120, 121, 118, 116, 80, 78, 115, 116, 110, 108, 70, 72, 112] * 5)[: len(dates)]
        tqqq_prices = [value * 0.3 for value in prices]
        bil_prices = [100 + index * 0.01 for index in range(len(dates))]
        qqq = pd.DataFrame({"trade_date": dates, "open": prices, "close": prices})
        tqqq = pd.DataFrame({"trade_date": dates, "open": tqqq_prices, "close": tqqq_prices})
        bil = pd.DataFrame({"trade_date": dates, "open": bil_prices, "close": bil_prices})
        ordinary, _, _ = backtest_qqq_sma_tqqq(
            qqq, tqqq, bil, monthly_contribution=10_000, sma_window=3
        )
        prepared = prepare_backtest_data(qqq, tqqq, bil, sma_window=3)
        fast = _run_strategy_path(
            prepared, dates[0], monthly_contribution=10_000
        )
        pd.testing.assert_series_equal(
            ordinary["daily_return"].reset_index(drop=True),
            fast["daily_return"],
            check_names=False,
        )
        pd.testing.assert_series_equal(
            ordinary["total_value"].reset_index(drop=True),
            fast["total_value"],
            check_names=False,
        )
        lump_sum = _run_lump_sum_path(prepared, dates[0])
        self.assertNotEqual(fast.iloc[-1]["total_value"], lump_sum.iloc[-1]["total_value"])
        pd.testing.assert_series_equal(
            ordinary["qqq_benchmark_daily_return"].reset_index(drop=True),
            fast["qqq_benchmark_daily_return"],
            check_names=False,
        )


if __name__ == "__main__":
    unittest.main()
