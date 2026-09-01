from pathlib import Path

import pandas as pd


def moving_average(df, window=20, price_column="close"):
    """计算指定价格列的简单移动平均线。"""
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("window 必须是正整数")
    if price_column not in df.columns:
        raise ValueError(f"缺少价格列：{price_column}")

    average = df[price_column].rolling(window=window, min_periods=window).mean()
    average.name = f"ma{window}"
    return average


def _buy_details(cash, market_price, commission_rate, slippage_rate):
    execution_price = market_price * (1 + slippage_rate)
    shares = cash / (execution_price * (1 + commission_rate))
    execution_notional = shares * execution_price
    commission = execution_notional * commission_rate
    slippage = shares * (execution_price - market_price)
    return {
        "shares": shares,
        "market_price": market_price,
        "execution_price": execution_price,
        "market_notional": shares * market_price,
        "execution_notional": execution_notional,
        "commission": commission,
        "sell_fee": 0.0,
        "slippage": slippage,
        "capital_gains_tax": 0.0,
        "net_cash_flow": -(execution_notional + commission),
        "total_transaction_cost": commission + slippage,
        "cost_basis": execution_notional + commission,
    }


def _sell_details(
    shares,
    market_price,
    cost_basis,
    commission_rate,
    slippage_rate,
    sell_fee_rate,
    capital_gains_tax_rate,
):
    execution_price = market_price * (1 - slippage_rate)
    execution_notional = shares * execution_price
    commission = execution_notional * commission_rate
    sell_fee = execution_notional * sell_fee_rate
    net_before_tax = execution_notional - commission - sell_fee
    taxable_gain = max(0.0, net_before_tax - cost_basis)
    capital_gains_tax = taxable_gain * capital_gains_tax_rate
    slippage = shares * (market_price - execution_price)
    net_proceeds = net_before_tax - capital_gains_tax
    return {
        "shares": shares,
        "market_price": market_price,
        "execution_price": execution_price,
        "market_notional": shares * market_price,
        "execution_notional": execution_notional,
        "commission": commission,
        "sell_fee": sell_fee,
        "slippage": slippage,
        "capital_gains_tax": capital_gains_tax,
        "net_cash_flow": net_proceeds,
        "total_transaction_cost": commission + sell_fee + slippage,
        "taxable_gain": taxable_gain,
        "realized_profit": net_proceeds - cost_basis,
    }


def market_regimes(
    closes,
    smas,
    bull_multiplier=1.04,
    bear_multiplier=0.97,
):
    """Build a hysteresis regime series; the buffer keeps the prior regime."""
    regime = "neutral"
    values = []
    for close, sma in zip(closes, smas):
        if pd.isna(sma):
            values.append(regime)
            continue
        if close > sma * bull_multiplier:
            regime = "bull"
        elif close < sma * bear_multiplier:
            regime = "bear"
        values.append(regime)
    return pd.Series(values, index=closes.index, name="market_regime")


def _regime_signal_text(regime, close, sma, sma_window, bull_multiplier, bear_multiplier):
    if pd.isna(sma):
        return f"SMA{sma_window} 尚不可用，市场状态为中性"
    if regime == "bull":
        return (
            f"牛市：QQQ={close:.2f}，牛市线="
            f"SMA{sma_window}×{bull_multiplier:.2f}={sma * bull_multiplier:.2f}"
        )
    if regime == "bear":
        return (
            f"熊市：QQQ={close:.2f}，熊市线="
            f"SMA{sma_window}×{bear_multiplier:.2f}={sma * bear_multiplier:.2f}"
        )
    return f"中性缓冲区：等待突破牛市线或跌破熊市线"


