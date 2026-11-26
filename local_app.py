"""Generate and optionally serve the QuantLab 50/50 static dashboard."""

import argparse
import html
import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from allocation_strategy import (
    ASSETS, BACKTEST_START, CAPITAL, SYMBOLS, backtest_allocation,
    daily_summary, performance, prepare_allocation,
)
from get_data import STRATEGY_DATA_DIR, load_strategy_market_data, refresh_strategy_market_data
from interactive_chart import build_interactive_market_chart


PUBLIC_DIR = Path("public")
REPORT_DIR = Path("reports")
SOURCE_CSS = Path(__file__).with_name("dashboard.css")
ACTION_LABELS = {"initial": "初始投入", "signal": "目标变动", "funding": "月度投入", "signal_and_funding": "目标变动与月度投入"}


def fmt(value, kind):
    if pd.isna(value):
        return "—"
    if kind == "date":
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if kind == "percent":
        return f"{value:.2%}"
    if kind == "signed":
        return f"{value:+.2%}"
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "shares":
        return f"{value:,.6f}"
    return html.escape(str(value))


def page(title, body, *, root=""):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="QuantLab QQQ / TQQQ 50/50 趋势与波动策略历史模拟，防守资金配置 BIL">
<title>{html.escape(title)} · QuantLab</title><link rel="stylesheet" href="{root}assets/dashboard.css">
</head><body><header class="topbar"><div class="shell">
  <a class="wordmark" href="{root}index.html">QuantLab</a>
  <nav aria-label="主导航"><a href="{root}index.html">策略概览</a><a href="{root}start-date-risk.html">起点风险</a><a href="{root}reports/trades.html">交易明细</a></nav>
