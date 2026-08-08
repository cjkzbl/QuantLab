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


def _signal(close, sma):
    if pd.isna(sma):
        return "sma_unavailable"
    if close > sma:
        return "above_sma"
    if close < sma:
        return "below_sma"
    return "equal_sma"


def determine_next_action(latest_signal, current_position, sma_window):
    """把均线状态与当前仓位转换为下一交易日的实际操作。"""
    if latest_signal == "above_sma":
        signal_text = f"QQQ 收盘高于 SMA{sma_window}"
        if current_position == "cash":
            return {
                "signal_text": signal_text,
                "action": "buy",
                "action_text": "买入 TQQQ",
                "reason": "当前空仓，QQQ 收盘高于均线",
            }
        return {
            "signal_text": signal_text,
            "action": "hold",
            "action_text": "不动，继续持有 TQQQ",
            "reason": "当前已持仓，QQQ 收盘仍高于均线",
        }

    if latest_signal == "below_sma":
        signal_text = f"QQQ 收盘低于 SMA{sma_window}"
        if current_position == "TQQQ":
            return {
                "signal_text": signal_text,
                "action": "sell",
                "action_text": "卖出 TQQQ",
                "reason": "当前持仓，QQQ 收盘低于均线",
            }
        return {
            "signal_text": signal_text,
            "action": "hold",
            "action_text": "不动，继续持有现金",
            "reason": "当前已空仓，QQQ 收盘仍低于均线",
        }

    return {
        "signal_text": f"QQQ 与 SMA{sma_window} 相等或均线尚不可用",
        "action": "hold",
        "action_text": "不动",
        "reason": "没有产生明确的买入或卖出信号",
    }


def backtest_qqq_sma_tqqq(
    qqq_df,
    tqqq_df,
    initial_capital=500_000,
    monthly_contribution=10_000,
    sma_window=200,
    commission_rate=0.001,
    slippage_rate=0.002,
    sell_fee_rate=0.001,
    capital_gains_tax_rate=0.0,
):
    """
    回测 QQQ SMA 择时、TQQQ 交易策略。

    首日一次性投入初始本金买入 TQQQ。之后使用 QQQ 前一交易日收盘价
    与 SMA 的关系，在下一交易日开盘全仓买入或清仓。每月首个交易日将
    定投资金加入现金池；策略空仓转为持仓时，将现金池一并投入。

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

    rates = {
        "commission_rate": commission_rate,
        "slippage_rate": slippage_rate,
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
            signal_date = data.at[i - 1, "trade_date"]
            execution_signal = _signal(previous_close, previous_sma)

            if execution_signal == "above_sma" and shares == 0 and cash > 0:
                cash_before_action = cash
                trade = _buy_details(
                    cash,
                    float(row["tqqq_open"]),
                    commission_rate,
                    slippage_rate,
                )
                shares = trade["shares"]
                cost_basis = trade["cost_basis"]
                cash = max(0.0, cash + trade["net_cash_flow"])
                transaction_costs_paid += trade["total_transaction_cost"]
                transaction_cost_today += trade["total_transaction_cost"]
                buy_count += 1
                action = "buy"
                action_reason = f"前一日 QQQ 收盘高于 SMA{sma_window}"
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
            elif execution_signal == "below_sma" and shares > 0:
                cash_before_action = cash
                shares_before_action = shares
                cost_basis_before_action = cost_basis
                trade = _sell_details(
                    shares,
                    float(row["tqqq_open"]),
                    cost_basis,
                    commission_rate,
                    slippage_rate,
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
                action_reason = f"前一日 QQQ 收盘低于 SMA{sma_window}"
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

        if i == 0:
            cash_before_action = cash
            trade = _buy_details(
                cash,
                float(row["tqqq_open"]),
                commission_rate,
                slippage_rate,
            )
            shares = trade["shares"]
            cost_basis = trade["cost_basis"]
            cash = max(0.0, cash + trade["net_cash_flow"])
            transaction_costs_paid += trade["total_transaction_cost"]
            transaction_cost_today = trade["total_transaction_cost"]
            buy_count = 1
            action = "initial_buy"
            action_reason = "回测首日按规则全仓买入 TQQQ"
            trade_records.append(
                {
                    "trade_id": 1,
                    "trade_date": date,
                    "signal_date": pd.NaT,
                    "side": "buy",
                    "reason": action_reason,
                    "qqq_signal_close": float("nan"),
                    f"qqq_sma{sma_window}": float("nan"),
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
                    "cost_basis_before": 0.0,
                    "cost_basis_after": cost_basis,
                    "realized_profit": 0.0,
                }
            )

        if i == 0 or contribution > 0:
            qqq_buy = _buy_details(
                qqq_cash,
                float(row["qqq_open"]),
                commission_rate,
                slippage_rate,
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
                slippage_rate,
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
            slippage_rate,
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
        current_signal = _signal(float(row["qqq_close"]), row["qqq_sma"])
        if current_signal == "above_sma":
            next_instruction = "下一交易日持有；若空仓则全仓买入 TQQQ"
        elif current_signal == "below_sma":
            next_instruction = "下一交易日清仓 TQQQ"
        else:
            next_instruction = "SMA 不可用或相等，下一交易日不交易"

        avg_cost_per_share = cost_basis / shares if shares > 0 else 0.0
        records.append(
            {
                "trade_date": date,
                "qqq_open": float(row["qqq_open"]),
                "qqq_close": float(row["qqq_close"]),
                f"qqq_sma{sma_window}": (
                    float(row["qqq_sma"]) if pd.notna(row["qqq_sma"]) else float("nan")
                ),
                "qqq_daily_change": (
                    float(row["qqq_close"]) / float(data.at[i - 1, "qqq_close"]) - 1
                    if i > 0
                    else float("nan")
                ),
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
    latest_signal = str(latest["close_signal"])
    next_decision = determine_next_action(
        latest_signal,
        current_position,
        sma_window,
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
        "commission_rate": float(commission_rate),
        "slippage_rate": float(slippage_rate),
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
        "average_cost_per_share",
        "cost_basis",
        "tqqq_close",
        "position_market_value",
        "position_liquidation_value",
        "unrealized_profit_before_exit_cost",
        "exposure_ratio",
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
    ax.set_title(f"QQQ SMA{sma_label} Timing Strategy")
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
    daily, trades, summary = backtest_qqq_sma_tqqq(qqq, tqqq)
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
