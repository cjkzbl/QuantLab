import os
from datetime import date
from pathlib import Path

import pandas as pd
from quantdash import QuantDash


def _get_daily_data(symbol):
    """获取指定美股标的从上市至今的后复权日线行情。"""
    api_key = os.getenv("QUANTDASH_API_KEY")
    if not api_key:
        api_key_path = Path("api.txt")
        if not api_key_path.exists():
            raise RuntimeError("缺少 QUANTDASH_API_KEY 环境变量或 api.txt")
        api_key = api_key_path.read_text(encoding="utf-8").strip()
    if not api_key:
        raise RuntimeError("QuantDash API Key 不能为空")

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


def get_bil_data():
    """获取 BIL 从上市至今的后复权日线行情。"""
    return _get_daily_data("BIL.US")


def save_data(df, filename):
    """把行情 DataFrame 保存为 UTF-8 CSV 文件。"""
    path = Path(filename)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_data(filename):
    """从 CSV 文件读取行情 DataFrame。"""
    return pd.read_csv(filename, encoding="utf-8-sig")


def validate_market_data(qqq_df, tqqq_df, bil_df=None, max_age_days=7, today=None):
    """确认行情完整、最新日期一致，并且没有过期。"""
    frames = {"QQQ": qqq_df, "TQQQ": tqqq_df}
    if bil_df is not None:
        frames["BIL"] = bil_df
    details = {}

    for symbol, frame in frames.items():
        if frame.empty:
            raise ValueError(f"{symbol} 行情为空")
        if "trade_date" not in frame.columns:
            raise ValueError(f"{symbol} 行情缺少 trade_date 列")

        dates = pd.to_datetime(frame["trade_date"], errors="coerce").dt.normalize()
        if dates.isna().any():
            raise ValueError(f"{symbol} 行情包含无法解析的交易日期")
        if dates.duplicated().any():
            raise ValueError(f"{symbol} 行情包含重复交易日期")
        if not dates.is_monotonic_increasing:
            raise ValueError(f"{symbol} 行情没有按交易日期升序排列")

        details[symbol] = {
            "rows": int(len(frame)),
            "start_date": dates.iloc[0],
            "latest_date": dates.iloc[-1],
        }

    qqq_latest = details["QQQ"]["latest_date"]
    latest_dates = {symbol: item["latest_date"] for symbol, item in details.items()}
    if len(set(latest_dates.values())) != 1:
        detail_text = "，".join(
            f"{symbol}={latest:%Y-%m-%d}" for symbol, latest in latest_dates.items()
        )
        raise ValueError(
            f"QQQ、TQQQ 与 BIL 最新交易日不一致：{detail_text}"
        )

    reference_date = pd.Timestamp(today or date.today()).normalize()
    age_days = int((reference_date - qqq_latest).days)
    if age_days < 0:
        raise ValueError("行情最新交易日晚于当前日期")
    if max_age_days is not None and age_days > max_age_days:
        raise ValueError(
            f"行情已经过期：最新交易日 {qqq_latest:%Y-%m-%d}，"
            f"距离当前日期 {age_days} 天，允许最多 {max_age_days} 天"
        )

    result = {
        "latest_date": qqq_latest.strftime("%Y-%m-%d"),
        "age_days": age_days,
        "qqq_rows": details["QQQ"]["rows"],
        "tqqq_rows": details["TQQQ"]["rows"],
    }
    if "BIL" in details:
        result["bil_rows"] = details["BIL"]["rows"]
    return result


def save_market_data_pair(
    qqq_df,
    tqqq_df,
    qqq_filename="qqq_daily.csv",
    tqqq_filename="tqqq_daily.csv",
    max_age_days=7,
    today=None,
):
    """校验后写入临时文件，再替换两份正式 CSV，并重新读取确认。"""
    expected = validate_market_data(
        qqq_df,
        tqqq_df,
        max_age_days=max_age_days,
        today=today,
    )
    qqq_path = Path(qqq_filename)
    tqqq_path = Path(tqqq_filename)
    qqq_temp = qqq_path.with_name(f".{qqq_path.name}.tmp")
    tqqq_temp = tqqq_path.with_name(f".{tqqq_path.name}.tmp")

    try:
        save_data(qqq_df, qqq_temp)
        save_data(tqqq_df, tqqq_temp)
        saved = validate_market_data(
            load_data(qqq_temp),
            load_data(tqqq_temp),
            max_age_days=max_age_days,
            today=today,
        )
        if saved != expected:
            raise RuntimeError("CSV 写入后校验结果与下载数据不一致")
        qqq_temp.replace(qqq_path)
        tqqq_temp.replace(tqqq_path)
    finally:
        qqq_temp.unlink(missing_ok=True)
        tqqq_temp.unlink(missing_ok=True)

    return validate_market_data(
        load_data(qqq_path),
        load_data(tqqq_path),
        max_age_days=max_age_days,
        today=today,
    )


def refresh_and_save_market_data(max_age_days=7):
    """同时下载三只 ETF，验证后持久化到正式 CSV。"""
    qqq = get_qqq_data()
    tqqq = get_tqqq_data()
    bil = get_bil_data()
    expected = validate_market_data(
        qqq, tqqq, bil, max_age_days=max_age_days
    )
    paths = {
        "QQQ": Path("qqq_daily.csv"),
        "TQQQ": Path("tqqq_daily.csv"),
        "BIL": Path("bil_daily.csv"),
    }
    frames = {"QQQ": qqq, "TQQQ": tqqq, "BIL": bil}
    temp_paths = {
        symbol: path.with_name(f".{path.name}.tmp") for symbol, path in paths.items()
    }
    try:
        for symbol, frame in frames.items():
            save_data(frame, temp_paths[symbol])
        saved = validate_market_data(
            load_data(temp_paths["QQQ"]),
            load_data(temp_paths["TQQQ"]),
            load_data(temp_paths["BIL"]),
            max_age_days=max_age_days,
        )
        if saved != expected:
            raise RuntimeError("CSV 写入后校验结果与下载数据不一致")
        for symbol, path in paths.items():
            temp_paths[symbol].replace(path)
    finally:
        for temp_path in temp_paths.values():
            temp_path.unlink(missing_ok=True)
    return validate_market_data(
        load_data(paths["QQQ"]),
        load_data(paths["TQQQ"]),
        load_data(paths["BIL"]),
        max_age_days=max_age_days,
    )


if __name__ == "__main__":
    result = refresh_and_save_market_data()
    print(
        "行情已更新并校验："
        f"交易日={result['latest_date']}，"
        f"QQQ={result['qqq_rows']} 行，TQQQ={result['tqqq_rows']} 行，"
        f"BIL={result['bil_rows']} 行"
    )