</div></header><main class="shell">{body}
<footer class="footer">QuantLab · 历史模拟。金额均按名义账户单位列示。</footer></main></body></html>'''


def table(frame, columns, *, scroll=False):
    fields, labels, kinds = zip(*columns)
    heading = "".join(f'<th scope="col">{html.escape(label)}</th>' for label in labels)
    rows = []
    for values in frame.loc[:, list(fields)].itertuples(index=False, name=None):
        rows.append("<tr>" + "".join(f"<td>{fmt(value, kind)}</td>" for value, kind in zip(values, kinds)) + "</tr>")
    if not rows:
        rows = [f'<tr><td colspan="{len(fields)}">暂无完整样本</td></tr>']
    classes = "table-wrap data-table" if scroll else "table-wrap"
    return f'<div class="{classes}"><table><thead><tr>{heading}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'


TRADE_COLUMNS = [
    ("trade_date", "成交日", "date"), ("asset", "标的", "text"), ("side", "方向", "text"),
    ("reason", "调仓原因", "text"), ("signal_date", "信号日", "date"), ("shares", "模型股数", "shares"),
    ("market_price", "后复权开盘价", "money"), ("execution_price", "模拟成交价", "money"),
    ("market_notional", "市场成交额", "money"), ("total_transaction_cost", "交易成本", "money"),
    ("net_cash_flow", "现金流", "money"), ("realized_profit", "已实现盈亏", "money"),
]
POSITION_COLUMNS = [("trade_date", "日期", "date"), ("total_value", "账户资产", "money")]
for asset in ASSETS:
    name = asset.lower()
    POSITION_COLUMNS += [
        (f"weight_{name}", f"{asset} 收盘权重", "percent"), (f"target_{name}", f"{asset} 开盘目标", "percent"),
        (f"shares_{name}", f"{asset} 模型股数", "shares"), (f"cost_basis_{name}", f"{asset} 成本", "money"),
    ]
POSITION_COLUMNS += [("contribution", "当日投入", "money"), ("total_contributions", "累计投入", "money")]
CHANGE_COLUMNS = [
    ("trade_date", "日期", "date"), ("daily_return", "策略日收益", "signed"),
    ("daily_profit", "净盈亏", "money"), ("drawdown", "净值回撤", "percent"),
    ("qqq_sma", "QQQ SMA200", "money"), ("active_volatility", "月度波动率", "percent"),
    *[(f"{asset.lower()}_daily_change", f"{asset} 日涨跌", "signed") for asset in SYMBOLS],
    ("transaction_cost", "交易成本", "money"), ("qqq_benchmark_daily_return", "QQQ 基准日收益", "signed"),
]
RISK_COLUMNS = [
    ("start_date", "开始日", "date"), ("end_date", "结束日", "date"), ("annualized_return", "策略年化", "signed"),
    ("max_drawdown", "策略最大回撤", "percent"), ("qqq_annualized_return", "QQQ 年化", "signed"),
    ("qqq_max_drawdown", "QQQ 最大回撤", "percent"), ("final_value", "策略期末资产", "money"),
]


def risk_reports(prepared, live_start_date):
    end = prepared.index[-1]
    start = max(prepared.index[0], pd.Timestamp(BACKTEST_START))
    requested = max(start, pd.Timestamp(live_start_date or f"{end.year}-01-01"))
    current, _ = backtest_allocation(prepared, requested, ledger=False)
    current = performance(current, cash_weighted=True)

    def window(left, right, monthly=CAPITAL):
        daily, _ = backtest_allocation(prepared, left, right, monthly=monthly, ledger=False)
        return performance(daily)

    phase = []
    for left, right in ((start, "2018-12-31"), ("2019-01-01", "2022-12-31"), ("2023-01-01", end)):
        left, right = max(pd.Timestamp(left), start), min(pd.Timestamp(right), end)
        if not prepared.loc[left:right].empty:
            phase.append(window(left, right))
    yearly = [window(max(start, pd.Timestamp(year=year, month=1, day=1)), end) for year in range(start.year, end.year + 1)]
    roll, summaries = {}, []
    for mode, monthly in (("lump_sum", 0.), ("dca", CAPITAL)):
        records = []
        for years in (1, 3, 5):
            period = []
            for left in pd.date_range(start, end, freq="QS"):
                right = left + pd.DateOffset(years=years)
                if right > end:
                    break
                period.append({"horizon_years": years, **window(left, right, monthly)})
            if not period:
                continue
            records.extend(period)
            cohort = pd.DataFrame(period)
            summaries.append({
                "mode": mode, "horizon_years": years, "windows": len(period),
                "median_cagr": float(cohort.annualized_return.median()),
                "worst_cagr": float(cohort.annualized_return.min()),
                "worst_drawdown": float(cohort.max_drawdown.min()),
                "joint_target_count": int((cohort.annualized_return.ge(.25) & cohort.max_drawdown.ge(-.50)).sum()),
            })
        roll[mode] = pd.DataFrame(records, columns=["horizon_years", *yearly[0]])
    return current, pd.DataFrame(phase), pd.DataFrame(yearly), roll, summaries


def dashboard_html(summary, trade_display):
    s = summary
    money = lambda value: f"{value / 10_000:,.2f} 万"
    annualized = fmt(s["annualized_return"], "percent")
    drawdown = fmt(s["max_drawdown"], "percent")
    change = s["latest_daily_return"]
    change_color = "gain" if change >= 0 else "loss"
    date = s["end_date"]
    targets = s["target_weights"]
    bar = "".join(f'<span class="{asset.lower()}" style="width:{weight:.6%}"></span>' for asset, weight in targets.items())
    weights = "".join(
        f'<tr><td class="{asset.lower()}"><strong>{asset}</strong></td><td>{s["current_weights"][asset]:.2%}</td>'
        f'<td><strong>{targets[asset]:.2%}</strong></td><td>{fmt(s["latest_changes"][asset], "signed")}</td></tr>'
        for asset in ASSETS
    )
    kpis = "".join(
        f'<article class="card"><span class="label">{label}</span><strong class="number {color}">{value}</strong><div class="sub">{note}</div></article>'
        for label, value, color, note in (
            ("时间加权年化", annualized, "gain", f'QQQ 定投 {fmt(s["qqq_annualized_return"], "percent")}'),
            ("最大净值回撤", drawdown, "loss", f'当前 {s["latest_drawdown"]:.2%}'),
            ("期末模拟资产", money(s["final_value"]), "", f'累计投入 {money(s["total_contributions"])}'),
            ("最新一日收益", fmt(change, "signed"), change_color, f'剔除投入的盈亏 {fmt(s["latest_daily_profit"], "money")}'),
        )
    )
    body = f'''
