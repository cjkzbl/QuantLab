"""固定持有期的滚动起点风险分析。"""

from __future__ import annotations

import math

import pandas as pd

from strategy import prepare_backtest_data


HORIZONS = (1, 3, 5)
MODES = {"lump_sum": 0.0, "dca": 10_000.0}


def first_trading_days(dates):
    """返回每个月第一个可用交易日。"""
    values = pd.Series(pd.to_datetime(dates)).dropna().sort_values().drop_duplicates()
    frame = pd.DataFrame({"trade_date": values})
    return frame.groupby(frame["trade_date"].dt.to_period("M"))["trade_date"].min().tolist()


def path_metrics(daily, start_date=None, end_date=None):
    """从每日时间加权收益计算区间收益和回撤。"""
    frame = daily.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    if start_date is not None:
        frame = frame[frame["trade_date"] >= pd.Timestamp(start_date)]
    if end_date is not None:
        frame = frame[frame["trade_date"] <= pd.Timestamp(end_date)]
    if frame.empty:
        raise ValueError("分析窗口没有交易日")

    strategy_nav = (1.0 + frame["daily_return"].astype(float)).cumprod()
    benchmark_nav = (
        1.0 + frame["qqq_benchmark_daily_return"].astype(float)
    ).cumprod()
    strategy_with_initial = pd.concat(
        [pd.Series([1.0]), strategy_nav.reset_index(drop=True)], ignore_index=True
    )
    benchmark_with_initial = pd.concat(
        [pd.Series([1.0]), benchmark_nav.reset_index(drop=True)], ignore_index=True
    )
    strategy_drawdown = strategy_with_initial / strategy_with_initial.cummax() - 1.0
    benchmark_drawdown = benchmark_with_initial / benchmark_with_initial.cummax() - 1.0
    dated_nav = strategy_nav.reset_index(drop=True)
    dated_drawdown = dated_nav / dated_nav.cummax() - 1.0
    trough_position = int(dated_drawdown.to_numpy().argmin())
    peak_value = float(dated_nav.iloc[: trough_position + 1].max())
    peak_candidates = dated_nav.iloc[: trough_position + 1]
    peak_position = int(peak_candidates[peak_candidates >= peak_value - 1e-12].index[-1])
    if float(dated_drawdown.min()) >= -1e-12:
        recovered = True
        recovery_position = peak_position
    else:
        recovery_candidates = dated_nav.iloc[trough_position + 1 :]
        recovery_candidates = recovery_candidates[
            recovery_candidates >= peak_value - 1e-12
        ]
        recovered = not recovery_candidates.empty
        recovery_position = int(recovery_candidates.index[0]) if recovered else None
    underwater_runs = []
    current_run = 0
    for is_underwater in (dated_drawdown < -1e-12):
        if is_underwater:
            current_run += 1
        else:
            if current_run:
                underwater_runs.append(current_run)
            current_run = 0
    if current_run:
        underwater_runs.append(current_run)
    elapsed_years = max(
        (frame.iloc[-1]["trade_date"] - frame.iloc[0]["trade_date"]).days / 365.2425,
        1 / 365.2425,
    )
    total_return = float(strategy_nav.iloc[-1] - 1.0)
    qqq_total_return = float(benchmark_nav.iloc[-1] - 1.0)
    return {
        "start_date": pd.Timestamp(frame.iloc[0]["trade_date"]),
        "end_date": pd.Timestamp(frame.iloc[-1]["trade_date"]),
        "observations": int(len(frame)),
        "elapsed_years": float(elapsed_years),
        "total_return": total_return,
        "cagr": float((1.0 + total_return) ** (1.0 / elapsed_years) - 1.0),
        "max_drawdown": float(strategy_drawdown.min()),
        "qqq_total_return": qqq_total_return,
        "qqq_cagr": float((1.0 + qqq_total_return) ** (1.0 / elapsed_years) - 1.0),
        "qqq_max_drawdown": float(benchmark_drawdown.min()),
        "drawdown_peak_date": pd.Timestamp(frame.iloc[peak_position]["trade_date"]),
        "drawdown_trough_date": pd.Timestamp(frame.iloc[trough_position]["trade_date"]),
        "recovery_date": (
            pd.Timestamp(frame.iloc[recovery_position]["trade_date"])
            if recovered
            else pd.NaT
        ),
        "recovery_days": (
            float(recovery_position - peak_position) if recovered else math.nan
        ),
        "recovered_within_window": bool(recovered),
        "max_underwater_days": int(max(underwater_runs, default=0)),
    }


