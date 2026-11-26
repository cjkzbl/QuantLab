"""The fixed 50/50 trend and volatility strategy used by Pages and daily email."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from risk_analysis import xirr
from strategy import market_regimes, moving_average


STRATEGY_ID = "qqq-tqqq-5050"
BACKTEST_START = "2010-03-11"
CAPITAL = 10_000.0
SMA_WINDOW = 200
BULL_LINE = 1.04
TREND_SHARE = 0.50
VOL_WINDOW = 20
VOL_TARGET = 0.30
SELL_FEE = 0.0000206
SYMBOLS = ("QQQ", "TQQQ", "BIL", "GLD")
ASSETS = SYMBOLS[1:]
SLIPPAGE = {"QQQ": 0.0001, "TQQQ": 0.0005, "BIL": 0.0001, "GLD": 0.0001}
TREND_LABELS = {"bull": "开启", "bear": "防守", "neutral": "等待确认"}


def validate_prices(frames, *, max_age_days=None, today=None):
    """Normalize the four adjusted-price series and verify their joint sessions."""
    out = {}
    for symbol in SYMBOLS:
        if symbol not in frames or frames[symbol].empty:
            raise ValueError(f"{symbol} 行情为空")
        if not {"trade_date", "open", "close"}.issubset(frames[symbol].columns):
            raise ValueError(f"{symbol} 行情缺少日期或开盘／收盘价")
        frame = frames[symbol][["trade_date", "open", "close"]].copy()
        dates = pd.to_datetime(frame.trade_date, errors="raise").dt.normalize()
        if dates.hasnans or not dates.is_unique or not dates.is_monotonic_increasing:
            raise ValueError(f"{symbol} 行情日期缺失、重复或未按顺序排列")
        prices = frame[["open", "close"]].to_numpy(dtype=float)
        if not np.isfinite(prices).all() or (prices <= 0).any():
            raise ValueError(f"{symbol} 行情包含无效的开盘／收盘价")
        frame["trade_date"] = dates
        frame[["open", "close"]] = prices
        out[symbol] = frame.set_index("trade_date")

    common_start = max(frame.index[0] for frame in out.values())
    calendar = out["QQQ"].loc[common_start:].index
    if calendar.empty or any(not frame.loc[common_start:].index.equals(calendar) for frame in out.values()):
        raise ValueError("QQQ、TQQQ、BIL 和 GLD 的共同交易日不一致")
    if max_age_days is not None:
        age = (pd.Timestamp(today or date.today()).normalize() - calendar[-1]).days
        if age < 0 or age > max_age_days:
            raise ValueError(f"行情最新日期 {calendar[-1]:%Y-%m-%d}，不在允许的 {max_age_days} 日范围内")
    return out


def prepare_allocation(frames):
    prices = validate_prices(frames)
    qqq = prices["QQQ"]
    sma = moving_average(qqq, SMA_WINDOW)
    regime = market_regimes(qqq.close, sma, BULL_LINE, 1.0)
    volatility = prices["TQQQ"].close.pct_change().rolling(VOL_WINDOW).std() * np.sqrt(252)
    data = pd.concat({symbol.lower(): frame for symbol, frame in prices.items()}, axis=1, join="inner")
    data.columns = [f"{symbol}_{field}" for symbol, field in data.columns]
    data["qqq_sma"] = sma.reindex(data.index)
    data["trend_state"] = regime.reindex(data.index)
    months = data.index.to_period("M").asi8
    first_close = np.r_[True, months[1:] != months[:-1]]
    # The first close in each month fixes that month's volatility sleeve. At
    # the first open, the preceding month's signal remains in effect.
    observed = volatility.reindex(data.index).to_numpy()
    sized = np.divide(VOL_TARGET, observed, out=np.zeros(len(data)), where=observed > 0).clip(max=1)
    data["volatility_tqqq_ratio"] = pd.Series(sized, index=data.index).where(first_close).ffill()
    sample_index = np.maximum.accumulate(np.where(first_close, np.arange(len(data)), 0))
    data["active_volatility"] = observed[sample_index]
    data["volatility_sample_date"] = data.index[sample_index]
    data["signal_tqqq"] = (TREND_SHARE * data.trend_state.eq("bull")
                          + (1 - TREND_SHARE) * data.volatility_tqqq_ratio)
    data["signal_bil"] = data["signal_gld"] = (1 - data.signal_tqqq) / 2
    for symbol, frame in prices.items():
        data[f"{symbol.lower()}_daily_change"] = frame.close.pct_change().reindex(data.index)
    return data


def _rebalance(held, funding, weights, slip, sale_cost):
    """Solve post-cost holdings at the modeled open while self-funding all trades."""
    gross = float(held.sum() + funding)
    net = gross
    for _ in range(len(held) + 1):
        signed_rates = np.where(weights * net >= held, slip, -sale_cost)
        updated = (gross + held @ signed_rates) / (1 + weights @ signed_rates)
        if abs(updated - net) <= 2e-15 * gross:
            net = updated
            break
        net = updated
    holdings = weights * net
    delta = holdings - held
    costs = np.maximum(delta, 0) * slip + np.maximum(-delta, 0) * sale_cost
    if abs(funding - float(delta.sum() + costs.sum())) > max(1e-8, gross * 1e-12):
        raise ArithmeticError("模拟调仓资金不平衡")
    return holdings, delta, costs


def backtest_allocation(prepared, start=BACKTEST_START, end=None, *, monthly=CAPITAL, ledger=True):
    """Execute the known prior close allocation at the next open, starting in BIL."""
    if not np.isfinite(monthly) or monthly < 0:
        raise ValueError("每月投入须为非负有限数值")
    data = prepared.loc[pd.Timestamp(start):pd.Timestamp(end) if end is not None else None]
    if data.empty:
        raise ValueError("回测区间内没有交易日")
    dates = data.index
    months = dates.to_period("M").asi8
    columns = [asset.lower() for asset in ASSETS]
    opens = data[[f"{name}_open" for name in columns]].to_numpy()
    closes = data[[f"{name}_close" for name in columns]].to_numpy()
    qqq_open, qqq_close = data.qqq_open.to_numpy(), data.qqq_close.to_numpy()
    close_targets = data[[f"signal_{name}" for name in columns]].to_numpy()
    slip = np.array([SLIPPAGE[asset] for asset in ASSETS])
    sale_cost = slip + SELL_FEE * (1 - slip)
    liquidation = 1 - sale_cost
    qqq_exit = (1 - SLIPPAGE["QQQ"]) * (1 - SELL_FEE)
    target = np.array([0., 1., 0.])
    shares, basis = np.zeros(len(ASSETS)), np.zeros(len(ASSETS))
    qqq_shares = 0.
    previous_value = previous_qqq = CAPITAL
    records, detail, trades = [], [], []

    for i, current_date in enumerate(dates):
        new_month = i > 0 and months[i] != months[i - 1]
        deposit = CAPITAL if i == 0 else (monthly if new_month else 0.)
        old_open_value = shares * opens[i]
        open_before_funding = float(old_open_value @ liquidation)
        next_target = close_targets[i - 1] if i else target
        changed = i > 0 and not np.array_equal(next_target, target)
        target = next_target
        cause = "initial" if i == 0 else "signal_and_funding" if changed and deposit else "signal" if changed else "funding" if deposit else "hold"
        cost = 0.
        signal_date = dates[i - 1] if i else pd.NaT
        if i == 0 or changed or deposit:
            holdings, delta, costs = _rebalance(old_open_value, deposit, target, slip, sale_cost)
            cost = float(costs.sum())
            if ledger:
                for k in np.argsort(delta):
                    amount = float(delta[k])
                    if abs(amount) < 1e-9:
                        continue
                    buy = amount > 0
                    quantity = abs(amount) / opens[i, k]
                    realized = 0.
                    if buy:
                        basis[k] += amount + costs[k]
                    else:
                        sold_basis = basis[k] * min(1., quantity / shares[k])
                        realized = -amount - costs[k] - sold_basis
                        basis[k] -= sold_basis
                    if abs(amount) < 0.01:
                        continue
                    trades.append((current_date, signal_date, ASSETS[k], "buy" if buy else "sell",
                                   cause, quantity, opens[i, k], opens[i, k] * (1 + slip[k] if buy else 1 - slip[k]),
                                   abs(amount), costs[k], -amount - costs[k], realized))
            shares = holdings / opens[i]

        qqq_shares += deposit / (qqq_open[i] * (1 + SLIPPAGE["QQQ"]))
        value = shares * closes[i]
        close_value = float(value @ liquidation)
        qqq_value = float(qqq_shares * qqq_close[i] * qqq_exit)
        funding_base = CAPITAL if i == 0 else previous_value + deposit
        benchmark_base = CAPITAL if i == 0 else previous_qqq + deposit
        daily_return = close_value / funding_base - 1
        benchmark_return = qqq_value / benchmark_base - 1
        split_return = daily_return if i == 0 else (
            (open_before_funding / previous_value) * (close_value / (open_before_funding + deposit)) - 1
        )
        records.append((deposit, close_value, qqq_value, daily_return, benchmark_return, split_return,
                        close_value - funding_base, cost))
        if ledger:
            detail.append((signal_date, cause, *target, *(value / value.sum()), *shares, *value, *basis))
        previous_value, previous_qqq = close_value, qqq_value

    daily = pd.DataFrame(records, index=dates, columns=["contribution", "total_value", "qqq_benchmark_value",
                         "daily_return", "qqq_benchmark_daily_return", "open_split_return", "daily_profit",
                         "transaction_cost"])
    daily["total_contributions"] = daily.contribution.cumsum()
    daily["nav"] = (1 + daily.daily_return).cumprod()
    daily["qqq_benchmark_nav"] = (1 + daily.qqq_benchmark_daily_return).cumprod()
    daily["drawdown"] = daily.nav / daily.nav.cummax().clip(lower=1) - 1
    daily["qqq_benchmark_drawdown"] = daily.qqq_benchmark_nav / daily.qqq_benchmark_nav.cummax().clip(lower=1) - 1
    if ledger:
        fields = [f"{prefix}_{name}" for prefix in ("target", "weight", "shares", "market_value", "cost_basis")
                  for name in columns]
        daily = daily.join(pd.DataFrame(detail, index=dates, columns=["signal_date", "action", *fields]))
        daily = daily.join(data)
    trade_columns = ["trade_date", "signal_date", "asset", "side", "reason", "shares", "market_price",
                     "execution_price", "market_notional", "total_transaction_cost", "net_cash_flow", "realized_profit"]
    return daily.reset_index(names="trade_date"), pd.DataFrame(trades, columns=trade_columns)


def performance(daily, *, cash_weighted=False):
    elapsed_years = (daily.trade_date.iloc[-1] - daily.trade_date.iloc[0]).days / 365.2425
    last = daily.iloc[-1]
    metrics = {
        "start_date": daily.trade_date.iloc[0].date().isoformat(),
        "end_date": last.trade_date.date().isoformat(),
        "final_value": float(last.total_value),
        "qqq_benchmark_final_value": float(last.qqq_benchmark_value),
        "total_contributions": float(last.total_contributions),
        "total_return": float(last.nav - 1),
        "annualized_return": float(last.nav ** (1 / elapsed_years) - 1) if elapsed_years else None,
        "qqq_annualized_return": float(last.qqq_benchmark_nav ** (1 / elapsed_years) - 1) if elapsed_years else None,
        "max_drawdown": float(daily.drawdown.min()),
        "qqq_max_drawdown": float(daily.qqq_benchmark_drawdown.min()),
    }
    if cash_weighted:
        flows = [(item.trade_date, -item.contribution) for item in daily.itertuples(index=False) if item.contribution > 0]
        rate = xirr([*flows, (last.trade_date, last.total_value)]) if elapsed_years else None
        metrics["money_weighted_return"] = float(rate) if rate is not None and np.isfinite(rate) else None
    return metrics


def daily_summary(daily, trades):
    last = daily.iloc[-1]
    targets = {asset: float(last[f"signal_{asset.lower()}"]) for asset in ASSETS}
    current = {asset: float(last[f"weight_{asset.lower()}"]) for asset in ASSETS}
    last_targets = [last[f"target_{asset.lower()}"] for asset in ASSETS]
    changed = not np.allclose(list(targets.values()), last_targets, atol=1e-12, rtol=0.)
    number = lambda value: float(value) if pd.notna(value) else None
    return {
        **performance(daily, cash_weighted=True),
        "strategy_id": STRATEGY_ID,
        "observation_count": len(daily),
        "target_changed": bool(changed),
        "next_action": "按新目标调仓" if changed else "延续目标仓位",
        "target_weights": targets,
        "current_weights": current,
        "trend_state": last.trend_state,
        "trend_label": TREND_LABELS[last.trend_state],
        "qqq_close": number(last.qqq_close),
        "qqq_sma": number(last.qqq_sma),
        "qqq_entry_line": number(last.qqq_sma * BULL_LINE),
        "volatility_sample_date": last.volatility_sample_date.date().isoformat(),
        "active_volatility": number(last.active_volatility),
        "volatility_tqqq_ratio": number(last.volatility_tqqq_ratio),
        "latest_daily_return": float(last.daily_return),
        "latest_daily_profit": float(last.daily_profit),
        "latest_drawdown": float(last.drawdown),
        "latest_trade_count": int(trades.trade_date.eq(last.trade_date).sum()),
        "latest_changes": {asset: number(last[f"{asset.lower()}_daily_change"]) for asset in SYMBOLS},
    }
