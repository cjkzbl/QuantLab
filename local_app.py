import argparse
import html
import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd

from get_data import load_data, refresh_and_save_market_data
from interactive_chart import build_interactive_market_chart
from strategy import backtest_qqq_sma_tqqq, plot_daily_curve, report_tables


PUBLIC_DIR = Path("public")
REPORT_DIR = Path("reports")
PUBLIC_REPORT_DIR = PUBLIC_DIR / "reports"


def refresh_market_data():
    """重新获取并保存 QQQ、TQQQ 后复权行情。"""
    result = refresh_and_save_market_data(max_age_days=7)
    print(
        "两份 CSV 已更新并重新校验："
        f"最新交易日={result['latest_date']}，"
        f"QQQ={result['qqq_rows']} 行，TQQQ={result['tqqq_rows']} 行"
    )
    return result


def _format_value(value, kind):
    if pd.isna(value):
        return "—"
    if kind == "date":
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    if kind == "money":
        return f"¥{float(value):,.2f}"
    if kind == "number":
        return f"{float(value):,.4f}"
    if kind == "shares":
        return f"{float(value):,.6f}"
    if kind == "percent":
        return f"{float(value):+.2%}"
    return html.escape(str(value))


def _write_table_page(filename, title, dataframe, columns):
    headers = "".join(f"<th>{html.escape(label)}</th>" for _, label, _ in columns)
    rows = []
    for row in dataframe.itertuples(index=False):
        values = row._asdict()
        cells = "".join(
            f"<td>{_format_value(values[column], kind)}</td>"
            for column, _, kind in columns
        )
        rows.append(f"<tr>{cells}</tr>")
    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ margin: 0; padding: 24px; background: Canvas; color: CanvasText; }}
    a {{ color: #1677ff; }}
    .toolbar {{ position: sticky; left: 0; margin-bottom: 16px; }}
    .table-wrap {{ overflow: auto; max-height: calc(100vh - 110px); border: 1px solid #8885; border-radius: 10px; }}
    table {{ border-collapse: collapse; min-width: 100%; white-space: nowrap; font-variant-numeric: tabular-nums; }}
    th,td {{ padding: 8px 10px; border-bottom: 1px solid #8884; text-align: right; }}
    th {{ position: sticky; top: 0; background: Canvas; z-index: 1; }}
    th:first-child,td:first-child {{ position: sticky; left: 0; background: Canvas; text-align: left; }}
    tbody tr:hover td {{ background: color-mix(in srgb, #1677ff 9%, Canvas); }}
  </style>
</head>
<body>
  <div class="toolbar"><a href="../index.html">← 返回概览</a> · 共 {len(dataframe):,} 行</div>
  <div class="table-wrap">
    <table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table>
  </div>
</body>
</html>
"""
    (PUBLIC_REPORT_DIR / filename).write_text(page, encoding="utf-8")


def _json_ready_summary(summary):
    result = {}
    for key, value in summary.items():
        if isinstance(value, pd.Timestamp):
            result[key] = value.strftime("%Y-%m-%d")
        elif hasattr(value, "item"):
            result[key] = value.item()
        else:
            result[key] = value
    return result


def save_reports(daily, trades, summary):
    """把全部交易、持仓和每日涨跌账本保存到本地及 Pages 目录。"""
    positions, changes = report_tables(daily)
    REPORT_DIR.mkdir(exist_ok=True)
    PUBLIC_REPORT_DIR.mkdir(parents=True, exist_ok=True)

    report_files = {
        "daily_full.csv": daily,
        "trade_details.csv": trades,
        "daily_positions.csv": positions,
        "daily_changes.csv": changes,
    }
    for filename, dataframe in report_files.items():
        local_path = REPORT_DIR / filename
        dataframe.to_csv(local_path, index=False, encoding="utf-8-sig")
        shutil.copy2(local_path, PUBLIC_REPORT_DIR / filename)

    summary_data = _json_ready_summary(summary)
    summary_text = json.dumps(summary_data, ensure_ascii=False, indent=2)
    (REPORT_DIR / "summary.json").write_text(summary_text, encoding="utf-8")
    (PUBLIC_REPORT_DIR / "summary.json").write_text(summary_text, encoding="utf-8")

    sma_column = next(column for column in daily.columns if column.startswith("qqq_sma"))
    sma_label = f"SMA{summary['sma_window']}"
    _write_table_page(
        "trades.html",
        "全部交易明细",
        trades.iloc[::-1],
        [
            ("trade_date", "交易日", "date"),
            ("side", "方向", "text"),
            ("reason", "原因", "text"),
            ("signal_date", "信号日", "date"),
            ("qqq_signal_close", "QQQ信号收盘", "number"),
            (sma_column, sma_label, "number"),
            ("tqqq_market_price", "TQQQ开盘价", "number"),
            ("execution_price", "成交价（含滑点）", "number"),
            ("shares", "股数", "shares"),
            ("execution_notional", "成交额", "money"),
            ("commission", "佣金", "money"),
            ("slippage_cost", "滑点成本", "money"),
            ("sell_fee", "卖出附加费", "money"),
            ("total_transaction_cost", "总交易成本", "money"),
            ("net_cash_flow", "现金流", "money"),
            ("cash_after", "交易后现金", "money"),
            ("realized_profit", "已实现盈亏", "money"),
        ],
    )
    _write_table_page(
        "positions.html",
        "每日持仓明细",
        positions.iloc[::-1],
        [
            ("trade_date", "日期", "date"),
            ("position_status", "仓位", "text"),
            ("action", "当日动作", "text"),
            ("monthly_contribution", "当月定投", "money"),
            ("cash", "现金", "money"),
            ("shares", "TQQQ股数", "shares"),
            ("average_cost_per_share", "平均成本", "number"),
            ("cost_basis", "持仓成本", "money"),
            ("tqqq_close", "TQQQ收盘", "number"),
            ("position_market_value", "持仓市值", "money"),
            ("position_liquidation_value", "清算净值", "money"),
            ("unrealized_profit_before_exit_cost", "未实现盈亏", "money"),
            ("exposure_ratio", "仓位比例", "percent"),
            ("total_value", "账户净值", "money"),
            ("estimated_exit_cost", "预估退出成本", "money"),
        ],
    )
    _write_table_page(
        "changes.html",
        "每日涨跌明细",
        changes.iloc[::-1],
        [
            ("trade_date", "日期", "date"),
            ("qqq_close", "QQQ收盘", "number"),
            (sma_column, sma_label, "number"),
            ("qqq_daily_change", "QQQ涨跌", "percent"),
            ("tqqq_close", "TQQQ收盘", "number"),
            ("tqqq_daily_change", "TQQQ涨跌", "percent"),
            ("close_signal", "收盘信号", "text"),
            ("total_value", "策略净值", "money"),
            ("daily_profit", "策略当日盈亏", "money"),
            ("daily_return", "策略当日涨跌", "percent"),
            ("cumulative_profit", "策略累计盈亏", "money"),
            ("cumulative_return", "策略累计收益率", "percent"),
            ("drawdown", "策略回撤", "percent"),
            ("transaction_cost_today", "当日交易成本", "money"),
            ("qqq_benchmark_value", "QQQ基准净值", "money"),
            ("qqq_benchmark_daily_profit", "QQQ当日盈亏", "money"),
            ("qqq_benchmark_daily_return", "QQQ当日涨跌", "percent"),
        ],
    )
    return positions, changes


def build_dashboard(sma_window=200):
    """运行模拟回测，生成本地报告和静态网页。"""
    qqq = load_data("qqq_daily.csv")
    tqqq = load_data("tqqq_daily.csv")
    daily, trades, summary = backtest_qqq_sma_tqqq(
        qqq,
        tqqq,
        sma_window=sma_window,
    )

    PUBLIC_DIR.mkdir(exist_ok=True)
    (PUBLIC_DIR / ".nojekyll").touch()
    chart_filename = f"sma{sma_window}_daily_curve.png"
    plot_daily_curve(daily, PUBLIC_DIR / chart_filename)
    build_interactive_market_chart(
        daily,
        trades,
        qqq,
        tqqq,
        PUBLIC_DIR / "interactive_market_chart.html",
    )
    save_reports(daily, trades, summary)

    latest = daily.iloc[-1]
    sma_column = next(column for column in daily.columns if column.startswith("qqq_sma"))
    signal = "持有 TQQQ" if latest["position_status"] == "TQQQ" else "持有现金"
    action_names = {"initial_buy": "首次买入", "buy": "买入", "sell": "清仓"}
    recent_trades = trades.tail(10).iloc[::-1]
    rows = "".join(
        "<tr>"
        f"<td>{row.trade_date:%Y-%m-%d}</td>"
        f"<td>{action_names.get(row.side, html.escape(str(row.side)))}</td>"
        f"<td>{row.execution_price:,.4f}</td>"
        f"<td>{row.shares:,.4f}</td>"
        f"<td>¥{row.total_transaction_cost:,.2f}</td>"
        "</tr>"
        for row in recent_trades.itertuples()
    )

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QQQ SMA{sma_window} 模拟交易</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 1180px; margin: auto; padding: 28px 18px 48px; background: Canvas; color: CanvasText; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }} a {{ color:#1677ff; }}
    .muted {{ opacity: .68; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin:24px 0; }}
    .card {{ border:1px solid color-mix(in srgb,CanvasText 18%,transparent); border-radius:12px; padding:16px; }}
    .label {{ font-size:13px; opacity:.68; }} .value {{ font-size:23px; margin-top:5px; font-variant-numeric:tabular-nums; }}
    .links {{ display:flex; flex-wrap:wrap; gap:10px; margin:14px 0; }}
    .links a {{ border:1px solid #1677ff88; border-radius:8px; padding:8px 10px; text-decoration:none; }}
    img {{ width:100%; height:auto; display:block; }}
    iframe {{ width:100%; height:1320px; border:0; display:block; background:#07111f; border-radius:10px; }}
    details summary {{ cursor:pointer; padding:8px 0; }}
    table {{ width:100%; border-collapse:collapse; margin-top:12px; font-variant-numeric:tabular-nums; }}
    th,td {{ text-align:right; padding:10px 8px; border-bottom:1px solid color-mix(in srgb,CanvasText 14%,transparent); }}
    th:first-child,td:first-child {{ text-align:left; }}
    @media(max-width:560px) {{ body {{ padding-top:18px; }} .value {{ font-size:19px; }} }}
  </style>
</head>
<body>
  <h1>QQQ SMA{sma_window} 模拟交易</h1>
  <div class="muted">数据截至 {latest.trade_date:%Y-%m-%d} · 图表支持缩放、区间选择、悬停明细和图片导出</div>
  <section class="grid" aria-label="模拟账户摘要">
    <div class="card"><div class="label">当前状态</div><div class="value">{signal}</div></div>
    <div class="card"><div class="label">策略资产</div><div class="value">¥{summary["final_value"]:,.2f}</div></div>
    <div class="card"><div class="label">QQQ 基准</div><div class="value">¥{summary["qqq_benchmark_final_value"]:,.2f}</div></div>
    <div class="card"><div class="label">累计投入</div><div class="value">¥{summary["total_contributions"]:,.2f}</div></div>
    <div class="card"><div class="label">今日策略涨跌</div><div class="value">{latest.daily_return:+.2%}</div></div>
    <div class="card"><div class="label">今日策略盈亏</div><div class="value">¥{latest.daily_profit:+,.2f}</div></div>
    <div class="card"><div class="label">下一交易日操作</div><div class="value">{summary["next_action_text"]}</div></div>
    <div class="card"><div class="label">QQQ收盘 / SMA{sma_window}</div><div class="value">{latest.qqq_close:,.2f} / {latest[sma_column]:,.2f}</div></div>
    <div class="card"><div class="label">累计交易成本</div><div class="value">¥{summary["transaction_costs_paid_or_estimated"]:,.2f}</div></div>
    <div class="card"><div class="label">最大回撤</div><div class="value">{summary["max_drawdown"]:.2%}</div></div>
  </section>
  <nav class="links">
    <a href="reports/trades.html">全部交易明细</a>
    <a href="reports/positions.html">每日持仓明细</a>
    <a href="reports/changes.html">每日涨跌明细</a>
    <a href="reports/daily_full.csv" download>下载完整每日账本 CSV</a>
    <a href="reports/trade_details.csv" download>下载交易 CSV</a>
  </nav>
  <section class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
      <div><strong>交互式行情与策略分析</strong><div class="label">滚轮缩放 · 拖动选择 · 双击复位 · 工具栏导出图片</div></div>
      <a href="interactive_market_chart.html" target="_blank" rel="noopener">全屏打开</a>
    </div>
    <iframe src="interactive_market_chart.html" title="QQQ、SMA200、TQQQ和策略交互式分析图"></iframe>
  </section>
  <details class="card" style="margin-top:12px">
    <summary>查看静态资产曲线（备用）</summary>
    <img src="{chart_filename}" alt="策略、QQQ基准和累计投入的每日曲线">
  </details>
  <section class="card" style="margin-top:12px">
    <div class="label">最近 10 次模拟交易</div>
    <table>
      <thead><tr><th>日期</th><th>方向</th><th>成交价</th><th>股数</th><th>成本</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </section>
</body>
</html>
"""
    output = PUBLIC_DIR / "index.html"
    output.write_text(page, encoding="utf-8")
    return output, summary


def serve(port):
    handler = partial(SimpleHTTPRequestHandler, directory=str(PUBLIC_DIR))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"本地模拟页面：http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="本地 QQQ SMA 模拟交易")
    parser.add_argument("--refresh", action="store_true", help="先更新行情")
    parser.add_argument("--serve", action="store_true", help="启动本地网页")
    parser.add_argument("--port", type=int, default=8000, help="网页端口")
    parser.add_argument("--sma-window", type=int, default=200, help="SMA 周期，默认 200")
    args = parser.parse_args()

    if args.refresh:
        refresh_market_data()
    output, summary = build_dashboard(sma_window=args.sma_window)
    print(f"页面已生成：{output.resolve()}")
    print(f"详细账本已生成：{REPORT_DIR.resolve()}")
    print(
        f"策略资产：{summary['final_value']:,.2f}；"
        f"QQQ基准：{summary['qqq_benchmark_final_value']:,.2f}"
    )
    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