def xirr(cash_flows):
    """计算只有一次符号变化的年度资金加权收益率。"""
    flows = [(pd.Timestamp(date), float(amount)) for date, amount in cash_flows]
    if not flows or not any(amount < 0 for _, amount in flows) or not any(
        amount > 0 for _, amount in flows
    ):
        return math.nan
    base_date = min(date for date, _ in flows)

    def npv(rate):
        return sum(
            amount / ((1.0 + rate) ** ((date - base_date).days / 365.2425))
            for date, amount in flows
        )

    low = -0.9999
    high = 10.0
    while npv(high) > 0 and high < 1_000_000:
        high *= 10.0
    if npv(low) * npv(high) > 0:
        return math.nan
    for _ in range(160):
        middle = (low + high) / 2.0
        if npv(middle) > 0:
            low = middle
        else:
            high = middle
    return float((low + high) / 2.0)


def _run_strategy_path(
    prepared, start_date, end_date=None, monthly_contribution=0.0
):
    """用与完整回测相同的状态机快速计算收益路径。"""
    data = prepared[prepared["trade_date"] >= pd.Timestamp(start_date)].copy()
    if end_date is not None:
        data = data[data["trade_date"] <= pd.Timestamp(end_date)]
    data = data.reset_index(drop=True)
    if data.empty:
        raise ValueError("分析起点不在可用行情内")

    initial_capital = 10_000.0
    tqqq_slippage = 0.0005
    bil_slippage = 0.0001
    qqq_slippage = 0.0001
    sell_fee = 0.0000206
    dip_threshold = 0.01
    cash = initial_capital
    tqqq_shares = 0.0
    bil_shares = 0.0
    qqq_shares = 0.0
    qqq_cash = initial_capital
    previous_total = None
    previous_qqq = None
    records = []
    rows = list(data.itertuples(index=False))

    def buy(cash_value, price, slippage):
        return cash_value / (price * (1.0 + slippage))

    def sell(shares, price, slippage):
        return shares * price * (1.0 - slippage) * (1.0 - sell_fee)

    for index, row in enumerate(rows):
        contribution = 0.0
        if index > 0:
            previous = rows[index - 1]
            if pd.Timestamp(row.trade_date).to_period("M") != pd.Timestamp(
                previous.trade_date
            ).to_period("M"):
                contribution = float(monthly_contribution)
                cash += contribution
                qqq_cash += contribution
            previous_regime = str(previous.market_regime)
            regime_before = str(rows[index - 2].market_regime) if index > 1 else "neutral"
            bear_to_bull = regime_before == "bear" and previous_regime == "bull"
            dip_buy = (
                previous_regime == "bull"
                and pd.notna(previous.qqq_daily_change)
                and float(previous.qqq_daily_change) <= -dip_threshold
            )
            if previous_regime == "bear" and tqqq_shares > 0:
                cash += sell(tqqq_shares, float(row.tqqq_open), tqqq_slippage)
                tqqq_shares = 0.0
                bil_shares = buy(cash, float(row.bil_open), bil_slippage)
                cash = 0.0
            elif (bear_to_bull or dip_buy) and (bil_shares > 0 or cash > 0):
                if bil_shares > 0:
                    cash += sell(bil_shares, float(row.bil_open), bil_slippage)
                    bil_shares = 0.0
                tqqq_shares += buy(cash, float(row.tqqq_open), tqqq_slippage)
                cash = 0.0

        if index == 0:
            bil_shares = buy(cash, float(row.bil_open), bil_slippage)
            cash = 0.0
        elif bil_shares > 0 and cash > 0:
            bil_shares += buy(cash, float(row.bil_open), bil_slippage)
            cash = 0.0

        if index == 0 or contribution > 0:
            qqq_shares += buy(qqq_cash, float(row.qqq_open), qqq_slippage)
            qqq_cash = 0.0

        total_value = cash
        total_value += sell(tqqq_shares, float(row.tqqq_close), tqqq_slippage)
        total_value += sell(bil_shares, float(row.bil_close), bil_slippage)
        qqq_value = qqq_cash + sell(
            qqq_shares, float(row.qqq_close), qqq_slippage
        )
        daily_return = (
            total_value / initial_capital - 1.0
            if previous_total is None
            else (total_value - previous_total - contribution)
            / (previous_total + contribution)
        )
        qqq_daily_return = (
            qqq_value / initial_capital - 1.0
            if previous_qqq is None
            else (qqq_value - previous_qqq - contribution)
            / (previous_qqq + contribution)
        )
        records.append(
            {
                "trade_date": row.trade_date,
                "daily_return": daily_return,
                "qqq_benchmark_daily_return": qqq_daily_return,
                "total_value": total_value,
                "qqq_benchmark_value": qqq_value,
                "contribution": initial_capital if index == 0 else contribution,
            }
        )
        previous_total = total_value
        previous_qqq = qqq_value
    return pd.DataFrame(records)