<section class="hero"><div><h1>QQQ / TQQQ 50/50</h1><p class="sub">趋势和波动率各占一半，决定 TQQQ 仓位。其余资金全部配置 BIL。</p></div>
<div class="timestamp">最新共同收盘<strong>{date}</strong></div></section>
<section class="grid metrics" aria-label="回测指标">{kpis}</section>
<section class="grid decision" aria-label="下次开盘目标和信号依据">
 <article class="card"><h2>下次开盘目标</h2><p class="target">{s['next_action']}</p>
 <p class="sub">按 {date} 收盘生成。首次跟踪本策略时可用开盘目标核对实际仓位。</p>
 <div class="allocation" aria-hidden="true">{bar}</div>
 <div class="table-wrap"><table><thead><tr><th>ETF</th><th>收盘持仓</th><th>开盘目标</th><th>日涨跌</th></tr></thead><tbody>{weights}</tbody></table></div></article>
 <article class="card"><h2>信号依据</h2><div class="signal-grid">
 <div><span class="label">趋势仓</span><strong>{s['trend_label']}</strong></div>
 <div><span class="label">QQQ 后复权收盘 / 日涨跌</span><strong>{fmt(s['qqq_close'],'money')} / {fmt(s['latest_changes']['QQQ'],'signed')}</strong></div>
 <div><span class="label">趋势开启线</span><strong>{fmt(s['qqq_entry_line'],'money')} · SMA200 × 1.04</strong></div>
 <div><span class="label">趋势退出线</span><strong>{fmt(s['qqq_sma'],'money')} · SMA200</strong></div>
 <div><span class="label">TQQQ 月度波动率</span><strong>{fmt(s['active_volatility'],'percent')}</strong></div>
 <div><span class="label">波动仓内 TQQQ 比例</span><strong>{fmt(s['volatility_tqqq_ratio'],'percent')}</strong></div>
 </div><p class="note">波动率取自 {s['volatility_sample_date']} 收盘。价格和均线采用后复权口径，收盘持仓权重随价格浮动。</p></article>
</section>
<section class="section" id="performance"><div class="section-head"><div><h2>收益、回撤与持仓</h2>
<p class="sub">{s['start_date']} 至 {date} · 初始和月度投入各 1 万 · 同现金流定投 QQQ 为基准</p></div>
<a class="button" href="interactive_market_chart.html" target="_blank" rel="noopener">全屏图表</a></div>
<div class="card chart"><iframe src="interactive_market_chart.html" title="策略净值、账户资产、回撤及 TQQQ、BIL 权重" loading="lazy"></iframe></div></section>
<section class="section"><div class="section-head"><h2>执行规则</h2><a class="button" href="start-date-risk.html">查看不同时期</a></div>
<div class="grid rules">
 <article class="card"><h3>趋势仓 · 50%</h3><p class="sub">QQQ 收盘高于 SMA200 的 104% 时开启，低于 SMA200 时退出。中间延续状态。</p></article>
 <article class="card"><h3>波动仓 · 50%</h3><p class="sub">每月首个共同交易日收盘更新。TQQQ 的目标比例为 30% 除以近 20 日年化波动率，上限 100%。</p></article>
 <article class="card"><h3>防守与成交</h3><p class="sub">合并两个仓的 TQQQ 权重，剩余全部配置 BIL。目标变化或有月度新增资金时，按开盘时已生效的目标模拟再平衡。</p></article>
</div></section>
<section class="section"><div class="section-head"><h2>最近模拟成交</h2><a class="button" href="reports/trades.html">完整交易记录</a></div>
<div class="card">{table(trade_display.tail(8).iloc[::-1], TRADE_COLUMNS[:8])}</div>
<div class="downloads"><a class="button" href="reports/positions.html">每日持仓</a><a class="button" href="reports/changes.html">每日收益</a>
<a class="button" href="reports/daily_full.csv" download>完整账本 CSV</a><a class="button" href="reports/summary.json">摘要 JSON</a></div></section>'''
    return page("QQQ / TQQQ 50/50", body)


def risk_html(current, phase, yearly, roll, summaries):
    summary_frame = pd.DataFrame(summaries, columns=["mode", "horizon_years", "windows", "median_cagr", "worst_cagr", "worst_drawdown", "joint_target_count"])
    summary_frame["mode"] = summary_frame["mode"].map({"lump_sum": "一次性投入", "dca": "每月定投"})
    summary_columns = [("mode", "投入方式", "text"), ("horizon_years", "持有年数", "text"), ("windows", "窗口数", "text"),
                       ("median_cagr", "年化中位数", "signed"), ("worst_cagr", "最差年化", "signed"),
                       ("worst_drawdown", "最深回撤", "percent"), ("joint_target_count", "双目标达标数", "text")]
    worst = roll["dca"].loc[roll["dca"].horizon_years.eq(3)].sort_values("annualized_return").head(10)
    body = f'''
<section class="hero"><div><h1>不同时期与起点风险</h1><p class="sub">每个起点用相同起始资金重新模拟。三个独立历史时段与按季滚动窗口使用真实 ETF 行情。</p></div></section>
<section class="card"><h2>当前起点</h2><p class="sub">{current['start_date']} 至 {current['end_date']} · 时间加权年化 {fmt(current['annualized_return'],'percent')}
 · 最大回撤 {fmt(current['max_drawdown'],'percent')} · 资金加权年化 {fmt(current['money_weighted_return'],'percent')}</p></section>
