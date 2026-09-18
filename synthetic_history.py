"""为 TQQQ/BIL 上市前区间构造明确标记的合成历史。"""

from __future__ import annotations

import pandas as pd


TRADING_DAYS = 252.0
TQQQ_EXPENSE_RATIO = 0.0095
QQQ_EXPENSE_RATIO = 0.0020
FINANCING_SPREAD = 0.0050


def prepare_treasury_rates(rate_df, dates):
    """把 FRED DGS3MO 年化百分比收益率对齐到交易日。"""
    rates = rate_df.copy()
    date_column = "trade_date" if "trade_date" in rates else "observation_date"
    value_column = "yield_percent" if "yield_percent" in rates else "DGS3MO"
    rates = rates[[date_column, value_column]].rename(
        columns={date_column: "trade_date", value_column: "yield_percent"}
    )
    rates["trade_date"] = pd.to_datetime(rates["trade_date"])
    rates["yield_percent"] = pd.to_numeric(rates["yield_percent"], errors="coerce")
    rates = rates.dropna().sort_values("trade_date").drop_duplicates("trade_date")
    target = pd.DataFrame({"trade_date": pd.to_datetime(dates)}).sort_values(
        "trade_date"
    )
    aligned = pd.merge_asof(target, rates, on="trade_date", direction="backward")
    aligned["yield_percent"] = aligned["yield_percent"].bfill().ffill()
    if aligned["yield_percent"].isna().any():
        raise ValueError("三个月期国债收益率无法覆盖合成历史日期")
    return aligned


def modeled_tqqq_daily_returns(
    qqq_df,
    rate_df,
    tqqq_expense_ratio=TQQQ_EXPENSE_RATIO,
    qqq_expense_ratio=QQQ_EXPENSE_RATIO,
    financing_spread=FINANCING_SPREAD,
):
    """根据 QQQ 总回报和融资成本估算 TQQQ 的每日净收益。"""
    qqq = qqq_df[["trade_date", "close"]].copy()
    qqq["trade_date"] = pd.to_datetime(qqq["trade_date"])
    qqq = qqq.sort_values("trade_date").drop_duplicates("trade_date")
    rates = prepare_treasury_rates(rate_df, qqq["trade_date"])
    qqq_return = qqq["close"].astype(float).pct_change().fillna(0.0)
    base_fee_adjustment = 3.0 * qqq_expense_ratio / TRADING_DAYS
    fund_fee = tqqq_expense_ratio / TRADING_DAYS
    financing = 2.0 * (
        rates["yield_percent"].to_numpy() / 100.0 + financing_spread
    ) / TRADING_DAYS
    modeled = 3.0 * qqq_return + base_fee_adjustment - fund_fee - financing
    return pd.DataFrame(
        {
            "trade_date": qqq["trade_date"].to_numpy(),
            "modeled_return": modeled.clip(lower=-0.99).to_numpy(),
            "yield_percent": rates["yield_percent"].to_numpy(),
        }
    )


def build_synthetic_tqqq(qqq_df, real_tqqq_df, rate_df):
    """生成 TQQQ 上市前行情，并与真实行情无跳点拼接。"""
    real = real_tqqq_df.copy()
    real["trade_date"] = pd.to_datetime(real["trade_date"])
    real = real.sort_values("trade_date").drop_duplicates("trade_date")
    first_real_date = pd.Timestamp(real["trade_date"].min())
    qqq = qqq_df[["trade_date", "open", "close"]].copy()
    qqq["trade_date"] = pd.to_datetime(qqq["trade_date"])
    qqq = qqq.sort_values("trade_date").drop_duplicates("trade_date")
    pre = qqq[qqq["trade_date"] < first_real_date].reset_index(drop=True)
    modeled = modeled_tqqq_daily_returns(pre, rate_df)
    raw_close = (1.0 + modeled["modeled_return"]).cumprod() * 100.0
    raw_open = []
    for index, row in pre.iterrows():
        if index == 0:
            raw_open.append(float(raw_close.iloc[index]))
            continue
        prior_qqq_close = float(pre.at[index - 1, "close"])
        overnight_return = float(row["open"]) / prior_qqq_close - 1.0
        raw_open.append(
            float(raw_close.iloc[index - 1]) * max(0.01, 1.0 + 3.0 * overnight_return)
        )
    anchor = float(real.iloc[0]["open"])
    scale = anchor / float(raw_close.iloc[-1])
    synthetic = pd.DataFrame(
        {
            "trade_date": pre["trade_date"],
            "open": pd.Series(raw_open) * scale,
            "close": raw_close * scale,
            "is_synthetic": True,
        }
    )
    real["is_synthetic"] = False
    return pd.concat([synthetic, real], ignore_index=True, sort=False)