def _run_lump_sum_path(prepared, start_date, end_date=None):
    return _run_strategy_path(
        prepared, start_date, end_date=end_date, monthly_contribution=0.0
    )


def _build_cohorts(
    prepared,
    matched_requests=None,
):
    last_date = pd.Timestamp(prepared["trade_date"].max())
    results = []
    matched_returns = {mode: [] for mode in MODES}
    matched_requests = matched_requests or {}
    for requested_start in first_trading_days(prepared["trade_date"]):
        needs_horizon = (
            requested_start + pd.DateOffset(years=min(HORIZONS)) <= last_date
        )
        needs_match = any(
            requested_start < pd.Timestamp(settings["before"])
            for settings in matched_requests.values()
        )
        if not needs_horizon and not needs_match:
            continue
        for mode, monthly_contribution in MODES.items():
            daily = _run_strategy_path(
                prepared,
                requested_start,
                end_date=min(
                    last_date, requested_start + pd.DateOffset(years=max(HORIZONS))
                ),
                monthly_contribution=monthly_contribution,
            )
            actual_start = pd.Timestamp(daily.iloc[0]["trade_date"])
            match = matched_requests.get(mode)
            if match and actual_start < pd.Timestamp(match["before"]):
                observation_count = int(match["observations"])
                if len(daily) >= observation_count:
                    matched = daily.iloc[:observation_count]
                    if pd.Timestamp(matched.iloc[-1]["trade_date"]) < pd.Timestamp(
                        match["before"]
                    ):
                        matched_returns[mode].append(
                            path_metrics(matched)["total_return"]
                        )
            for horizon in HORIZONS:
                anniversary = actual_start + pd.DateOffset(years=horizon)
                if anniversary > last_date:
                    continue
                window = daily[daily["trade_date"] <= anniversary]
                metrics = path_metrics(window)
                strategy_display = (
                    metrics["total_return"] if horizon == 1 else metrics["cagr"]
                )
                benchmark_display = (
                    metrics["qqq_total_return"]
                    if horizon == 1
                    else metrics["qqq_cagr"]
                )
                cash_flows = [
                    (row.trade_date, -float(row.contribution))
                    for row in window.itertuples(index=False)
                    if float(row.contribution) > 0
                ]
                cash_flows.append(
                    (window.iloc[-1]["trade_date"], float(window.iloc[-1]["total_value"]))
                )
                total_contributions = float(window["contribution"].sum())
                results.append(
                    {
                        "mode": mode,
                        "horizon_years": horizon,
                        **metrics,
                        "money_weighted_return": xirr(cash_flows),
                        "total_contributions": total_contributions,
                        "final_value": float(window.iloc[-1]["total_value"]),
                        "return_on_contributions": float(
                            window.iloc[-1]["total_value"] / total_contributions - 1.0
                        ),
                        "display_return": strategy_display,
                        "qqq_display_return": benchmark_display,
                        "excess_return": strategy_display - benchmark_display,
                        "outperformed_qqq": strategy_display > benchmark_display,
                        "positive_return": strategy_display > 0,
                    }
                )
    return pd.DataFrame(results), matched_returns


def build_rolling_start_analysis(qqq, tqqq, bil, sma_window=200):
    """按月度起点生成完整 1/3/5 年 Lump Sum 样本。"""
    prepared = prepare_backtest_data(qqq, tqqq, bil, sma_window=sma_window)
    results, _ = _build_cohorts(prepared)
    return results, prepared


def summarize_rolling_results(results):
    """生成每个固定持有期的统计摘要。"""
    working = results.copy()
    if "mode" not in working:
        working["mode"] = "lump_sum"
    summaries = []
    for (mode, horizon), group in working.groupby(
        ["mode", "horizon_years"], sort=True
    ):
        values = group["display_return"].astype(float)
        summaries.append(
            {
                "mode": mode,
                "horizon_years": int(horizon),
                "sample_count": int(len(group)),
                "mean": float(values.mean()),
                "median": float(values.median()),
                "p25": float(values.quantile(0.25)),
                "p10": float(values.quantile(0.10)),
                "worst": float(values.min()),
                "best": float(values.max()),
                "median_max_drawdown": float(group["max_drawdown"].median()),
                "worst_max_drawdown": float(group["max_drawdown"].min()),
                "positive_rate": float(group["positive_return"].mean()),
                "outperformance_rate": float(group["outperformed_qqq"].mean()),
                "recovery_rate": float(group["recovered_within_window"].mean()),
                "median_recovery_days": float(
                    group.loc[group["recovered_within_window"], "recovery_days"].median()
                ),
                "median_underwater_days": float(group["max_underwater_days"].median()),
                "median_money_weighted_return": float(
                    group["money_weighted_return"].median()
                )
                if "money_weighted_return" in group
                else math.nan,
            }
        )
    return pd.DataFrame(summaries)