def backtest_qqq_sma_tqqq_cash(
    qqq_df,
    tqqq_df,
    initial_capital=10_000,
    monthly_contribution=10_000,
    sma_window=200,
    bull_multiplier=1.04,
    bear_multiplier=0.97,
    dip_threshold=0.01,
    commission_rate=0.0,
    qqq_slippage_rate=0.0001,
    tqqq_slippage_rate=0.0005,
    sell_fee_rate=0.0000206,
    capital_gains_tax_rate=0.0,
):
    """
    回测带牛熊缓冲区的 QQQ SMA200 / TQQQ 策略。

    QQQ 高于 SMA×bull_multiplier 时进入牛市，低于 SMA×bear_multiplier
    时进入熊市，缓冲区内延续原状态。熊市清仓；熊转牛立即投入全部现金；
    牛市中仅在 QQQ 单日跌幅达到 dip_threshold 时投入全部可用现金。
    每月首个交易日把定投资金加入现金池。收盘产生信号，下一交易日开盘执行。

    返回：
      daily：包含全部行情、信号、持仓、资产、涨跌和费用字段的每日账本。
      trades：每次实际买卖的独立成交账本。
      summary：回测汇总指标。
    """
    if initial_capital <= 0:
        raise ValueError("initial_capital 必须大于 0")
    if monthly_contribution < 0:
        raise ValueError("monthly_contribution 不能小于 0")
    if not isinstance(sma_window, int) or isinstance(sma_window, bool) or sma_window <= 0:
        raise ValueError("sma_window 必须是正整数")
    if bull_multiplier <= 1:
        raise ValueError("bull_multiplier 必须大于 1")
    if not 0 < bear_multiplier < 1:
        raise ValueError("bear_multiplier 必须在 (0, 1) 范围内")
    if bull_multiplier <= bear_multiplier:
        raise ValueError("bull_multiplier 必须大于 bear_multiplier")
    if not 0 < dip_threshold < 1:
        raise ValueError("dip_threshold 必须在 (0, 1) 范围内")

    rates = {
        "commission_rate": commission_rate,
        "qqq_slippage_rate": qqq_slippage_rate,
        "tqqq_slippage_rate": tqqq_slippage_rate,
        "sell_fee_rate": sell_fee_rate,
        "capital_gains_tax_rate": capital_gains_tax_rate,
    }
    if any(rate < 0 or rate >= 1 for rate in rates.values()):
        raise ValueError("费率和税率必须在 [0, 1) 范围内")

    required_columns = {"trade_date", "open", "close"}
    if not required_columns.issubset(qqq_df.columns):
        raise ValueError("QQQ 数据必须包含 trade_date、open 和 close 列")
    if not required_columns.issubset(tqqq_df.columns):
        raise ValueError("TQQQ 数据必须包含 trade_date、open 和 close 列")

    qqq = qqq_df[["trade_date", "open", "close"]].copy()
    qqq["trade_date"] = pd.to_datetime(qqq["trade_date"])
    qqq = qqq.sort_values("trade_date").drop_duplicates("trade_date")
    qqq["qqq_sma"] = moving_average(qqq, sma_window)
    qqq["qqq_daily_change"] = qqq["close"].pct_change()
    qqq["market_regime"] = market_regimes(
        qqq["close"],
        qqq["qqq_sma"],
        bull_multiplier,
        bear_multiplier,
    )
    qqq = qqq.rename(columns={"open": "qqq_open", "close": "qqq_close"})

    tqqq = tqqq_df[["trade_date", "open", "close"]].copy()
    tqqq["trade_date"] = pd.to_datetime(tqqq["trade_date"])
    tqqq = tqqq.sort_values("trade_date").drop_duplicates("trade_date")
    tqqq = tqqq.rename(columns={"open": "tqqq_open", "close": "tqqq_close"})

    data = qqq.merge(tqqq, on="trade_date", how="inner")
    data = data.dropna(
        subset=["qqq_open", "qqq_close", "tqqq_open", "tqqq_close"]
    ).reset_index(drop=True)
    if data.empty:
        raise ValueError("QQQ 和 TQQQ 没有可用于回测的共同交易日")
    if (data[["qqq_open", "qqq_close", "tqqq_open", "tqqq_close"]] <= 0).any().any():
        raise ValueError("QQQ 和 TQQQ 的开盘价、收盘价必须大于 0")

    cash = float(initial_capital)
    shares = 0.0
    cost_basis = 0.0
    transaction_costs_paid = 0.0
    capital_gains_taxes_paid = 0.0
    total_contributions = float(initial_capital)
    buy_count = 0
    sell_count = 0

    qqq_cash = float(initial_capital)
    qqq_shares = 0.0
    qqq_cost_basis = 0.0
    qqq_transaction_costs_paid = 0.0

    records = []
    trade_records = []
    previous_total_value = None
    previous_qqq_value = None
    peak_value = None

    for i, row in data.iterrows():
        date = row["trade_date"]
        contribution = 0.0
        action = "hold"
        action_reason = "保持当前仓位"
        signal_date = pd.NaT
        execution_signal = "initial"
        transaction_cost_today = 0.0
        tax_today = 0.0
        cash_before_action = cash
        shares_before_action = shares
        cost_basis_before_action = cost_basis

        if i > 0:
            current_month = date.to_period("M")
            previous_month = data.at[i - 1, "trade_date"].to_period("M")
            if current_month != previous_month:
                contribution = float(monthly_contribution)
                cash += contribution
                qqq_cash += contribution
                total_contributions += contribution

            previous_close = float(data.at[i - 1, "qqq_close"])
            previous_sma = data.at[i - 1, "qqq_sma"]
            previous_change = data.at[i - 1, "qqq_daily_change"]
            previous_regime = str(data.at[i - 1, "market_regime"])
            regime_before_signal = (
                str(data.at[i - 2, "market_regime"]) if i > 1 else "neutral"
            )
            bear_to_bull = regime_before_signal == "bear" and previous_regime == "bull"
            dip_buy = (
                previous_regime == "bull"
                and pd.notna(previous_change)
                and float(previous_change) <= -dip_threshold
            )
            signal_date = data.at[i - 1, "trade_date"]
            if previous_regime == "bear":
                execution_signal = "bear_exit"
            elif bear_to_bull:
                execution_signal = "bear_to_bull_buy"
            elif dip_buy:
                execution_signal = "bull_dip_buy"
            else:
                execution_signal = "hold"

            if execution_signal in {"bear_to_bull_buy", "bull_dip_buy"} and cash > 0:
                cash_before_action = cash
                shares_before_action = shares
                cost_basis_before_action = cost_basis
                trade = _buy_details(
                    cash,
                    float(row["tqqq_open"]),
                    commission_rate,
                    tqqq_slippage_rate,
                )
                shares += trade["shares"]
                cost_basis += trade["cost_basis"]
                cash = max(0.0, cash + trade["net_cash_flow"])
                transaction_costs_paid += trade["total_transaction_cost"]
                transaction_cost_today += trade["total_transaction_cost"]
                buy_count += 1
                action = "buy"
                if bear_to_bull:
                    action_reason = (
                        f"QQQ 熊转牛：收盘突破 SMA{sma_window}×{bull_multiplier:.2f}，"
                        "不等待回调，全现金买入 TQQQ"
                    )
                else:
                    action_reason = (
                        f"牛市中 QQQ 单日下跌 {abs(float(previous_change)):.2%}，"
                        f"达到 {dip_threshold:.2%} 回调阈值，全现金买入 TQQQ"
                    )
                trade_records.append(
                    {
                        "trade_id": len(trade_records) + 1,
                        "trade_date": date,
                        "signal_date": signal_date,
                        "side": "buy",
                        "reason": action_reason,
                        "qqq_signal_close": previous_close,
                        f"qqq_sma{sma_window}": float(previous_sma),
                        "tqqq_market_price": trade["market_price"],
                        "execution_price": trade["execution_price"],
                        "shares": trade["shares"],
                        "market_notional": trade["market_notional"],
                        "execution_notional": trade["execution_notional"],
                        "commission": trade["commission"],
                        "slippage_cost": trade["slippage"],
                        "sell_fee": 0.0,
                        "capital_gains_tax": 0.0,
                        "total_transaction_cost": trade["total_transaction_cost"],
                        "net_cash_flow": trade["net_cash_flow"],
                        "cash_before": cash_before_action,
                        "cash_after": cash,
                        "cost_basis_before": cost_basis_before_action,
                        "cost_basis_after": cost_basis,
                        "realized_profit": 0.0,
                    }
                )
            elif execution_signal == "bear_exit" and shares > 0:
                cash_before_action = cash
                shares_before_action = shares
                cost_basis_before_action = cost_basis
                trade = _sell_details(
                    shares,
                    float(row["tqqq_open"]),
                    cost_basis,
                    commission_rate,
                    tqqq_slippage_rate,
                    sell_fee_rate,
                    capital_gains_tax_rate,
                )
                cash += trade["net_cash_flow"]
                transaction_costs_paid += trade["total_transaction_cost"]
                capital_gains_taxes_paid += trade["capital_gains_tax"]
                transaction_cost_today += trade["total_transaction_cost"]
                tax_today += trade["capital_gains_tax"]
                shares = 0.0
                cost_basis = 0.0
                sell_count += 1
                action = "sell"
                action_reason = (
                    f"QQQ 跌破 SMA{sma_window}×{bear_multiplier:.2f}，进入熊市并清仓 TQQQ"
                )
                trade_records.append(
                    {
                        "trade_id": len(trade_records) + 1,
                        "trade_date": date,
                        "signal_date": signal_date,
                        "side": "sell",
                        "reason": action_reason,
                        "qqq_signal_close": previous_close,
                        f"qqq_sma{sma_window}": float(previous_sma),
                        "tqqq_market_price": trade["market_price"],
                        "execution_price": trade["execution_price"],
                        "shares": trade["shares"],
                        "market_notional": trade["market_notional"],
                        "execution_notional": trade["execution_notional"],
                        "commission": trade["commission"],
                        "slippage_cost": trade["slippage"],
                        "sell_fee": trade["sell_fee"],
                        "capital_gains_tax": trade["capital_gains_tax"],
                        "total_transaction_cost": trade["total_transaction_cost"],
                        "net_cash_flow": trade["net_cash_flow"],
                        "cash_before": cash_before_action,
                        "cash_after": cash,
                        "cost_basis_before": cost_basis_before_action,
                        "cost_basis_after": 0.0,
                        "realized_profit": trade["realized_profit"],
                    }
                )

        if i == 0 or contribution > 0:
            qqq_buy = _buy_details(
                qqq_cash,
                float(row["qqq_open"]),
                commission_rate,
                qqq_slippage_rate,
            )
            qqq_shares += qqq_buy["shares"]
            qqq_cost_basis += qqq_buy["cost_basis"]
            qqq_cash = max(0.0, qqq_cash + qqq_buy["net_cash_flow"])
            qqq_transaction_costs_paid += qqq_buy["total_transaction_cost"]

        position_market_value = shares * float(row["tqqq_close"])
        if shares > 0:
            estimated_exit = _sell_details(
                shares,
                float(row["tqqq_close"]),
                cost_basis,
                commission_rate,
                tqqq_slippage_rate,
                sell_fee_rate,
                capital_gains_tax_rate,
            )
        else:
            estimated_exit = {
                "net_cash_flow": 0.0,
                "total_transaction_cost": 0.0,
                "capital_gains_tax": 0.0,
            }
        liquidation_value = estimated_exit["net_cash_flow"]
        total_value = cash + liquidation_value

        qqq_estimated_exit = _sell_details(
            qqq_shares,
            float(row["qqq_close"]),
            qqq_cost_basis,
            commission_rate,
            qqq_slippage_rate,
            sell_fee_rate,
            capital_gains_tax_rate,
        )
        qqq_value = qqq_cash + qqq_estimated_exit["net_cash_flow"]

        if previous_total_value is None:
            daily_profit = total_value - float(initial_capital)
            daily_return = total_value / float(initial_capital) - 1
            qqq_daily_profit = qqq_value - float(initial_capital)
            qqq_daily_return = qqq_value / float(initial_capital) - 1
        else:
            daily_profit = total_value - previous_total_value - contribution
            daily_return = daily_profit / (previous_total_value + contribution)
            qqq_daily_profit = qqq_value - previous_qqq_value - contribution
            qqq_daily_return = qqq_daily_profit / (previous_qqq_value + contribution)

        peak_value = total_value if peak_value is None else max(peak_value, total_value)
        current_signal = str(row["market_regime"])
        current_change = row["qqq_daily_change"]
        prior_regime = str(data.at[i - 1, "market_regime"]) if i > 0 else "neutral"
        current_bear_to_bull = prior_regime == "bear" and current_signal == "bull"
        if current_signal == "bear":
            next_instruction = "下一交易日清仓 TQQQ；新增定投资金保留为现金"
        elif current_bear_to_bull:
            next_instruction = "熊转牛，下一交易日将全部现金买入 TQQQ"
        elif (
            current_signal == "bull"
            and pd.notna(current_change)
            and float(current_change) <= -dip_threshold
        ):
            next_instruction = "牛市回调达到阈值，下一交易日将全部现金买入 TQQQ"
        elif current_signal == "bull":
            next_instruction = "保持仓位，现金等待 QQQ 单日下跌达到回调阈值"
        else:
            next_instruction = "状态尚未明确，保持现金并等待牛市或熊市信号"

        avg_cost_per_share = cost_basis / shares if shares > 0 else 0.0
        records.append(
            {
                "trade_date": date,
                "qqq_open": float(row["qqq_open"]),
                "qqq_close": float(row["qqq_close"]),
                f"qqq_sma{sma_window}": (
                    float(row["qqq_sma"]) if pd.notna(row["qqq_sma"]) else float("nan")
                ),
                "bull_threshold": (
                    float(row["qqq_sma"] * bull_multiplier)
                    if pd.notna(row["qqq_sma"])
                    else float("nan")
                ),
                "bear_threshold": (
                    float(row["qqq_sma"] * bear_multiplier)
                    if pd.notna(row["qqq_sma"])
                    else float("nan")
                ),
                "market_regime": current_signal,
                "qqq_daily_change": float(current_change) if pd.notna(current_change) else float("nan"),
                "tqqq_open": float(row["tqqq_open"]),
                "tqqq_close": float(row["tqqq_close"]),
                "tqqq_daily_change": (
                    float(row["tqqq_close"]) / float(data.at[i - 1, "tqqq_close"]) - 1
                    if i > 0
                    else float("nan")
                ),
                "close_signal": current_signal,
                "next_day_instruction": next_instruction,
                "execution_signal_date": signal_date,
                "execution_signal": execution_signal,
                "action": action,
                "action_reason": action_reason,
                "monthly_contribution": contribution,
                "total_contributions": total_contributions,
                "position_status": "TQQQ" if shares > 0 else "cash",
                "cash": cash,
                "shares": shares,
                "average_cost_per_share": avg_cost_per_share,
                "cost_basis": cost_basis,
                "position_market_value": position_market_value,
                "position_liquidation_value": liquidation_value,
                "unrealized_profit_before_exit_cost": position_market_value - cost_basis,
                "exposure_ratio": position_market_value / (cash + position_market_value),
                "total_value": total_value,
                "daily_profit": daily_profit,
                "daily_return": daily_return,
                "cumulative_profit": total_value - total_contributions,
                "cumulative_return": total_value / total_contributions - 1,
                "drawdown": total_value / peak_value - 1,
                "transaction_cost_today": transaction_cost_today,
                "transaction_costs_paid": transaction_costs_paid,
                "capital_gains_tax_today": tax_today,
                "capital_gains_taxes_paid": capital_gains_taxes_paid,
                "estimated_exit_cost": estimated_exit["total_transaction_cost"],
                "estimated_exit_tax": estimated_exit["capital_gains_tax"],
                "qqq_benchmark_shares": qqq_shares,
                "qqq_benchmark_cost_basis": qqq_cost_basis,
                "qqq_benchmark_value": qqq_value,
                "qqq_benchmark_daily_profit": qqq_daily_profit,
                "qqq_benchmark_daily_return": qqq_daily_return,
                "qqq_benchmark_cumulative_profit": qqq_value - total_contributions,
                "qqq_benchmark_cumulative_return": qqq_value / total_contributions - 1,
                "qqq_transaction_costs_paid": qqq_transaction_costs_paid,
                "qqq_estimated_exit_cost": qqq_estimated_exit["total_transaction_cost"],
                "qqq_estimated_exit_tax": qqq_estimated_exit["capital_gains_tax"],
            }
        )
        previous_total_value = total_value
        previous_qqq_value = qqq_value

    daily = pd.DataFrame(records)
    trades = pd.DataFrame(trade_records)
    latest = daily.iloc[-1]
    final_value = float(latest["total_value"])
    qqq_benchmark_final_value = float(latest["qqq_benchmark_value"])
    profit = final_value - total_contributions

    current_position = str(latest["position_status"])
    latest_signal = str(latest["market_regime"])
    latest_change = float(latest["qqq_daily_change"])
    latest_bear_to_bull = (
        len(daily) > 1
        and str(daily.iloc[-2]["market_regime"]) == "bear"
        and latest_signal == "bull"
    )
    available_cash = float(latest["cash"])
    if latest_signal == "bear" and current_position == "TQQQ":
        next_decision = {
            "action": "sell",
            "action_text": "卖出全部 TQQQ",
            "reason": f"QQQ 已跌破 SMA{sma_window}×{bear_multiplier:.2f}，进入熊市",
        }
    elif available_cash > 0 and latest_bear_to_bull:
        next_decision = {
            "action": "buy",
            "action_text": "用全部现金买入 TQQQ",
            "reason": f"QQQ 熊转牛并突破 SMA{sma_window}×{bull_multiplier:.2f}",
        }
    elif (
        available_cash > 0
        and latest_signal == "bull"
        and latest_change <= -dip_threshold
    ):
        next_decision = {
            "action": "buy",
            "action_text": "用全部现金买入 TQQQ",
            "reason": f"牛市中 QQQ 单日下跌 {abs(latest_change):.2%}，达到回调阈值",
        }
    else:
        next_decision = {
            "action": "hold",
            "action_text": "不交易",
            "reason": str(latest["next_day_instruction"]),
        }
    next_decision["signal_text"] = _regime_signal_text(
        latest_signal,
        float(latest["qqq_close"]),
        latest[f"qqq_sma{sma_window}"],
        sma_window,
        bull_multiplier,
        bear_multiplier,
    )

    today_action = str(latest["action"])
    today_action_text = {
        "initial_buy": "首次买入 TQQQ",
        "buy": "已买入 TQQQ",
        "sell": "已卖出 TQQQ",
        "hold": "今日未交易",
    }.get(today_action, today_action)
    summary = {
        "start_date": daily.iloc[0]["trade_date"],
        "end_date": daily.iloc[-1]["trade_date"],
        "initial_capital": float(initial_capital),
        "monthly_contribution": float(monthly_contribution),
        "sma_window": int(sma_window),
        "bull_multiplier": float(bull_multiplier),
        "bear_multiplier": float(bear_multiplier),
        "dip_threshold": float(dip_threshold),
        "commission_rate": float(commission_rate),
        "qqq_slippage_rate": float(qqq_slippage_rate),
        "tqqq_slippage_rate": float(tqqq_slippage_rate),
        "sell_fee_rate": float(sell_fee_rate),
        "capital_gains_tax_rate": float(capital_gains_tax_rate),
        "total_contributions": total_contributions,
        "final_value": final_value,
        "qqq_benchmark_final_value": qqq_benchmark_final_value,
        "strategy_vs_qqq": final_value / qqq_benchmark_final_value - 1,
        "transaction_costs_paid_or_estimated": (
            float(daily.iloc[-1]["transaction_costs_paid"])
            + float(daily.iloc[-1]["estimated_exit_cost"])
        ),
        "capital_gains_taxes_paid_or_estimated": (
            float(daily.iloc[-1]["capital_gains_taxes_paid"])
            + float(daily.iloc[-1]["estimated_exit_tax"])
        ),
        "qqq_transaction_costs_paid_or_estimated": (
            qqq_transaction_costs_paid
            + float(daily.iloc[-1]["qqq_estimated_exit_cost"])
        ),
        "qqq_capital_gains_tax_estimated": float(
            daily.iloc[-1]["qqq_estimated_exit_tax"]
        ),
        "profit": profit,
        "return_rate": profit / total_contributions,
        "max_drawdown": float(daily["drawdown"].min()),
        "buy_count": buy_count,
        "sell_count": sell_count,
        "current_position": current_position,
        "latest_signal": latest_signal,
        "latest_signal_text": next_decision["signal_text"],
        "latest_qqq_close": float(latest["qqq_close"]),
        "latest_sma": float(latest[f"qqq_sma{sma_window}"]),
        "latest_qqq_daily_change": float(latest["qqq_daily_change"]),
        "latest_tqqq_daily_change": float(latest["tqqq_daily_change"]),
        "latest_daily_profit": float(latest["daily_profit"]),
        "latest_daily_return": float(latest["daily_return"]),
        "latest_qqq_benchmark_daily_profit": float(
            latest["qqq_benchmark_daily_profit"]
        ),
        "latest_qqq_benchmark_daily_return": float(
            latest["qqq_benchmark_daily_return"]
        ),
        "today_action": today_action,
        "today_action_text": today_action_text,
        "next_action": next_decision["action"],
        "next_action_text": next_decision["action_text"],
        "next_action_reason": next_decision["reason"],
    }
    return daily, trades, summary


