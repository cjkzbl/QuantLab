from pathlib import Path

import pandas as pd


def moving_average(df, window=20, price_column="close"):
    """计算指定价格列的简单移动平均线。"""
    if not isinstance(window, int) or isinstance(window, bool) or window <= 0:
        raise ValueError("window 必须是正整数")
    if price_column not in df.columns:
        raise ValueError(f"缺少价格列: {price_column}")

    average = df[price_column].rolling(window=window, min_periods=window).mean()
    average.name = f"ma{window}"
    return average


def _buy_all(cash, market_price, commission_rate, slippage_rate):
    execution_price = market_price * (1 + slippage_rate)
    shares = cash / (execution_price * (1 + commission_rate))
    commission = shares * execution_price * commission_rate
    slippage = shares * market_price * slippage_rate
    return shares, cash, commission + slippage


def _sell_all(
    shares,
    market_price,
    cost_basis,
    commission_rate,
    slippage_rate,
    sell_fee_rate,
    capital_gains_tax_rate,
):
    execution_price = market_price * (1 - slippage_rate)
    gross = shares * execution_price
    commission = gross * commission_rate
    sell_fee = gross * sell_fee_rate
    net_before_tax = gross - commission - sell_fee
    taxable_gain = max(0.0, net_before_tax - cost_basis)
    capital_gains_tax = taxable_gain * capital_gains_tax_rate
    slippage = shares * market_price * slippage_rate
    return (
        net_before_tax - capital_gains_tax,
        commission + sell_fee + slippage,
        capital_gains_tax,
    )