def current_start_snapshot(
    prepared,
    live_start_date=None,
    historical_returns=None,
    mode="lump_sum",
):
    """计算当前实盘起点及等时长历史百分位。"""
    last_date = pd.Timestamp(prepared["trade_date"].max())
    if live_start_date is None:
        live_start_date = pd.Timestamp(year=last_date.year, month=1, day=1)
    requested = pd.Timestamp(live_start_date).normalize()
    if requested > last_date:
        raise ValueError("live_start_date 晚于可用行情的最后交易日")
    current_daily = _run_strategy_path(
        prepared, requested, monthly_contribution=MODES[mode]
    )
    metrics = path_metrics(current_daily)
    observation_count = metrics["observations"]

    historical_returns = historical_returns or []
    percentile = math.nan
    if historical_returns:
        percentile = sum(value <= metrics["total_return"] for value in historical_returns)
        percentile /= len(historical_returns)
    return {
        **metrics,
        "mode": mode,
        "money_weighted_return": xirr(
            [
                (row.trade_date, -float(row.contribution))
                for row in current_daily.itertuples(index=False)
                if float(row.contribution) > 0
            ]
            + [
                (
                    current_daily.iloc[-1]["trade_date"],
                    float(current_daily.iloc[-1]["total_value"]),
                )
            ]
        ),
        "return_percentile": float(percentile),
        "matched_sample_count": int(len(historical_returns)),
        "is_full_year": bool(metrics["elapsed_years"] >= 1.0),
    }


def analyze_start_date_risk(
    qqq, tqqq, bil, sma_window=200, live_start_date=None
):
    """一次遍历生成第一阶段需要的滚动样本、摘要和当前起点。"""
    prepared = prepare_backtest_data(qqq, tqqq, bil, sma_window=sma_window)
    current = {
        mode: current_start_snapshot(
            prepared, live_start_date=live_start_date, mode=mode
        )
        for mode in MODES
    }
    results, matched_returns = _build_cohorts(
        prepared,
        matched_requests={
            mode: {
                "observations": snapshot["observations"],
                "before": snapshot["start_date"],
            }
            for mode, snapshot in current.items()
        },
    )
    for mode, snapshot in current.items():
        samples = matched_returns[mode]
        snapshot["matched_sample_count"] = len(samples)
        snapshot["return_percentile"] = (
            sum(value <= snapshot["total_return"] for value in samples) / len(samples)
            if samples
            else math.nan
        )
    transitions = build_regime_transition_analysis(prepared)
    return results, summarize_rolling_results(results), current, transitions


def build_regime_transition_analysis(prepared):
    """汇总 QQQ 从牛市切换至熊市时的信号滞后损失。"""
    data = prepared.reset_index(drop=True)
    transitions = []
    for index in range(1, len(data) - 1):
        if not (
            str(data.at[index - 1, "market_regime"]) == "bull"
            and str(data.at[index, "market_regime"]) == "bear"
        ):
            continue
        bull_start = index - 1
        while bull_start > 0 and str(data.at[bull_start - 1, "market_regime"]) == "bull":
            bull_start -= 1
        bull_episode = data.iloc[bull_start : index + 1]
        peak_index = int(bull_episode["qqq_close"].idxmax())
        execution_index = index + 1
        qqq_peak = float(data.at[peak_index, "qqq_close"])
        tqqq_peak = float(data.at[peak_index, "tqqq_close"])
        transitions.append(
            {
                "peak_date": pd.Timestamp(data.at[peak_index, "trade_date"]),
                "signal_date": pd.Timestamp(data.at[index, "trade_date"]),
                "execution_date": pd.Timestamp(
                    data.at[execution_index, "trade_date"]
                ),
                "days_to_exit": int(execution_index - peak_index),
                "qqq_peak_to_exit": float(
                    data.at[execution_index, "qqq_open"] / qqq_peak - 1.0
                ),
                "tqqq_peak_to_exit": float(
                    data.at[execution_index, "tqqq_open"] / tqqq_peak - 1.0
                ),
                "qqq_peak_close": qqq_peak,
                "tqqq_peak_close": tqqq_peak,
                "qqq_exit_open": float(data.at[execution_index, "qqq_open"]),
                "tqqq_exit_open": float(data.at[execution_index, "tqqq_open"]),
            }
        )
    return pd.DataFrame(transitions)
