import unittest

import numpy as np
import pandas as pd

from allocation_strategy import backtest_allocation, daily_summary, prepare_allocation, validate_prices


def sample_prices():
    dates = pd.bdate_range(end="2025-02-05", periods=240)
    qqq = pd.Series(100., index=dates)
    qqq.loc["2025-01-28"] = 107.
    qqq.loc["2025-01-29"] = 101.
    qqq.loc["2025-01-30"] = 98.
    qqq.loc["2025-01-31"] = 102.
    qqq.loc["2025-02-03"] = 108.
    values = {"QQQ": qqq.to_numpy(), "TQQQ": 70 * np.cumprod(1 + np.tile([-.035, .033, .015], 80)),
              "BIL": np.full(len(dates), 100.)}
    return {asset: pd.DataFrame({"trade_date": dates, "open": close, "close": close}) for asset, close in values.items()}


class AllocationTests(unittest.TestCase):
    def test_trend_retains_state_and_executes_on_the_following_open(self):
        prepared = prepare_allocation(sample_prices())
        daily, trades = backtest_allocation(prepared, "2025-01-28")
        daily = daily.set_index("trade_date")
        self.assertEqual(daily.loc["2025-01-28", "target_tqqq"], 0.)
        self.assertEqual(daily.loc["2025-01-29", "trend_state"], "bull")
        self.assertGreater(daily.loc["2025-01-29", "target_tqqq"], .5)
        self.assertEqual(daily.loc["2025-01-31", "trend_state"], "bear")
        self.assertLess(daily.loc["2025-01-31", "target_tqqq"], .5)
        np.testing.assert_allclose(daily.target_bil, 1 - daily.target_tqqq)
        self.assertEqual(set(trades.asset), {"TQQQ", "BIL"})
        self.assertTrue((trades.dropna(subset=["signal_date"]).signal_date < trades.dropna(subset=["signal_date"]).trade_date).all())

    def test_first_monthly_deposit_executes_the_previous_close_plan(self):
        prepared = prepare_allocation(sample_prices())
        daily, _ = backtest_allocation(prepared, "2025-01-28")
        daily = daily.set_index("trade_date")
        self.assertEqual(daily.loc["2025-02-03", "contribution"], 10_000.)
        self.assertEqual(daily.loc["2025-02-03", "target_tqqq"], daily.loc["2025-01-31", "signal_tqqq"])
        self.assertEqual(daily.loc["2025-02-03", "volatility_sample_date"], pd.Timestamp("2025-02-03"))
        self.assertEqual(daily.loc["2025-02-04", "target_tqqq"], daily.loc["2025-02-03", "signal_tqqq"])
        self.assertEqual(daily.iloc[-1].total_contributions, 20_000.)

    def test_later_prices_do_not_change_the_existing_trade_history(self):
        prices = sample_prices()
        cutoff = pd.Timestamp("2025-02-03")
        prefix = {asset: data[data.trade_date <= cutoff] for asset, data in prices.items()}
        full_daily, full_trades = backtest_allocation(prepare_allocation(prices), "2025-01-28")
        daily, trades = backtest_allocation(prepare_allocation(prefix), "2025-01-28")
        pd.testing.assert_frame_equal(daily, full_daily[full_daily.trade_date <= cutoff])
        pd.testing.assert_frame_equal(trades, full_trades[full_trades.trade_date <= cutoff])
        summary = daily_summary(daily, trades)
        self.assertTrue(summary["target_changed"])
        self.assertAlmostEqual(summary["target_weights"]["TQQQ"], daily.iloc[-1].signal_tqqq)
        self.assertAlmostEqual(sum(summary["target_weights"].values()), 1.)

    def test_missing_session_and_invalid_price_stop_publication(self):
        prices = sample_prices()
        prices["BIL"] = prices["BIL"].drop(index=80)
        with self.assertRaisesRegex(ValueError, "共同交易日"):
            validate_prices(prices)
        prices = sample_prices()
        prices["BIL"].loc[10, "close"] = np.inf
        with self.assertRaisesRegex(ValueError, "开盘／收盘价"):
            validate_prices(prices)


if __name__ == "__main__":
    unittest.main()
