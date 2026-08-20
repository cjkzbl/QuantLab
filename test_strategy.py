import unittest

import pandas as pd

from strategy import backtest_qqq_sma_tqqq, market_regimes


class MarketRegimeTests(unittest.TestCase):
    def test_buffer_keeps_previous_regime(self):
        closes = pd.Series([105.0, 100.0, 96.0, 100.0, 105.0])
        smas = pd.Series([100.0] * len(closes))

        regimes = market_regimes(closes, smas, 1.04, 0.97)

        self.assertEqual(regimes.tolist(), ["bull", "bull", "bear", "bear", "bull"])

    def test_bear_to_bull_buys_on_next_open(self):
        dates = pd.date_range("2020-01-01", periods=203, freq="D")
        qqq_closes = [100.0] * 200 + [95.0, 106.0, 106.0]
        qqq = pd.DataFrame(
            {"trade_date": dates, "open": qqq_closes, "close": qqq_closes}
        )
        tqqq = pd.DataFrame(
            {"trade_date": dates, "open": [10.0] * 203, "close": [10.0] * 203}
        )

        daily, trades, summary = backtest_qqq_sma_tqqq(
            qqq,
            tqqq,
            monthly_contribution=0,
            commission_rate=0,
            slippage_rate=0,
            sell_fee_rate=0,
        )

        self.assertEqual(daily.iloc[0]["cash"], 10_000)
        self.assertTrue(trades.iloc[:1]["reason"].str.contains("熊转牛").all())
        self.assertEqual(trades.iloc[0]["signal_date"], dates[201])
        self.assertEqual(trades.iloc[0]["trade_date"], dates[202])
        self.assertEqual(summary["initial_capital"], 10_000)


if __name__ == "__main__":
    unittest.main()