<section class="section"><h2>独立历史时段</h2><div class="card">{table(phase, RISK_COLUMNS)}</div></section>
<section class="section"><h2>1 / 3 / 5 年滚动样本</h2><p class="note">达到年化 25% 且回撤幅度不超过 50% 时记一次双目标达标。滚动窗口相互重叠。</p>
<div class="card">{table(summary_frame, summary_columns)}</div>
<div class="downloads"><a class="button" href="reports/rolling_start_dca.csv" download>定投样本 CSV</a><a class="button" href="reports/rolling_start_lump_sum.csv" download>一次性投入样本 CSV</a></div></section>
<section class="section"><h2>三年定投最弱起点</h2><div class="card">{table(worst, RISK_COLUMNS)}</div></section>
<section class="section"><div class="section-head"><h2>逐年起投至同一终点</h2><a class="button" href="reports/start_date_sensitivity.csv" download>下载 CSV</a></div>
<div class="card">{table(yearly, RISK_COLUMNS)}</div></section>'''
    return page("起点风险", body)


def build_dashboard(data_dir=STRATEGY_DATA_DIR, live_start_date=None, *, public_dir=PUBLIC_DIR, report_dir=REPORT_DIR):
    prepared = prepare_allocation(load_strategy_market_data(data_dir))
    daily, trades = backtest_allocation(prepared)
    summary = daily_summary(daily, trades)
    current, phase, yearly, roll, risk_summary = risk_reports(prepared, live_start_date)

    public_reports = public_dir / "reports"
    public_reports.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    (public_dir / "assets").mkdir(exist_ok=True)
    shutil.copyfile(SOURCE_CSS, public_dir / "assets/dashboard.css")
    (public_dir / ".nojekyll").touch()

    positions = daily[[column for column, _, _ in POSITION_COLUMNS]]
    changes = daily[[column for column, _, _ in CHANGE_COLUMNS]]
    for name, frame in {
        "daily_full.csv": daily, "trade_details.csv": trades, "daily_positions.csv": positions,
        "daily_changes.csv": changes, "phase_results.csv": phase, "start_date_sensitivity.csv": yearly,
        "rolling_start_dca.csv": roll["dca"], "rolling_start_lump_sum.csv": roll["lump_sum"],
    }.items():
        frame.to_csv(report_dir / name, index=False, encoding="utf-8-sig")
        shutil.copyfile(report_dir / name, public_reports / name)
    for name, data in (("summary.json", summary), ("rolling_start_summary.json", risk_summary)):
        payload = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
        (report_dir / name).write_text(payload, encoding="utf-8")
        (public_reports / name).write_text(payload, encoding="utf-8")
    display = trades.assign(side=trades.side.map({"buy": "买入", "sell": "卖出"}), reason=trades.reason.map(ACTION_LABELS))
    for name, title, frame, columns, csv in (
        ("trades", "全部交易明细", display, TRADE_COLUMNS, "trade_details.csv"),
        ("positions", "每日持仓明细", positions, POSITION_COLUMNS, "daily_positions.csv"),
        ("changes", "每日收益与行情", changes, CHANGE_COLUMNS, "daily_changes.csv"),
    ):
        body = f'''<div class="section-head"><div><h1>{title}</h1><p class="sub">共 {len(frame):,} 条，按日期倒序</p></div>
<a class="button" href="{csv}" download>下载 CSV</a></div><div class="card">{table(frame.iloc[::-1], columns, scroll=True)}</div>'''
        (public_reports / f"{name}.html").write_text(page(title, body, root="../"), encoding="utf-8")
    build_interactive_market_chart(daily, public_dir / "interactive_market_chart.html")
    (public_dir / "start-date-risk.html").write_text(risk_html(current, phase, yearly, roll, risk_summary), encoding="utf-8")
    index = public_dir / "index.html"
    index.write_text(dashboard_html(summary, display), encoding="utf-8")
    return index, summary


def serve(port, directory=PUBLIC_DIR):
    handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"本地策略看板：http://127.0.0.1:{port}")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description="QQQ / TQQQ 50/50 组合模拟看板")
    parser.add_argument("--data-dir", type=Path, default=STRATEGY_DATA_DIR)
    parser.add_argument("--refresh", action="store_true", help="先更新 QQQ、TQQQ 和 BIL 的复权行情")
    parser.add_argument("--live-start-date", help="起点风险页的自定义起点（YYYY-MM-DD）")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.refresh:
        refresh_strategy_market_data(args.data_dir)
    output, summary = build_dashboard(args.data_dir, args.live_start_date)
    print(f"页面已生成：{output.resolve()} · {summary['end_date']} · 最新目标 {summary['target_weights']}")
    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