def backtest_qqq_sma_tqqq(
    qqq_df,
    tqqq_df,
    bil_df,
    initial_capital=10_000,
    monthly_contribution=10_000,
    sma_window=200,
    bull_multiplier=1.04,
    bear_multiplier=0.97,
    dip_threshold=0.01,
    commission_rate=0.0,
    qqq_slippage_rate=0.0001,
    tqqq_slippage_rate=0.0005,
    bil_slippage_rate=0.0001,
    sell_fee_rate=0.0000206,
    capital_gains_tax_rate=0.0,
    start_date=None,
):
    """回测牛市持有 TQQQ、等待或熊市阶段持有 BIL 的轮动策略。

    start_date 仅决定资金开始投入的日期；SMA 和牛熊状态仍使用此前的 QQQ
    历史数据预热，避免不同起始时间因指标缺少预热期而失真。
    """
    if initial_capital <= 0:
        raise ValueError("initial_capital 必须大于 0")
    if monthly_contribution < 0:
        raise ValueError("monthly_contribution 不能小于 0")
    if not isinstance(sma_window, int) or isinstance(sma_window, bool) or sma_window <= 0:
        raise ValueError("sma_window 必须是正整数")
    if bull_multiplier <= 1 or not 0 < bear_multiplier < 1:
        raise ValueError("牛熊阈值倍数无效")
    if not 0 < dip_threshold < 1:
        raise ValueError("dip_threshold 必须在 (0, 1) 范围内")

    rates = [
        commission_rate,
        qqq_slippage_rate,
        tqqq_slippage_rate,
        bil_slippage_rate,
        sell_fee_rate,
        capital_gains_tax_rate,
    ]
    if any(rate < 0 or rate >= 1 for rate in rates):
        raise ValueError("费率和税率必须在 [0, 1) 范围内")

    required_columns = {"trade_date", "open", "close"}
    frames = {"QQQ": qqq_df, "TQQQ": tqqq_df, "BIL": bil_df}
    for symbol, frame in frames.items():
        if not required_columns.issubset(frame.columns):
            raise ValueError(f"{symbol} 数据必须包含 trade_date、open 和 close 列")

    prepared = {}
    for symbol, frame in frames.items():
        item = frame[["trade_date", "open", "close"]].copy()
        item["trade_date"] = pd.to_datetime(item["trade_date"])
        item = item.sort_values("trade_date").drop_duplicates("trade_date")
        prefix = symbol.lower()
        prepared[symbol] = item.rename(
            columns={"open": f"{prefix}_open", "close": f"{prefix}_close"}
        )

    qqq = prepared["QQQ"]
    qqq["qqq_sma"] = moving_average(qqq, sma_window, "qqq_close")
    qqq["qqq_daily_change"] = qqq["qqq_close"].pct_change()
    qqq["market_regime"] = market_regimes(
        qqq["qqq_close"], qqq["qqq_sma"], bull_multiplier, bear_multiplier
    )
    data = qqq.merge(prepared["TQQQ"], on="trade_date", how="inner")
    data = data.merge(prepared["BIL"], on="trade_date", how="inner")
    price_columns = [
        "qqq_open",
        "qqq_close",
        "tqqq_open",
        "tqqq_close",
        "bil_open",
        "bil_close",
    ]
    data = data.dropna(subset=price_columns).reset_index(drop=True)
    if data.empty:
        raise ValueError("QQQ、TQQQ 与 BIL 没有共同交易日")
    if (data[price_columns] <= 0).any().any():
        raise ValueError("全部开盘价和收盘价必须大于 0")
    if start_date is not None:
        try:
            requested_start = pd.Timestamp(start_date).normalize()
        except (TypeError, ValueError) as exc:
            raise ValueError("start_date 必须是有效日期") from exc
        data = data[data["trade_date"] >= requested_start].reset_index(drop=True)
        if data.empty:
            raise ValueError("start_date 晚于可用行情的最后交易日")

    slippage_rates = {
        "QQQ": float(qqq_slippage_rate),
        "TQQQ": float(tqqq_slippage_rate),
        "BIL": float(bil_slippage_rate),
    }
    portfolio = {
        "cash": float(initial_capital),
        "tqqq_shares": 0.0,
        "tqqq_cost_basis": 0.0,
        "bil_shares": 0.0,
        "bil_cost_basis": 0.0,
        "transaction_costs_paid": 0.0,
        "taxes_paid": 0.0,
    }
    trade_records = []

    def buy_asset(asset, price, date, signal_date, reason, signal_close, signal_sma):
        cash_before = portfolio["cash"]
        if cash_before <= 0:
            return None
        trade = _buy_details(
            cash_before, price, commission_rate, slippage_rates[asset]
        )
        key = asset.lower()
        cost_before = portfolio[f"{key}_cost_basis"]
        portfolio[f"{key}_shares"] += trade["shares"]
        portfolio[f"{key}_cost_basis"] += trade["cost_basis"]
        portfolio["cash"] = max(0.0, portfolio["cash"] + trade["net_cash_flow"])
        portfolio["transaction_costs_paid"] += trade["total_transaction_cost"]
        trade_records.append(
            {
                "trade_id": len(trade_records) + 1,
                "trade_date": date,
                "signal_date": signal_date,
                "asset": asset,
                "side": "buy",
                "reason": reason,
                "qqq_signal_close": signal_close,
                f"qqq_sma{sma_window}": signal_sma,
                "market_price": trade["market_price"],
                "execution_price": trade["execution_price"],
                "shares": trade["shares"],
                "market_notional": trade["market_notional"],
                "execution_notional": trade["execution_notional"],
                "commission": trade["commission"],
                "slippage_cost": trade["slippage"],
                "sell_fee": 0.0,
                "capital_gains_tax": 0.0,
                "total_transaction_cost": trade["total_transaction_cost"],
                "net_cash_flow": trade["net_cash_flow"],
                "cash_before": cash_before,
                "cash_after": portfolio["cash"],
                "cost_basis_before": cost_before,
                "cost_basis_after": portfolio[f"{key}_cost_basis"],
                "realized_profit": 0.0,
            }
        )
        return trade

    def sell_asset(asset, price, date, signal_date, reason, signal_close, signal_sma):
        key = asset.lower()
        shares = portfolio[f"{key}_shares"]
        if shares <= 0:
            return None
        cost_before = portfolio[f"{key}_cost_basis"]
        cash_before = portfolio["cash"]
        trade = _sell_details(
            shares,
            price,
            cost_before,
            commission_rate,
            slippage_rates[asset],
            sell_fee_rate,
            capital_gains_tax_rate,
        )
        portfolio["cash"] += trade["net_cash_flow"]
        portfolio[f"{key}_shares"] = 0.0
        portfolio[f"{key}_cost_basis"] = 0.0
        portfolio["transaction_costs_paid"] += trade["total_transaction_cost"]
        portfolio["taxes_paid"] += trade["capital_gains_tax"]
        trade_records.append(
            {
                "trade_id": len(trade_records) + 1,
                "trade_date": date,
                "signal_date": signal_date,
                "asset": asset,
                "side": "sell",
                "reason": reason,
                "qqq_signal_close": signal_close,
                f"qqq_sma{sma_window}": signal_sma,
                "market_price": trade["market_price"],
                "execution_price": trade["execution_price"],
                "shares": trade["shares"],
                "market_notional": trade["market_notional"],
                "execution_notional": trade["execution_notional"],
                "commission": trade["commission"],
                "slippage_cost": trade["slippage"],
                "sell_fee": trade["sell_fee"],
                "capital_gains_tax": trade["capital_gains_tax"],
                "total_transaction_cost": trade["total_transaction_cost"],
                "net_cash_flow": trade["net_cash_flow"],
                "cash_before": cash_before,
                "cash_after": portfolio["cash"],
                "cost_basis_before": cost_before,
                "cost_basis_after": 0.0,
                "realized_profit": trade["realized_profit"],
            }
        )
        return trade

    qqq_cash = float(initial_capital)
    qqq_shares = 0.0
    qqq_cost_basis = 0.0
    qqq_transaction_costs_paid = 0.0
    total_contributions = float(initial_capital)
    tqqq_buy_count = 0
    tqqq_sell_count = 0
    bil_buy_count = 0
    bil_sell_count = 0
    records = []
    previous_total_value = None
    previous_qqq_value = None
    peak_value = None

    for i, row in data.iterrows():
        date = row["trade_date"]
        contribution = 0.0
        action = "hold"
        action_reason = "保持当前仓位"
        execution_signal = "initial"
        signal_date = pd.NaT
        signal_close = float("nan")
        signal_sma = float("nan")
        transaction_cost_today = 0.0
        tax_today = 0.0

        if i > 0:
            if date.to_period("M") != data.at[i - 1, "trade_date"].to_period("M"):
                contribution = float(monthly_contribution)
                portfolio["cash"] += contribution
                qqq_cash += contribution
                total_contributions += contribution

            signal_date = data.at[i - 1, "trade_date"]
            signal_close = float(data.at[i - 1, "qqq_close"])
            signal_sma_value = data.at[i - 1, "qqq_sma"]
            signal_sma = (
                float(signal_sma_value) if pd.notna(signal_sma_value) else float("nan")
            )
            previous_change = data.at[i - 1, "qqq_daily_change"]
            previous_regime = str(data.at[i - 1, "market_regime"])
            regime_before = str(data.at[i - 2, "market_regime"]) if i > 1 else "neutral"
            bear_to_bull = regime_before == "bear" and previous_regime == "bull"
            dip_buy = (
                previous_regime == "bull"
                and pd.notna(previous_change)
                and float(previous_change) <= -dip_threshold
            )
            if previous_regime == "bear":
                execution_signal = "bear_to_bil"
            elif bear_to_bull:
                execution_signal = "bear_to_bull_buy"
            elif dip_buy:
                execution_signal = "bull_dip_buy"
            else:
                execution_signal = "hold"

            if execution_signal == "bear_to_bil" and portfolio["tqqq_shares"] > 0:
                reason = (
                    f"QQQ 跌破 SMA{sma_window}×{bear_multiplier:.2f}，"
                    "卖出 TQQQ 并转入 BIL"
                )
                sold = sell_asset(
                    "TQQQ", float(row["tqqq_open"]), date, signal_date,
                    reason, signal_close, signal_sma,
                )
                bought = buy_asset(
                    "BIL", float(row["bil_open"]), date, signal_date,
                    reason, signal_close, signal_sma,
                )
                transaction_cost_today += sold["total_transaction_cost"]
                transaction_cost_today += bought["total_transaction_cost"]
                tax_today += sold["capital_gains_tax"]
                tqqq_sell_count += 1
                bil_buy_count += 1
                action = "sell"
                action_reason = reason
            elif execution_signal in {"bear_to_bull_buy", "bull_dip_buy"} and (
                portfolio["bil_shares"] > 0 or portfolio["cash"] > 0
            ):
                if execution_signal == "bear_to_bull_buy":
                    reason = "QQQ 熊转牛，卖出 BIL 并将全部资金买入 TQQQ"
                else:
                    reason = (
                        f"牛市中 QQQ 单日下跌 {abs(float(previous_change)):.2%}，"
                        "卖出 BIL 并将全部可用资金买入 TQQQ"
                    )
                if portfolio["bil_shares"] > 0:
                    sold = sell_asset(
                        "BIL", float(row["bil_open"]), date, signal_date,
                        reason, signal_close, signal_sma,
                    )
                    transaction_cost_today += sold["total_transaction_cost"]
                    tax_today += sold["capital_gains_tax"]
                    bil_sell_count += 1
                bought = buy_asset(
                    "TQQQ", float(row["tqqq_open"]), date, signal_date,
                    reason, signal_close, signal_sma,
                )
                transaction_cost_today += bought["total_transaction_cost"]
                tqqq_buy_count += 1
                action = "buy"
                action_reason = reason

        if i == 0:
            action_reason = "回测起始资金买入短期美国国库券 ETF BIL"
            bought = buy_asset(
                "BIL", float(row["bil_open"]), date, pd.NaT,
                action_reason, float("nan"), float("nan"),
            )
            transaction_cost_today += bought["total_transaction_cost"]
            bil_buy_count += 1
            action = "buy_bil"
        elif portfolio["bil_shares"] > 0 and portfolio["cash"] > 0:
            reason = "处于 BIL 防守仓位，将本月新增资金继续买入 BIL"
            bought = buy_asset(
                "BIL", float(row["bil_open"]), date, signal_date,
                reason, signal_close, signal_sma,
            )
            transaction_cost_today += bought["total_transaction_cost"]
            bil_buy_count += 1
            if action == "hold":
                action = "buy_bil"
                action_reason = reason

        if i == 0 or contribution > 0:
            qqq_buy = _buy_details(
                qqq_cash, float(row["qqq_open"]), commission_rate, qqq_slippage_rate
            )
            qqq_shares += qqq_buy["shares"]
            qqq_cost_basis += qqq_buy["cost_basis"]
            qqq_cash = max(0.0, qqq_cash + qqq_buy["net_cash_flow"])
            qqq_transaction_costs_paid += qqq_buy["total_transaction_cost"]

        tqqq_market_value = portfolio["tqqq_shares"] * float(row["tqqq_close"])
        bil_market_value = portfolio["bil_shares"] * float(row["bil_close"])
        tqqq_exit = (
            _sell_details(
                portfolio["tqqq_shares"], float(row["tqqq_close"]),
                portfolio["tqqq_cost_basis"], commission_rate, tqqq_slippage_rate,
                sell_fee_rate, capital_gains_tax_rate,
            )
            if portfolio["tqqq_shares"] > 0
            else {"net_cash_flow": 0.0, "total_transaction_cost": 0.0, "capital_gains_tax": 0.0}
        )
        bil_exit = (
            _sell_details(
                portfolio["bil_shares"], float(row["bil_close"]),
                portfolio["bil_cost_basis"], commission_rate, bil_slippage_rate,
                sell_fee_rate, capital_gains_tax_rate,
            )
            if portfolio["bil_shares"] > 0
            else {"net_cash_flow": 0.0, "total_transaction_cost": 0.0, "capital_gains_tax": 0.0}
        )
        liquidation_value = tqqq_exit["net_cash_flow"] + bil_exit["net_cash_flow"]
        total_value = portfolio["cash"] + liquidation_value

        qqq_exit = _sell_details(
            qqq_shares, float(row["qqq_close"]), qqq_cost_basis,
            commission_rate, qqq_slippage_rate, sell_fee_rate, capital_gains_tax_rate,
        )
        qqq_value = qqq_cash + qqq_exit["net_cash_flow"]
        if previous_total_value is None:
            daily_profit = total_value - initial_capital
            daily_return = total_value / initial_capital - 1
            qqq_daily_profit = qqq_value - initial_capital
            qqq_daily_return = qqq_value / initial_capital - 1
        else:
            daily_profit = total_value - previous_total_value - contribution
            daily_return = daily_profit / (previous_total_value + contribution)
            qqq_daily_profit = qqq_value - previous_qqq_value - contribution
            qqq_daily_return = qqq_daily_profit / (previous_qqq_value + contribution)

        peak_value = total_value if peak_value is None else max(peak_value, total_value)
        regime = str(row["market_regime"])
        current_change = row["qqq_daily_change"]
        prior_regime = str(data.at[i - 1, "market_regime"]) if i > 0 else "neutral"
        current_bear_to_bull = prior_regime == "bear" and regime == "bull"
        if regime == "bear":
            next_instruction = "下一交易日持有或买入 BIL；若持有 TQQQ 则切换至 BIL"
        elif current_bear_to_bull:
            next_instruction = "熊转牛，下一交易日卖出 BIL 并买入 TQQQ"
        elif regime == "bull" and pd.notna(current_change) and float(current_change) <= -dip_threshold:
            next_instruction = "牛市回调达到阈值，下一交易日卖出 BIL 并买入 TQQQ"
        elif portfolio["tqqq_shares"] > 0:
            next_instruction = "继续持有 TQQQ；新增现金等待下一次回调"
        else:
            next_instruction = "继续持有 BIL，等待牛市回调买点"

        position = "TQQQ" if portfolio["tqqq_shares"] > 0 else "BIL"
        active_shares = (
            portfolio["tqqq_shares"] if position == "TQQQ" else portfolio["bil_shares"]
        )
        active_cost = (
            portfolio["tqqq_cost_basis"] if position == "TQQQ" else portfolio["bil_cost_basis"]
        )
        gross_value = portfolio["cash"] + tqqq_market_value + bil_market_value
        records.append(
            {
                "trade_date": date,
                "qqq_open": float(row["qqq_open"]),
                "qqq_close": float(row["qqq_close"]),
                f"qqq_sma{sma_window}": float(row["qqq_sma"]) if pd.notna(row["qqq_sma"]) else float("nan"),
                "bull_threshold": float(row["qqq_sma"] * bull_multiplier) if pd.notna(row["qqq_sma"]) else float("nan"),
                "bear_threshold": float(row["qqq_sma"] * bear_multiplier) if pd.notna(row["qqq_sma"]) else float("nan"),
                "market_regime": regime,
                "qqq_daily_change": float(current_change) if pd.notna(current_change) else float("nan"),
                "tqqq_open": float(row["tqqq_open"]),
                "tqqq_close": float(row["tqqq_close"]),
                "tqqq_daily_change": float(row["tqqq_close"]) / float(data.at[i - 1, "tqqq_close"]) - 1 if i > 0 else float("nan"),
                "bil_open": float(row["bil_open"]),
                "bil_close": float(row["bil_close"]),
                "bil_daily_change": float(row["bil_close"]) / float(data.at[i - 1, "bil_close"]) - 1 if i > 0 else float("nan"),
                "close_signal": regime,
                "next_day_instruction": next_instruction,
                "execution_signal_date": signal_date,
                "execution_signal": execution_signal,
                "action": action,
                "action_reason": action_reason,
                "monthly_contribution": contribution,
                "total_contributions": total_contributions,
                "position_status": position,
                "cash": portfolio["cash"],
                "shares": portfolio["tqqq_shares"],
                "bil_shares": portfolio["bil_shares"],
                "average_cost_per_share": active_cost / active_shares if active_shares > 0 else 0.0,
                "cost_basis": active_cost,
                "tqqq_cost_basis": portfolio["tqqq_cost_basis"],
                "bil_cost_basis": portfolio["bil_cost_basis"],
                "tqqq_market_value": tqqq_market_value,
                "bil_market_value": bil_market_value,
                "position_market_value": tqqq_market_value + bil_market_value,
                "position_liquidation_value": liquidation_value,
                "unrealized_profit_before_exit_cost": tqqq_market_value + bil_market_value - active_cost,
                "exposure_ratio": tqqq_market_value / gross_value if gross_value > 0 else 0.0,
                "bil_exposure_ratio": bil_market_value / gross_value if gross_value > 0 else 0.0,
                "total_value": total_value,
                "daily_profit": daily_profit,
                "daily_return": daily_return,
                "cumulative_profit": total_value - total_contributions,
                "cumulative_return": total_value / total_contributions - 1,
                "drawdown": total_value / peak_value - 1,
                "transaction_cost_today": transaction_cost_today,
                "transaction_costs_paid": portfolio["transaction_costs_paid"],
                "capital_gains_tax_today": tax_today,
                "capital_gains_taxes_paid": portfolio["taxes_paid"],
                "estimated_exit_cost": tqqq_exit["total_transaction_cost"] + bil_exit["total_transaction_cost"],
                "estimated_exit_tax": tqqq_exit["capital_gains_tax"] + bil_exit["capital_gains_tax"],
                "qqq_benchmark_shares": qqq_shares,
                "qqq_benchmark_cost_basis": qqq_cost_basis,
                "qqq_benchmark_value": qqq_value,
                "qqq_benchmark_daily_profit": qqq_daily_profit,
                "qqq_benchmark_daily_return": qqq_daily_return,
                "qqq_benchmark_cumulative_profit": qqq_value - total_contributions,
                "qqq_benchmark_cumulative_return": qqq_value / total_contributions - 1,
                "qqq_transaction_costs_paid": qqq_transaction_costs_paid,
                "qqq_estimated_exit_cost": qqq_exit["total_transaction_cost"],
                "qqq_estimated_exit_tax": qqq_exit["capital_gains_tax"],
            }
        )
        previous_total_value = total_value
        previous_qqq_value = qqq_value

    daily = pd.DataFrame(records)
    trades = pd.DataFrame(trade_records)
    latest = daily.iloc[-1]
    latest_regime = str(latest["market_regime"])
    latest_change = float(latest["qqq_daily_change"])
    latest_bear_to_bull = (
        len(daily) > 1
        and str(daily.iloc[-2]["market_regime"]) == "bear"
        and latest_regime == "bull"
    )
    if latest_regime == "bear" and latest["position_status"] == "TQQQ":
        next_action = "sell"
        next_action_text = "卖出 TQQQ，并将全部资金买入 BIL"
        next_reason = "QQQ 已进入熊市，切换至短期美国国库券防守仓位"
    elif latest_bear_to_bull or (
        latest_regime == "bull" and latest_change <= -dip_threshold
    ):
        next_action = "buy"
        next_action_text = "卖出 BIL，并将全部资金买入 TQQQ"
        next_reason = str(latest["next_day_instruction"])
    else:
        next_action = "hold"
        next_action_text = f"不交易，继续持有 {latest['position_status']}"
        next_reason = str(latest["next_day_instruction"])

    today_action_text = {
        "buy": "已切换或加仓 TQQQ",
        "sell": "已卖出 TQQQ 并切换至 BIL",
        "buy_bil": "已买入 BIL",
        "hold": "今日未交易",
    }.get(str(latest["action"]), str(latest["action"]))
    final_value = float(latest["total_value"])
    summary = {
        "start_date": daily.iloc[0]["trade_date"],
        "end_date": latest["trade_date"],
        "defensive_asset": "BIL",
        "initial_capital": float(initial_capital),
        "monthly_contribution": float(monthly_contribution),
        "sma_window": int(sma_window),
        "bull_multiplier": float(bull_multiplier),
        "bear_multiplier": float(bear_multiplier),
        "dip_threshold": float(dip_threshold),
        "commission_rate": float(commission_rate),
        "qqq_slippage_rate": float(qqq_slippage_rate),
        "tqqq_slippage_rate": float(tqqq_slippage_rate),
        "bil_slippage_rate": float(bil_slippage_rate),
        "sell_fee_rate": float(sell_fee_rate),
        "capital_gains_tax_rate": float(capital_gains_tax_rate),
        "total_contributions": total_contributions,
        "final_value": final_value,
        "qqq_benchmark_final_value": float(latest["qqq_benchmark_value"]),
        "strategy_vs_qqq": final_value / float(latest["qqq_benchmark_value"]) - 1,
        "transaction_costs_paid_or_estimated": float(latest["transaction_costs_paid"] + latest["estimated_exit_cost"]),
        "capital_gains_taxes_paid_or_estimated": float(latest["capital_gains_taxes_paid"] + latest["estimated_exit_tax"]),
        "profit": final_value - total_contributions,
        "return_rate": final_value / total_contributions - 1,
        "max_drawdown": float(daily["drawdown"].min()),
        "buy_count": tqqq_buy_count,
        "sell_count": tqqq_sell_count,
        "bil_buy_count": bil_buy_count,
        "bil_sell_count": bil_sell_count,
        "current_position": str(latest["position_status"]),
        "latest_signal": latest_regime,
        "latest_signal_text": _regime_signal_text(
            latest_regime, float(latest["qqq_close"]), latest[f"qqq_sma{sma_window}"],
            sma_window, bull_multiplier, bear_multiplier,
        ),
        "latest_qqq_close": float(latest["qqq_close"]),
        "latest_sma": float(latest[f"qqq_sma{sma_window}"]),
        "latest_qqq_daily_change": latest_change,
        "latest_tqqq_daily_change": float(latest["tqqq_daily_change"]),
        "latest_bil_close": float(latest["bil_close"]),
        "latest_bil_daily_change": float(latest["bil_daily_change"]),
        "latest_daily_profit": float(latest["daily_profit"]),
        "latest_daily_return": float(latest["daily_return"]),
        "today_action": str(latest["action"]),
        "today_action_text": today_action_text,
        "next_action": next_action,
        "next_action_text": next_action_text,
        "next_action_reason": next_reason,
    }
    return daily, trades, summary


