import unittest

import pandas as pd

from synthetic_history import (
    build_extended_market_data,
    modeled_tqqq_daily_returns,
    prepare_treasury_rates,
)


class SyntheticHistoryTests(unittest.TestCase):
    def test_rates_forward_fill_to_trading_days(self):
        rates = pd.DataFrame(
            {"observation_date": ["2020-01-02", "2020-01-06"], "DGS3MO": [1.5, 1.6]}
        )
        aligned = prepare_treasury_rates(
            rates, pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
        )
        self.assertEqual(aligned["yield_percent"].tolist(), [1.5, 1.5, 1.6])

    def test_modeled_tqqq_uses_daily_three_times_return(self):
        qqq = pd.DataFrame(
            {
                "trade_date": pd.date_range("2020-01-01", periods=3, freq="B"),
                "close": [100.0, 101.0, 99.99],
            }
        )
        rates = pd.DataFrame(
            {"trade_date": qqq["trade_date"], "yield_percent": [0.0, 0.0, 0.0]}
        )
        modeled = modeled_tqqq_daily_returns(
            qqq,
            rates,
            tqqq_expense_ratio=0.0,
            qqq_expense_ratio=0.0,
            financing_spread=0.0,
        )
        self.assertAlmostEqual(modeled.iloc[1]["modeled_return"], 0.03)
        self.assertAlmostEqual(modeled.iloc[2]["modeled_return"], -0.03)

    def test_extended_data_has_no_join_price_jump(self):
        dates = pd.date_range("2020-01-01", periods=8, freq="B")
        qqq = pd.DataFrame(
            {"trade_date": dates, "open": [100.0] * 8, "close": [100.0] * 8}
        )
        tqqq = pd.DataFrame(
            {"trade_date": dates[5:], "open": [50.0] * 3, "close": [50.0] * 3}
        )
        bil = pd.DataFrame(
            {"trade_date": dates[4:], "open": [25.0] * 4, "close": [25.0] * 4}
        )
        rates = pd.DataFrame(
            {"trade_date": dates, "yield_percent": [1.0] * len(dates)}
        )
        extended_tqqq, extended_bil, _ = build_extended_market_data(
            qqq, tqqq, bil, rates
        )
        synthetic_tqqq = extended_tqqq[extended_tqqq["is_synthetic"]]
        synthetic_bil = extended_bil[extended_bil["is_synthetic"]]
        self.assertAlmostEqual(synthetic_tqqq.iloc[-1]["close"], 50.0)
        self.assertAlmostEqual(synthetic_bil.iloc[-1]["close"], 25.0)
        self.assertEqual(extended_tqqq.iloc[0]["trade_date"], dates[0])


if __name__ == "__main__":
    unittest.main()
