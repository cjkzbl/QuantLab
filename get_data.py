import os
from pathlib import Path

import pandas as pd
from quantdash import QuantDash


def _get_daily_data(symbol):
    """获取指定美股标的从上市至今的后复权日线行情。"""
    api_key = os.getenv("QUANTDASH_API_KEY")
    if not api_key:
        api_key = Path("api.txt").read_text(encoding="utf-8").strip()
    qd = QuantDash(api_key=api_key)
    return qd.klines.get(
        symbol,
        period="1d",
        count=10000,
        adjust="backward",
        to_dataframe=True,
    )


def get_qqq_data():
    """获取 QQQ 从上市至今的后复权日线行情。"""
    return _get_daily_data("QQQ.US")


def get_tqqq_data():
    """获取 TQQQ 从上市至今的后复权日线行情。"""
    return _get_daily_data("TQQQ.US")


def save_data(df, filename):
    """把行情 DataFrame 保存为 UTF-8 CSV 文件。"""
    path = Path(filename)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_data(filename):
    """从 CSV 文件读取行情 DataFrame。"""
    return pd.read_csv(filename, encoding="utf-8-sig")


if __name__ == "__main__":
    save_data(get_qqq_data(), "qqq_daily.csv")
    save_data(get_tqqq_data(), "tqqq_daily.csv")
    print("已保存 qqq_daily.csv 和 tqqq_daily.csv")