def report_tables(daily):
    """从完整每日账本拆分出持仓明细与涨跌明细。"""
    position_columns = [
        "trade_date",
        "position_status",
        "action",
        "action_reason",
        "monthly_contribution",
        "cash",
        "shares",
        "bil_shares",
        "average_cost_per_share",
        "cost_basis",
        "tqqq_close",
        "bil_close",
        "tqqq_market_value",
        "bil_market_value",
        "position_market_value",
        "position_liquidation_value",
        "unrealized_profit_before_exit_cost",
        "exposure_ratio",
        "bil_exposure_ratio",
        "total_value",
        "estimated_exit_cost",
        "estimated_exit_tax",
    ]
    change_columns = [
        "trade_date",
        "qqq_close",
        next(column for column in daily.columns if column.startswith("qqq_sma")),
        "qqq_daily_change",
        "tqqq_close",
        "tqqq_daily_change",
        "bil_close",
        "bil_daily_change",
        "close_signal",
        "next_day_instruction",
        "total_value",
        "daily_profit",
        "daily_return",
        "cumulative_profit",
        "cumulative_return",
        "drawdown",
        "transaction_cost_today",
        "transaction_costs_paid",
        "qqq_benchmark_value",
        "qqq_benchmark_daily_profit",
        "qqq_benchmark_daily_return",
        "qqq_benchmark_cumulative_profit",
        "qqq_benchmark_cumulative_return",
    ]
    return daily[position_columns].copy(), daily[change_columns].copy()


