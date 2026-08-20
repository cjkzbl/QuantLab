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
        dates = pd.date_range("2020-01-01", periods=205, freq="D")
        qqq_closes = [100.0] * 200 + [95.0, 106.0, 106.0, 90.0, 90.0]
        qqq = pd.DataFrame(
            {"trade_date": dates, "open": qqq_closes, "close": qqq_closes}
        )
        tqqq = pd.DataFrame(
            {"trade_date": dates, "open": [10.0] * 205, "close": [10.0] * 205}
        )
        bil = pd.DataFrame(
            {"trade_date": dates, "open": [100.0] * 205, "close": [100.0] * 205}
        )

        daily, trades, summary = backtest_qqq_sma_tqqq(
            qqq,
            tqqq,
            bil,
            monthly_contribution=0,
            commission_rate=0,
            qqq_slippage_rate=0,
            tqqq_slippage_rate=0,
            bil_slippage_rate=0,
            sell_fee_rate=0,
        )

        self.assertEqual(daily.iloc[0]["position_status"], "BIL")
        self.assertGreater(daily.iloc[0]["bil_shares"], 0)
        tqqq_buys = trades[(trades["asset"] == "TQQQ") & (trades["side"] == "buy")]
        self.assertTrue(tqqq_buys.iloc[:1]["reason"].str.contains("熊转牛").all())
        self.assertEqual(tqqq_buys.iloc[0]["signal_date"], dates[201])
        self.assertEqual(tqqq_buys.iloc[0]["trade_date"], dates[202])
        tqqq_sells = trades[(trades["asset"] == "TQQQ") & (trades["side"] == "sell")]
        self.assertEqual(tqqq_sells.iloc[0]["trade_date"], dates[204])
        self.assertEqual(daily.iloc[-1]["position_status"], "BIL")
        self.assertEqual(summary["initial_capital"], 10_000)


if __name__ == "__main__":
    unittest.main()
