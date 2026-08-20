import unittest

import pandas as pd

from get_data import validate_market_data


class MarketDataValidationTests(unittest.TestCase):
    def test_three_etfs_must_share_latest_trade_date(self):
        dates = pd.to_datetime(["2026-08-18", "2026-08-19"])
        frame = pd.DataFrame({"trade_date": dates, "close": [1.0, 1.1]})
        result = validate_market_data(
            frame, frame.copy(), frame.copy(), max_age_days=None, today="2026-08-20"
        )
        self.assertEqual(result["latest_date"], "2026-08-19")
        self.assertEqual(result["bil_rows"], 2)

        stale_bil = frame.iloc[:1].copy()
        with self.assertRaisesRegex(ValueError, "最新交易日不一致"):
            validate_market_data(
                frame,
                frame.copy(),
                stale_bil,
                max_age_days=None,
                today="2026-08-20",
            )


if __name__ == "__main__":
    unittest.main()