def build_synthetic_bil(qqq_df, real_bil_df, rate_df):
    """用三个月期国债收益率生成 BIL 上市前的现金代理。"""
    real = real_bil_df.copy()
    real["trade_date"] = pd.to_datetime(real["trade_date"])
    real = real.sort_values("trade_date").drop_duplicates("trade_date")
    first_real_date = pd.Timestamp(real["trade_date"].min())
    qqq_dates = pd.to_datetime(qqq_df["trade_date"])
    pre_dates = qqq_dates[qqq_dates < first_real_date].drop_duplicates().sort_values()
    rates = prepare_treasury_rates(rate_df, pre_dates)
    daily_return = (1.0 + rates["yield_percent"] / 100.0) ** (1.0 / TRADING_DAYS) - 1.0
    raw_close = (1.0 + daily_return).cumprod() * 100.0
    raw_open = raw_close.shift(1)
    raw_open.iloc[0] = raw_close.iloc[0] / (1.0 + daily_return.iloc[0])
    anchor = float(real.iloc[0]["open"])
    scale = anchor / float(raw_close.iloc[-1])
    synthetic = pd.DataFrame(
        {
            "trade_date": pre_dates.to_numpy(),
            "open": raw_open.to_numpy() * scale,
            "close": raw_close.to_numpy() * scale,
            "is_synthetic": True,
        }
    )
    real["is_synthetic"] = False
    return pd.concat([synthetic, real], ignore_index=True, sort=False)


def validate_synthetic_tqqq(qqq_df, real_tqqq_df, rate_df):
    """在真实 TQQQ 区间衡量模型的日收益拟合误差。"""
    qqq = qqq_df[["trade_date", "close"]].copy()
    qqq["trade_date"] = pd.to_datetime(qqq["trade_date"])
    real = real_tqqq_df[["trade_date", "close"]].copy()
    real["trade_date"] = pd.to_datetime(real["trade_date"])
    overlap = qqq.merge(real, on="trade_date", suffixes=("_qqq", "_tqqq"))
    modeled = modeled_tqqq_daily_returns(
        overlap[["trade_date", "close_qqq"]].rename(columns={"close_qqq": "close"}),
        rate_df,
    )
    actual_return = overlap["close_tqqq"].astype(float).pct_change()
    comparison = pd.DataFrame(
        {
            "actual": actual_return,
            "modeled": modeled["modeled_return"],
        }
    ).dropna()
    return {
        "start_date": pd.Timestamp(overlap.iloc[0]["trade_date"]),
        "end_date": pd.Timestamp(overlap.iloc[-1]["trade_date"]),
        "observations": int(len(comparison)),
        "daily_correlation": float(comparison.corr().iloc[0, 1]),
        "daily_mae": float((comparison["actual"] - comparison["modeled"]).abs().mean()),
        "annualized_tracking_difference": float(
            ((1.0 + comparison["modeled"]).prod() / (1.0 + comparison["actual"]).prod())
            ** (TRADING_DAYS / len(comparison))
            - 1.0
        ),
    }


def build_extended_market_data(qqq_df, tqqq_df, bil_df, rate_df):
    return (
        build_synthetic_tqqq(qqq_df, tqqq_df, rate_df),
        build_synthetic_bil(qqq_df, bil_df, rate_df),
        validate_synthetic_tqqq(qqq_df, tqqq_df, rate_df),
    )