def plot_daily_curve(daily, filename="sma200_daily_curve.png"):
    """绘制策略、QQQ 基准和累计投入的每日曲线，并保存为 PNG。"""
    import matplotlib

    matplotlib.use("Agg", force=True)
    import matplotlib.pyplot as plt

    required_columns = {
        "trade_date",
        "total_value",
        "qqq_benchmark_value",
        "total_contributions",
    }
    if not required_columns.issubset(daily.columns):
        raise ValueError("daily 缺少绘制资产曲线所需的列")
    if daily.empty:
        raise ValueError("daily 不能为空")

    dates = pd.to_datetime(daily["trade_date"])
    sma_column = next(
        (column for column in daily.columns if column.startswith("qqq_sma")),
        "qqq_sma",
    )
    sma_label = sma_column.removeprefix("qqq_sma")
    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, daily["total_value"] / 1_000_000, label="Strategy", linewidth=1.5)
    ax.plot(
        dates,
        daily["qqq_benchmark_value"] / 1_000_000,
        label="QQQ benchmark",
        linewidth=1.3,
    )
    ax.plot(
        dates,
        daily["total_contributions"] / 1_000_000,
        label="Total contributions",
        linewidth=1.2,
    )
    ax.set_title(f"QQQ SMA{sma_label} Regime / TQQQ Dip-Buy Strategy")
    ax.set_xlabel("Date")
    ax.set_ylabel("Value (million)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()

    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    from get_data import load_data

    qqq = load_data("qqq_daily.csv")
    tqqq = load_data("tqqq_daily.csv")
    bil = load_data("bil_daily.csv")
    daily, trades, summary = backtest_qqq_sma_tqqq(qqq, tqqq, bil)
    positions, changes = report_tables(daily)
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    daily.to_csv(report_dir / "daily_full.csv", index=False, encoding="utf-8-sig")
    trades.to_csv(report_dir / "trade_details.csv", index=False, encoding="utf-8-sig")
    positions.to_csv(report_dir / "daily_positions.csv", index=False, encoding="utf-8-sig")
    changes.to_csv(report_dir / "daily_changes.csv", index=False, encoding="utf-8-sig")
    chart_path = plot_daily_curve(daily)
    print(trades.tail())
    print(summary)
    print(f"详细账本已保存到 {report_dir.resolve()}")
    print(f"每日曲线已保存到 {chart_path.resolve()}")
