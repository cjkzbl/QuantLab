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
        f"QQQ={result['qqq_rows']} 行，TQQQ={result['tqqq_rows']} 行，"
        f"BIL={result['bil_rows']} 行"
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
    download_files = {
        "trades.html": "trade_details.csv",
        "positions.html": "daily_positions.csv",
        "changes.html": "daily_changes.csv",
    }
    download_file = download_files.get(filename, "daily_full.csv")
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
    :root {{ color-scheme:dark;font-family:Inter,"Segoe UI",system-ui,sans-serif;--bg:#070b14;--panel:#0e1625;--line:#22304a;--text:#f4f7fb;--muted:#8c9ab1;--blue:#4f8cff; }}
    * {{ box-sizing:border-box; }} body {{ margin:0;min-height:100vh;background:radial-gradient(circle at 15% 0,#173765 0,transparent 32rem),var(--bg);color:var(--text); }}
    a {{ color:#9ac1ff;text-decoration:none; }} a:hover {{ color:#d4e4ff; }}
    .shell {{ width:min(1600px,calc(100% - 32px));margin:auto;padding:24px 0 40px; }}
    .topbar {{ display:flex;align-items:center;justify-content:space-between;gap:20px;margin-bottom:22px; }}
    .back {{ display:inline-flex;align-items:center;gap:8px;font-size:13px;color:var(--muted); }}
    h1 {{ margin:8px 0 3px;font-size:clamp(25px,3vw,36px);letter-spacing:-.035em; }}
    .meta {{ color:var(--muted);font-size:13px; }}
    .download {{ padding:10px 14px;border:1px solid #4f8cff66;border-radius:10px;background:#4f8cff16;font-weight:650;font-size:13px;white-space:nowrap; }}
    .table-wrap {{ overflow:auto;max-height:calc(100vh - 150px);border:1px solid var(--line);border-radius:16px;background:#0c1320e8;box-shadow:0 24px 70px #0006; }}
    table {{ border-collapse:separate;border-spacing:0;min-width:100%;white-space:nowrap;font-variant-numeric:tabular-nums; }}
    th,td {{ padding:11px 13px;border-bottom:1px solid #ffffff0d;text-align:right;font-size:12px; }}
    th {{ position:sticky;top:0;z-index:2;background:#131d2f;color:#9ba9bd;font-size:10px;text-transform:uppercase;letter-spacing:.065em; }}
    th:first-child,td:first-child {{ position:sticky;left:0;text-align:left; }} th:first-child {{ z-index:3;background:#131d2f; }} td:first-child {{ background:#0d1523; }}
    tbody tr:nth-child(even) td {{ background-color:#ffffff02; }} tbody tr:hover td {{ background-color:#4f8cff10; }}
    @media(max-width:620px) {{ .shell {{ width:calc(100% - 20px);padding-top:16px; }} .topbar {{ align-items:flex-end; }} .table-wrap {{ max-height:calc(100vh - 130px); }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar"><div><a class="back" href="../index.html">← 返回策略概览</a><h1>{html.escape(title)}</h1><div class="meta">共 {len(dataframe):,} 条记录 · 表头与日期列可在滚动时固定</div></div><a class="download" href="{download_file}" download>下载 CSV</a></header>
    <div class="table-wrap"><table><thead><tr>{headers}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
  </main>
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
            ("asset", "标的", "text"),
            ("side", "方向", "text"),
            ("reason", "原因", "text"),
            ("signal_date", "信号日", "date"),
            ("qqq_signal_close", "QQQ信号收盘", "number"),
            (sma_column, sma_label, "number"),
            ("market_price", "市场开盘价", "number"),
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
            ("bil_shares", "BIL股数", "shares"),
            ("average_cost_per_share", "平均成本", "number"),
            ("cost_basis", "持仓成本", "money"),
            ("tqqq_close", "TQQQ收盘", "number"),
            ("bil_close", "BIL收盘", "number"),
            ("tqqq_market_value", "TQQQ市值", "money"),
            ("bil_market_value", "BIL市值", "money"),
            ("position_market_value", "持仓市值", "money"),
            ("position_liquidation_value", "清算净值", "money"),
            ("unrealized_profit_before_exit_cost", "未实现盈亏", "money"),
            ("exposure_ratio", "仓位比例", "percent"),
            ("bil_exposure_ratio", "BIL仓位比例", "percent"),
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
            ("bil_close", "BIL收盘", "number"),
            ("bil_daily_change", "BIL涨跌", "percent"),
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


def _build_return_heatmap(daily):
    """按资金流调整后的每日收益率生成月度、季度和年度收益格子。"""
    returns = daily[["trade_date", "daily_return"]].copy()
    returns["trade_date"] = pd.to_datetime(returns["trade_date"])
    returns["daily_return"] = pd.to_numeric(
        returns["daily_return"], errors="coerce"
    ).fillna(0.0)
    returns["year"] = returns["trade_date"].dt.year
    returns["month"] = returns["trade_date"].dt.month
    returns["quarter"] = returns["trade_date"].dt.quarter

    def compound(group):
        return float((1.0 + group).prod() - 1.0)

    monthly = returns.groupby(["year", "month"])["daily_return"].apply(compound)
    quarterly = returns.groupby(["year", "quarter"])["daily_return"].apply(compound)
    yearly = returns.groupby("year")["daily_return"].apply(compound)
    latest_year = int(returns["year"].max())
    latest_date = returns["trade_date"].max().strftime("%Y-%m-%d")

    def cell(value, period, is_total=False):
        if value is None:
            return '<td class="return-cell empty">—</td>'
        positive = value >= 0
        rgb = "53,211,153" if positive else "251,113,133"
        intensity = min(abs(value) / 0.30, 1.0)
        alpha = 0.10 + intensity * 0.48
        css_class = "gain" if positive else "loss"
        if is_total:
            css_class += " total"
        return (
            f'<td class="return-cell {css_class}" '
            f'style="background:rgba({rgb},{alpha:.3f})" '
            f'title="{html.escape(period)}：{value:+.4%}">{value:+.1%}</td>'
        )

    rows = []
    for year in sorted(yearly.index, reverse=True):
        month_cells = "".join(
            cell(monthly.get((year, month)), f"{year}-{month:02d}")
            for month in range(1, 13)
        )
        quarter_cells = "".join(
            cell(quarterly.get((year, quarter)), f"{year} Q{quarter}", True)
            for quarter in range(1, 5)
        )
        year_label = f"{year} YTD（截至 {latest_date}）" if year == latest_year else str(year)
        rows.append(
            f'<tr><th class="year-cell">{year}</th>{month_cells}{quarter_cells}'
            f'{cell(yearly.get(year), year_label, True)}</tr>'
        )

    month_headers = "".join(f"<th>{month}月</th>" for month in range(1, 13))
    quarter_headers = "".join(f"<th>Q{quarter}</th>" for quarter in range(1, 5))
    return (
        '<div class="panel heatmap-panel"><div class="heatmap-wrap">'
        '<table class="returns-grid"><thead><tr><th>年份</th>'
        f'{month_headers}{quarter_headers}<th>全年 / YTD</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
        '<div class="heatmap-legend"><span><i class="legend-loss"></i>负收益</span>'
        '<span>颜色越深，绝对收益越高</span><span><i class="legend-gain"></i>正收益</span></div></div>'
    )


def build_dashboard(sma_window=200):
    """运行模拟回测，生成本地报告和静态网页。"""
    qqq = load_data("qqq_daily.csv")
    tqqq = load_data("tqqq_daily.csv")
    bil = load_data("bil_daily.csv")
    daily, trades, summary = backtest_qqq_sma_tqqq(
        qqq,
        tqqq,
        bil,
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
    signal = f"持有 {latest['position_status']}"
    regime_key = str(latest["market_regime"])
    regime_names = {"bull": "牛市", "bear": "熊市", "neutral": "中性"}
    regime_name = regime_names.get(regime_key, regime_key)
    strategy_return = summary["final_value"] / summary["total_contributions"] - 1
    benchmark_return = (
        summary["qqq_benchmark_final_value"] / summary["total_contributions"] - 1
    )
    action_names = {"buy": "买入", "sell": "卖出"}
    recent_trades = trades.tail(10).iloc[::-1]
    rows = "".join(
        "<tr>"
        f"<td>{row.trade_date:%Y-%m-%d}</td>"
        f"<td><strong>{html.escape(str(row.asset))}</strong></td>"
        f"<td><span class=\"trade-badge {html.escape(str(row.side))}\">"
        f"{action_names.get(row.side, html.escape(str(row.side)))}</span></td>"
        f"<td class=\"reason\">{html.escape(str(row.reason))}</td>"
        f"<td>{row.execution_price:,.4f}</td>"
        f"<td>{row.shares:,.4f}</td>"
        f"<td>¥{row.total_transaction_cost:,.2f}</td>"
        "</tr>"
        for row in recent_trades.itertuples()
    )
    return_grid = _build_return_heatmap(daily)

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QQQ 牛熊缓冲 / TQQQ-BIL 轮动策略</title>
  <style>
    :root {{
      color-scheme: dark;
      font-family: Inter, "SF Pro Display", "Segoe UI", system-ui, sans-serif;
      --bg:#070b14; --panel:#101726; --panel-2:#0c1220; --line:#22304a;
      --text:#f4f7fb; --muted:#8c9ab1; --blue:#4f8cff; --cyan:#46d6db;
      --green:#35d399; --red:#fb7185; --amber:#f7bf58;
    }}
    * {{ box-sizing:border-box; }}
    html {{ scroll-behavior:smooth; }}
    body {{ margin:0; min-height:100vh; color:var(--text); background:
      radial-gradient(circle at 15% -10%,#173765 0,transparent 34rem),
      radial-gradient(circle at 90% 0,#173f3d55 0,transparent 30rem),var(--bg); }}
    body::before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.18;
      background-image:linear-gradient(#ffffff08 1px,transparent 1px),linear-gradient(90deg,#ffffff08 1px,transparent 1px);
      background-size:44px 44px; mask-image:linear-gradient(to bottom,#000,transparent 75%); }}
    a {{ color:#8db8ff; text-decoration:none; }} a:hover {{ color:#c5dbff; }}
    .shell {{ width:min(1440px,calc(100% - 40px)); margin:auto; padding-bottom:56px; position:relative; }}
    .topbar {{ height:68px; display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #ffffff12; }}
    .brand {{ display:flex; align-items:center; gap:11px; font-weight:760; letter-spacing:.02em; }}
    .brand-mark {{ width:34px; height:34px; display:grid; place-items:center; border-radius:10px; color:#07101c;
      background:linear-gradient(135deg,var(--cyan),var(--blue)); box-shadow:0 8px 30px #4f8cff44; }}
    .top-links {{ display:flex; gap:22px; font-size:14px; color:var(--muted); }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.45fr) minmax(310px,.55fr); gap:24px; padding:46px 0 28px; }}
    .eyebrow {{ display:flex; gap:9px; align-items:center; color:#a9b7cc; font-size:13px; text-transform:uppercase; letter-spacing:.13em; }}
    .live-dot {{ width:8px;height:8px;border-radius:50%;background:var(--green);box-shadow:0 0 18px var(--green); }}
    h1 {{ margin:14px 0 12px; max-width:850px; font-size:clamp(34px,5vw,64px); line-height:1.04; letter-spacing:-.045em; }}
    .gradient-text {{ background:linear-gradient(90deg,#fff 25%,#7ec8ff 62%,#51dec7); -webkit-background-clip:text; color:transparent; }}
    .lede {{ max-width:760px; margin:0; color:var(--muted); font-size:16px; line-height:1.75; }}
    .hero-actions {{ display:flex; flex-wrap:wrap; gap:11px; margin-top:24px; }}
    .button {{ display:inline-flex; align-items:center; gap:8px; min-height:42px; padding:0 15px; border:1px solid var(--line); border-radius:11px;
      background:#111b2d; color:#eaf1ff; font-weight:650; font-size:14px; transition:.18s ease; }}
    .button:hover {{ transform:translateY(-1px); border-color:#5274a7; background:#16233a; }}
    .button.primary {{ border-color:#5b92ff; background:linear-gradient(135deg,#3275ed,#4f8cff); box-shadow:0 10px 28px #245bb94d; }}
    .status-card {{ align-self:stretch; padding:22px; border:1px solid #ffffff18; border-radius:20px; background:linear-gradient(150deg,#142038e8,#0c1322e8); box-shadow:0 24px 80px #0007; }}
    .status-head {{ display:flex; align-items:center; justify-content:space-between; color:var(--muted); font-size:13px; }}
    .regime {{ display:inline-flex; align-items:center; gap:7px; padding:6px 10px; border-radius:999px; font-weight:750; }}
    .regime::before {{ content:""; width:7px;height:7px;border-radius:50%;background:currentColor; }}
    .regime.bull {{ color:#56e5ac;background:#35d39917;border:1px solid #35d39945; }}
    .regime.bear {{ color:#ff8da0;background:#fb718517;border:1px solid #fb718545; }}
    .regime.neutral {{ color:#ffd175;background:#f7bf5817;border:1px solid #f7bf5845; }}
    .action-title {{ margin:26px 0 4px; color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.12em; }}
    .action-value {{ font-size:28px; font-weight:780; letter-spacing:-.025em; }}
    .action-reason {{ margin-top:9px; color:#a8b5c9; line-height:1.55; font-size:13px; }}
    .threshold {{ display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:22px;padding-top:18px;border-top:1px solid #ffffff12; }}
    .threshold div {{ min-width:0; }} .threshold span {{ display:block;color:var(--muted);font-size:11px; }}
    .threshold strong {{ display:block;margin-top:4px;font-size:14px;font-variant-numeric:tabular-nums;white-space:nowrap; }}
    .section-head {{ display:flex; align-items:end; justify-content:space-between; gap:20px; margin:26px 0 14px; }}
    .section-head h2 {{ margin:0; font-size:20px; letter-spacing:-.015em; }}
    .section-head p {{ margin:5px 0 0; color:var(--muted); font-size:13px; }}
    .kpi-grid {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
    .kpi {{ min-height:132px; padding:18px; border:1px solid var(--line); border-radius:16px; background:linear-gradient(150deg,#111a2b,#0b111d); position:relative; overflow:hidden; }}
    .kpi::after {{ content:"";position:absolute;width:90px;height:90px;border-radius:50%;right:-45px;top:-45px;background:var(--glow,#4f8cff);filter:blur(35px);opacity:.16; }}
    .label {{ color:var(--muted);font-size:12px;letter-spacing:.035em; }}
    .value {{ margin-top:14px;font-size:clamp(21px,2.2vw,30px);font-weight:760;letter-spacing:-.025em;font-variant-numeric:tabular-nums; }}
    .subvalue {{ margin-top:8px;color:var(--muted);font-size:12px; }} .positive {{ color:var(--green); }} .negative {{ color:var(--red); }}
    .panel {{ border:1px solid var(--line); border-radius:18px; background:linear-gradient(150deg,#0f1727ed,#0a101bed); box-shadow:0 20px 60px #0004; overflow:hidden; }}
    .panel-head {{ display:flex;align-items:center;justify-content:space-between;gap:20px;padding:18px 20px;border-bottom:1px solid var(--line); }}
    .panel-title {{ font-weight:730; }} .panel-note {{ margin-top:4px;color:var(--muted);font-size:12px; }}
    iframe {{ width:100%;height:880px;border:0;display:block;background:#07111f; }}
    .split {{ display:grid;grid-template-columns:minmax(0,1.25fr) minmax(300px,.75fr);gap:14px;margin-top:14px; }}
    .rules {{ padding:20px; }} .rule {{ display:grid;grid-template-columns:36px 1fr;gap:12px;padding:13px 0;border-bottom:1px solid #ffffff0d; }}
    .rule:last-child {{ border:0; }} .rule-num {{ width:30px;height:30px;display:grid;place-items:center;border-radius:9px;background:#4f8cff18;color:#8db8ff;font-weight:750;font-size:12px; }}
    .rule strong {{ display:block;font-size:14px; }} .rule span {{ display:block;margin-top:4px;color:var(--muted);font-size:12px;line-height:1.5; }}
    .table-panel {{ padding:0 20px 14px; overflow:auto; }}
    .heatmap-panel {{ padding:14px; }} .heatmap-wrap {{ overflow-x:auto; padding-bottom:5px; }}
    .returns-grid {{ min-width:1180px; table-layout:fixed; border-collapse:separate; border-spacing:6px; }}
    .returns-grid th,.returns-grid td {{ border:0; border-radius:9px; padding:11px 6px; text-align:center; }}
    .returns-grid thead th {{ color:#8796ad; background:#0b1220; font-size:10px; }}
    .returns-grid .year-cell {{ position:sticky; left:0; z-index:2; color:#dce7f8; background:#111b2c; font-size:12px; }}
    .return-cell {{ font-size:12px; font-weight:720; color:#dce7f8; }}
    .return-cell.gain {{ color:#87f3c7; }} .return-cell.loss {{ color:#ffadba; }}
    .return-cell.total {{ outline:1px solid #ffffff16; font-weight:800; }}
    .return-cell.empty {{ color:#48566c; background:#0b111d; }}
    .heatmap-legend {{ display:flex; align-items:center; justify-content:flex-end; gap:18px; padding:8px 7px 1px; color:var(--muted); font-size:11px; }}
    .heatmap-legend span {{ display:flex; align-items:center; gap:6px; }}
    .heatmap-legend i {{ width:10px; height:10px; border-radius:3px; }} .legend-loss {{ background:var(--red); }} .legend-gain {{ background:var(--green); }}
    table {{ width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums;white-space:nowrap; }}
    th,td {{ padding:13px 10px;border-bottom:1px solid #ffffff0d;text-align:right;font-size:13px; }}
    th {{ color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.06em;font-weight:650; }}
    th:first-child,td:first-child {{ text-align:left; }} td.reason {{ max-width:330px;overflow:hidden;text-overflow:ellipsis;text-align:left;color:#aab6c9; }}
    tbody tr:hover {{ background:#ffffff05; }} .trade-badge {{ display:inline-flex;padding:4px 8px;border-radius:999px;font-weight:700;font-size:11px; }}
    .trade-badge.buy {{ color:#61e7b2;background:#35d39918; }} .trade-badge.sell {{ color:#ff8da0;background:#fb718518; }}
    .footer {{ display:flex;justify-content:space-between;gap:20px;margin-top:28px;padding:22px 0;border-top:1px solid #ffffff10;color:var(--muted);font-size:12px; }}
    details summary {{ cursor:pointer;padding:17px 20px;font-weight:650; }} details img {{ width:100%;height:auto;display:block; }}
    @media(max-width:980px) {{ .hero,.split {{ grid-template-columns:1fr; }} .kpi-grid {{ grid-template-columns:repeat(2,minmax(0,1fr)); }} }}
    @media(max-width:620px) {{ .shell {{ width:min(100% - 24px,1440px); }} .top-links {{ display:none; }} .hero {{ padding-top:30px; }} .kpi-grid {{ grid-template-columns:1fr; }}
      .status-card {{ padding:18px; }} .threshold {{ gap:4px; }} .threshold strong {{ font-size:12px; }} .panel-head,.section-head {{ align-items:flex-start; }} .panel-head {{ padding:15px; }} .footer {{ flex-direction:column; }} }}
  </style>
</head>
<body>
  <main class="shell">
    <header class="topbar">
      <a class="brand" href="#top"><span class="brand-mark">Q</span><span>QuantLab</span></a>
      <nav class="top-links" aria-label="主导航">
        <a href="#overview">账户概览</a><a href="#chart">策略图表</a><a href="#returns">收益格子</a><a href="#rules">交易规则</a><a href="#trades">交易记录</a>
      </nav>
    </header>

    <section class="hero" id="top">
      <div>
        <div class="eyebrow"><span class="live-dot"></span> Strategy dashboard · 数据截至 {latest.trade_date:%Y-%m-%d}</div>
        <h1><span class="gradient-text">识别趋势，等待回调，纪律执行。</span></h1>
        <p class="lede">以 QQQ 的 SMA{summary["sma_window"]} 牛熊缓冲区判断市场环境：进攻阶段持有 TQQQ，熊市或等待买点时由短期美国国库券 ETF BIL 承接资金。所有信号均在下一交易日开盘执行。</p>
        <div class="hero-actions">
          <a class="button primary" href="#chart">查看策略图表 →</a>
          <a class="button" href="reports/trades.html">交易明细</a>
          <a class="button" href="reports/daily_full.csv" download>下载完整账本</a>
        </div>
      </div>
      <aside class="status-card" aria-label="当前交易信号">
        <div class="status-head"><span>当前市场环境</span><span class="regime {regime_key}">{regime_name}</span></div>
        <div class="action-title">下一交易日操作</div>
        <div class="action-value">{html.escape(summary["next_action_text"])}</div>
        <div class="action-reason">{html.escape(summary["next_action_reason"])}</div>
        <div class="threshold">
          <div><span>QQQ 收盘</span><strong>{latest.qqq_close:,.2f}</strong></div>
          <div><span>牛市线</span><strong>{latest.bull_threshold:,.2f}</strong></div>
          <div><span>熊市线</span><strong>{latest.bear_threshold:,.2f}</strong></div>
        </div>
      </aside>
    </section>

    <section id="overview">
      <div class="section-head"><div><h2>账户概览</h2><p>同资金流 QQQ 定投作为基准，金额已计入交易成本和预计退出成本。</p></div><span class="regime {regime_key}">{signal}</span></div>
      <div class="kpi-grid">
        <article class="kpi" style="--glow:#4f8cff"><div class="label">策略净值</div><div class="value">¥{summary["final_value"]:,.0f}</div><div class="subvalue positive">累计收益 {strategy_return:+.2%}</div></article>
        <article class="kpi" style="--glow:#46d6db"><div class="label">QQQ 定投基准</div><div class="value">¥{summary["qqq_benchmark_final_value"]:,.0f}</div><div class="subvalue">累计收益 {benchmark_return:+.2%}</div></article>
        <article class="kpi" style="--glow:#35d399"><div class="label">相对 QQQ</div><div class="value positive">{summary["strategy_vs_qqq"]:+.2%}</div><div class="subvalue">策略净值 / 基准净值 − 1</div></article>
        <article class="kpi" style="--glow:#f7bf58"><div class="label">累计投入</div><div class="value">¥{summary["total_contributions"]:,.0f}</div><div class="subvalue">起始 ¥{summary["initial_capital"]:,.0f} · 每月 ¥{summary["monthly_contribution"]:,.0f}</div></article>
        <article class="kpi"><div class="label">今日策略涨跌</div><div class="value {'positive' if latest.daily_return >= 0 else 'negative'}">{latest.daily_return:+.2%}</div><div class="subvalue">盈亏 ¥{latest.daily_profit:+,.0f}</div></article>
        <article class="kpi"><div class="label">最大回撤</div><div class="value negative">{summary["max_drawdown"]:.2%}</div><div class="subvalue">历史峰值至谷底</div></article>
        <article class="kpi"><div class="label">TQQQ切换次数</div><div class="value">{summary["buy_count"] + summary["sell_count"]:,}</div><div class="subvalue">转入 {summary["buy_count"]} · 转出 {summary["sell_count"]}</div></article>
        <article class="kpi"><div class="label">交易成本</div><div class="value">¥{summary["transaction_costs_paid_or_estimated"]:,.0f}</div><div class="subvalue">零佣金 · QQQ/BIL 1bp · TQQQ 5bp</div></article>
      </div>
    </section>

    <section id="chart">
      <div class="section-head"><div><h2>账户收益曲线</h2><p>策略账户为主线，QQQ定投和累计投入作为对照；下方时间导航条可直接拖动缩放。</p></div><a class="button" href="interactive_market_chart.html" target="_blank" rel="noopener">全屏图表 ↗</a></div>
      <div class="panel"><iframe src="interactive_market_chart.html" loading="lazy" title="策略账户收益、QQQ基准、回撤与仓位图"></iframe></div>
    </section>

    <section id="returns">
      <div class="section-head"><div><h2>年度 · 季度 · 月度收益</h2><p>按每日策略收益复合计算，已剔除每月新增资金对收益率的影响；当年数据为截至最新交易日的 YTD。</p></div></div>
      {return_grid}
    </section>

    <section class="split" id="rules">
      <div class="panel rules">
        <div class="section-head" style="margin:0 0 6px"><div><h2>交易规则</h2><p>状态机带缓冲区，减少均线附近的反复切换。</p></div></div>
        <div class="rule"><div class="rule-num">01</div><div><strong>牛市确认</strong><span>QQQ 收盘高于 SMA{summary["sma_window"]} × {summary["bull_multiplier"]:.2f}，切换为牛市。</span></div></div>
        <div class="rule"><div class="rule-num">02</div><div><strong>回调转入 TQQQ</strong><span>牛市中 QQQ 单日下跌达到 {summary["dip_threshold"]:.0%}，下一交易日卖出 BIL，并将全部资金买入 TQQQ。</span></div></div>
        <div class="rule"><div class="rule-num">03</div><div><strong>熊市转入 BIL</strong><span>QQQ 收盘低于 SMA{summary["sma_window"]} × {summary["bear_multiplier"]:.2f}，下一交易日卖出 TQQQ 并买入 BIL。</span></div></div>
        <div class="rule"><div class="rule-num">04</div><div><strong>熊转牛</strong><span>熊市重新突破牛市线时不等待回调，立即卖出 BIL 并买入 TQQQ。</span></div></div>
      </div>
      <details class="panel">
        <summary>静态资产曲线（兼容模式）</summary>
        <img src="{chart_filename}" alt="策略、QQQ基准和累计投入的每日曲线">
      </details>
    </section>

    <section id="trades">
      <div class="section-head"><div><h2>最近交易</h2><p>展示最近 10 次模拟成交；完整记录和每日账本可单独查看。</p></div><div class="hero-actions" style="margin:0"><a class="button" href="reports/positions.html">持仓明细</a><a class="button" href="reports/changes.html">每日涨跌</a></div></div>
      <div class="panel table-panel"><table>
        <thead><tr><th>日期</th><th>标的</th><th>方向</th><th style="text-align:left">触发原因</th><th>成交价</th><th>股数</th><th>交易成本</th></tr></thead>
        <tbody>{rows}</tbody>
      </table></div>
    </section>

    <footer class="footer"><span>QuantLab · 静态模拟研究面板</span><span>历史回测不代表未来收益，杠杆 ETF 具有显著波动与路径风险。</span></footer>
  </main>
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
    parser = argparse.ArgumentParser(description="本地 QQQ 牛熊缓冲 / TQQQ-BIL 轮动策略")
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
