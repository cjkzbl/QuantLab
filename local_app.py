import argparse
import html
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from get_data import (
    get_qqq_data,
    get_tqqq_data,
    load_data,
    save_data,
)
from strategy import backtest_qqq_sma_tqqq, plot_daily_curve


PUBLIC_DIR = Path("public")


def refresh_market_data():
    """重新获取并保存 QQQ、TQQQ 行情。"""
    save_data(get_qqq_data(), "qqq_daily.csv")
    save_data(get_tqqq_data(), "tqqq_daily.csv")


def build_dashboard():
    """运行模拟回测并生成本地静态仪表盘。"""
    qqq = load_data("qqq_daily.csv")
    tqqq = load_data("tqqq_daily.csv")
    daily, summary = backtest_qqq_sma_tqqq(qqq, tqqq)

    PUBLIC_DIR.mkdir(exist_ok=True)
    (PUBLIC_DIR / ".nojekyll").touch()
    plot_daily_curve(daily, PUBLIC_DIR / "sma225_daily_curve.png")

    latest = daily.iloc[-1]
    sma_column = next(
        column for column in daily.columns if column.startswith("qqq_sma")
    )
    in_market = float(latest["shares"]) > 0
    signal = "持有 TQQQ" if in_market else "持有现金"
    action_names = {"initial_buy": "首次买入", "buy": "买入", "sell": "清仓"}
    recent_trades = daily[daily["action"].isin(["initial_buy", "buy", "sell"])]
    recent_trades = recent_trades.tail(10).iloc[::-1]
    rows = "".join(
        "<tr>"
        f"<td>{row.trade_date:%Y-%m-%d}</td>"
        f"<td>{action_names.get(row.action, html.escape(str(row.action)))}</td>"
        f"<td>{row.qqq_close:,.2f}</td>"
        f"<td>¥{row.total_value:,.2f}</td>"
        "</tr>"
        for row in recent_trades.itertuples()
    )

    page = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <meta http-equiv="refresh" content="300">
  <title>QQQ SMA225 模拟交易</title>
  <style>
    :root {{ color-scheme: light dark; font-family: system-ui, sans-serif; }}
    body {{ max-width: 1180px; margin: auto; padding: 28px 18px 48px; background: Canvas; color: CanvasText; }}
    h1 {{ margin: 0 0 6px; font-size: 28px; }}
    .muted {{ opacity: .68; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(180px,1fr)); gap: 12px; margin: 24px 0; }}
    .card {{ border: 1px solid color-mix(in srgb, CanvasText 18%, transparent); border-radius: 12px; padding: 16px; }}
    .label {{ font-size: 13px; opacity: .68; }}
    .value {{ font-size: 23px; margin-top: 5px; font-variant-numeric: tabular-nums; }}
    img {{ width: 100%; height: auto; display: block; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
    th,td {{ text-align: right; padding: 10px 8px; border-bottom: 1px solid color-mix(in srgb, CanvasText 14%, transparent); }}
    th:first-child,td:first-child {{ text-align: left; }}
    @media(max-width:560px) {{ body {{ padding-top: 18px; }} .value {{ font-size: 19px; }} }}
  </style>
</head>
<body>
  <h1>QQQ SMA225 模拟交易</h1>
  <div class="muted">数据截至 {latest.trade_date:%Y-%m-%d} · 页面每5分钟自动刷新</div>
  <section class="grid" aria-label="模拟账户摘要">
    <div class="card"><div class="label">当前状态</div><div class="value">{signal}</div></div>
    <div class="card"><div class="label">策略资产</div><div class="value">¥{summary["final_value"]:,.2f}</div></div>
    <div class="card"><div class="label">QQQ 基准</div><div class="value">¥{summary["qqq_benchmark_final_value"]:,.2f}</div></div>
    <div class="card"><div class="label">累计投入</div><div class="value">¥{summary["total_contributions"]:,.2f}</div></div>
    <div class="card"><div class="label">QQQ收盘 / SMA225</div><div class="value">{latest.qqq_close:,.2f} / {latest[sma_column]:,.2f}</div></div>
    <div class="card"><div class="label">累计交易成本</div><div class="value">¥{summary["transaction_costs_paid_or_estimated"]:,.2f}</div></div>
  </section>
  <section class="card">
    <img src="sma225_daily_curve.png" alt="策略、QQQ基准和累计投入的每日曲线">
  </section>
  <section class="card" style="margin-top:12px">
    <div class="label">最近10次模拟交易</div>
    <table>
      <thead><tr><th>日期</th><th>动作</th><th>QQQ收盘</th><th>账户资产</th></tr></thead>
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
    print(f"本地模拟仪表盘：http://127.0.0.1:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    parser = argparse.ArgumentParser(description="本地 QQQ SMA225 模拟交易")
    parser.add_argument("--refresh", action="store_true", help="先更新行情")
    parser.add_argument("--serve", action="store_true", help="启动本地网页")
    parser.add_argument("--port", type=int, default=8000, help="网页端口")
    args = parser.parse_args()

    if args.refresh:
        refresh_market_data()
    output, summary = build_dashboard()
    print(f"仪表盘已生成：{output}")
    print(
        f"策略资产：{summary['final_value']:,.2f}，"
        f"QQQ基准：{summary['qqq_benchmark_final_value']:,.2f}"
    )
    if args.serve:
        serve(args.port)


if __name__ == "__main__":
    main()
