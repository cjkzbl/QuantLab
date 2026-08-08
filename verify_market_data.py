import argparse

from get_data import load_data, validate_market_data


def main():
    parser = argparse.ArgumentParser(description="校验 QQQ/TQQQ CSV 是否最新")
    parser.add_argument("--max-age-days", type=int, default=7)
    args = parser.parse_args()

    result = validate_market_data(
        load_data("qqq_daily.csv"),
        load_data("tqqq_daily.csv"),
        max_age_days=args.max_age_days,
    )
    print(
        "CSV 校验通过："
        f"最新交易日={result['latest_date']}，"
        f"距今天={result['age_days']} 天，"
        f"QQQ={result['qqq_rows']} 行，TQQQ={result['tqqq_rows']} 行"
    )


if __name__ == "__main__":
    main()