def backtest_qqq_sma_tqqq(
    qqq_df,
    tqqq_df,
    initial_capital=500_000,
    monthly_contribution=10_000,
    sma_window=225,
    commission_rate=0.001,
    slippage_rate=0.002,
    sell_fee_rate=0.001,
    capital_gains_tax_rate=0.0,
):
    """
    回测 QQQ SMA 择时、TQQQ 交易策略。

    QQQ 当日收盘价高于/低于 SMA 时，在下一交易日以 TQQQ 开盘价
    全仓买入/清仓。每月首个交易日把定投资金加入现金池，等下一次
    从空仓转为买入时一并投入。
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
    if not {"trade_date", "open", "close"}.issubset(tqqq_df.columns):
        raise ValueError("TQQQ 数据必须包含 trade_date、open 和 close 列")

    qqq = qqq_df[["trade_date", "open", "close"]].copy()
    qqq["trade_date"] = pd.to_datetime(qqq["trade_date"])
    qqq = qqq.sort_values("trade_date").drop_duplicates("trade_date")
    qqq["qqq_sma"] = moving_average(qqq, sma_window)
    qqq = qqq.rename(columns={"open": "qqq_open", "close": "qqq_close"})

    tqqq = tqqq_df[["trade_date", "open", "close"]].copy()
    tqqq["trade_date"] = pd.to_datetime(tqqq["trade_date"])
    tqqq = tqqq.sort_values("trade_date").drop_duplicates("trade_date")
    tqqq = tqqq.rename(
        columns={"open": "tqqq_open", "close": "tqqq_close"}
    )

    data = qqq.merge(tqqq, on="trade_date", how="inner")
    data = data.dropna(
        subset=["qqq_open", "qqq_close", "tqqq_open", "tqqq_close"]
    )
    data = data.reset_index(drop=True)
    if data.empty:
        raise ValueError("QQQ 和 TQQQ 没有可用于回测的共同交易日")
    if (
        data[["qqq_open", "qqq_close", "tqqq_open", "tqqq_close"]] <= 0
    ).any().any():
        raise ValueError("QQQ 和 TQQQ 的开盘价、收盘价必须大于 0")

    cash = 0.0
    shares, cost_basis, initial_cost = _buy_all(
        float(initial_capital),
        float(data.at[0, "tqqq_open"]),
        commission_rate,
        slippage_rate,
    )
    qqq_benchmark_shares, qqq_cost_basis, qqq_initial_cost = _buy_all(
        float(initial_capital),
        float(data.at[0, "qqq_open"]),
        commission_rate,
        slippage_rate,
    )
    transaction_costs_paid = initial_cost
    capital_gains_taxes_paid = 0.0
    qqq_transaction_costs_paid = qqq_initial_cost
    total_contributions = float(initial_capital)
    buy_count = 1
    sell_count = 0
    records = []

    for i, row in data.iterrows():
        contribution = 0.0
        action = "hold"

        if i == 0:
            action = "initial_buy"
        else:
            current_month = row["trade_date"].to_period("M")
            previous_month = data.at[i - 1, "trade_date"].to_period("M")
            if current_month != previous_month:
                contribution = float(monthly_contribution)
                cash += contribution
                total_contributions += contribution
                new_qqq_shares, new_qqq_basis, qqq_buy_cost = _buy_all(
                    contribution,
                    float(row["qqq_open"]),
                    commission_rate,
                    slippage_rate,
                )
                qqq_benchmark_shares += new_qqq_shares
                qqq_cost_basis += new_qqq_basis
                qqq_transaction_costs_paid += qqq_buy_cost

            previous_close = float(data.at[i - 1, "qqq_close"])
            previous_sma = data.at[i - 1, "qqq_sma"]
            if pd.notna(previous_sma) and previous_close > float(previous_sma):
                if shares == 0 and cash > 0:
                    shares, cost_basis, buy_cost = _buy_all(
                        cash,
                        float(row["tqqq_open"]),
                        commission_rate,
                        slippage_rate,
                    )
                    cash = 0.0
                    transaction_costs_paid += buy_cost
                    buy_count += 1
                    action = "buy"
            elif pd.notna(previous_sma) and previous_close < float(previous_sma):
                if shares > 0:
                    proceeds, sell_cost, capital_gains_tax = _sell_all(
                        shares,
                        float(row["tqqq_open"]),
                        cost_basis,
                        commission_rate,
                        slippage_rate,
                        sell_fee_rate,
                        capital_gains_tax_rate,
                    )
                    cash += proceeds
                    transaction_costs_paid += sell_cost
                    capital_gains_taxes_paid += capital_gains_tax
                    shares = 0.0
                    cost_basis = 0.0
                    sell_count += 1
                    action = "sell"

        position_value = shares * float(row["tqqq_close"])
        if shares > 0:
            net_position_value, exit_cost, exit_tax = _sell_all(
                shares,
                float(row["tqqq_close"]),
                cost_basis,
                commission_rate,
                slippage_rate,
                sell_fee_rate,
                capital_gains_tax_rate,
            )
        else:
            net_position_value, exit_cost, exit_tax = 0.0, 0.0, 0.0
        qqq_net_value, qqq_exit_cost, qqq_exit_tax = _sell_all(
            qqq_benchmark_shares,
            float(row["qqq_close"]),
            qqq_cost_basis,
            commission_rate,
            slippage_rate,
            sell_fee_rate,
            capital_gains_tax_rate,
        )
        records.append(
            {
                "trade_date": row["trade_date"],
                "qqq_open": float(row["qqq_open"]),
                "qqq_close": float(row["qqq_close"]),
                f"qqq_sma{sma_window}": (
                    float(row["qqq_sma"]) if pd.notna(row["qqq_sma"]) else float("nan")
                ),
                "tqqq_open": float(row["tqqq_open"]),
                "tqqq_close": float(row["tqqq_close"]),
                "monthly_contribution": contribution,
                "total_contributions": total_contributions,
                "action": action,
                "cash": cash,
                "shares": shares,
                "position_value": position_value,
                "total_value": cash + net_position_value,
                "qqq_benchmark_value": qqq_net_value,
                "transaction_costs_paid": transaction_costs_paid,
                "capital_gains_taxes_paid": capital_gains_taxes_paid,
                "estimated_exit_cost": exit_cost,
                "estimated_exit_tax": exit_tax,
                "qqq_estimated_exit_cost": qqq_exit_cost,
                "qqq_estimated_exit_tax": qqq_exit_tax,
            }
        )

    daily = pd.DataFrame(records)
    final_value = float(daily.iloc[-1]["total_value"])
    qqq_benchmark_final_value = float(
        daily.iloc[-1]["qqq_benchmark_value"]
    )
    profit = final_value - total_contributions
    summary = {
        "start_date": daily.iloc[0]["trade_date"],
        "end_date": daily.iloc[-1]["trade_date"],
        "initial_capital": float(initial_capital),
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
        "buy_count": buy_count,
        "sell_count": sell_count,
    }
    return daily, summary


def plot_daily_curve(daily, filename="sma225_daily_curve.png"):
    """绘制策略、QQQ 基准和累计投入的每日曲线，并保存为 PNG 文件。"""
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
        raise ValueError(
            "daily 缺少绘制策略、QQQ 基准或累计投入所需的列"
        )
    if daily.empty:
        raise ValueError("daily 不能为空")

    dates = pd.to_datetime(daily["trade_date"])
    total_value = daily["total_value"] / 1_000_000
    qqq_benchmark_value = daily["qqq_benchmark_value"] / 1_000_000
    total_contributions = daily["total_contributions"] / 1_000_000

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.plot(dates, total_value, label="Portfolio value", linewidth=1.5)
    ax.plot(
        dates,
        qqq_benchmark_value,
        label="QQQ benchmark",
        linewidth=1.3,
    )
    ax.plot(
        dates,
        total_contributions,
        label="Total contributions",
        linewidth=1.2,
    )
    ax.set_title("QQQ SMA225 Timing Strategy")
    ax.set_xlabel("Date")
    ax.set_ylabel("Value (million)")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.tight_layout()

    path = Path(filename)
    fig.savefig(path, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return path


if __name__ == "__main__":
    from get_data import load_data

    qqq = load_data("qqq_daily.csv")
    tqqq = load_data("tqqq_daily.csv")
    daily, summary = backtest_qqq_sma_tqqq(qqq, tqqq)
    chart_path = plot_daily_curve(daily)
    print(daily.tail())
    print(summary)
    print(f"每日曲线已保存到 {chart_path}")
