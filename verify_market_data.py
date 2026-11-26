import argparse
from pathlib import Path

from allocation_strategy import validate_prices
from get_data import STRATEGY_DATA_DIR, load_strategy_market_data


def main():
    parser = argparse.ArgumentParser(description="校验 50/50 策略的四只 ETF 行情")
    parser.add_argument("--data-dir", type=Path, default=STRATEGY_DATA_DIR)
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()

    prices = validate_prices(load_strategy_market_data(args.data_dir), max_age_days=args.max_age_days)
    print(f"行情校验通过，最新共同交易日 {prices['QQQ'].index[-1]:%Y-%m-%d}")


if __name__ == "__main__":
    main()
