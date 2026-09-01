import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


POSITIVE_COLOR = "#22c55e"
NEGATIVE_COLOR = "#ef4444"
QQQ_COLOR = "#38bdf8"
TQQQ_COLOR = "#f59e0b"
SMA_COLOR = "#e879f9"
STRATEGY_COLOR = "#a78bfa"


def _prepare_ohlcv(frame, prefix):
    required = {"trade_date", "open", "high", "low", "close", "volume"}
    if not required.issubset(frame.columns):
        missing = ", ".join(sorted(required - set(frame.columns)))
        raise ValueError(f"{prefix.upper()} 行情缺少列：{missing}")

    result = frame[list(required)].copy()
    result["trade_date"] = pd.to_datetime(result["trade_date"])
    result = result.sort_values("trade_date").drop_duplicates("trade_date")
    return result.rename(
        columns={
            column: f"{prefix}_{column}"
            for column in ["open", "high", "low", "close", "volume"]
        }
    )


def _market_data(daily, qqq, tqqq):
    qqq_ohlcv = _prepare_ohlcv(qqq, "qqq")
    tqqq_ohlcv = _prepare_ohlcv(tqqq, "tqqq")
    details = daily.copy()
    details["trade_date"] = pd.to_datetime(details["trade_date"])
    details = details.drop(
        columns=[
            column
            for column in ["qqq_open", "qqq_close", "tqqq_open", "tqqq_close"]
            if column in details.columns
        ]
    )
    merged = details.merge(qqq_ohlcv, on="trade_date", how="inner")
    merged = merged.merge(tqqq_ohlcv, on="trade_date", how="inner")
    if merged.empty:
        raise ValueError("没有可用于绘图的 QQQ/TQQQ 共同交易日")
    return merged.sort_values("trade_date").reset_index(drop=True)


def _marker_data(data, actions):
    return data[data["action"].isin(actions)]


def _build_legacy_market_chart(
    daily,
    trades,
    qqq,
    tqqq,
    filename="public/interactive_market_chart.html",
):
    """生成 QQQ/TQQQ K线、均线、信号、资产和涨跌联动图。"""
    data = _market_data(daily, qqq, tqqq)
    sma_column = next(column for column in data.columns if column.startswith("qqq_sma"))
    sma_window = sma_column.removeprefix("qqq_sma")
    dates = data["trade_date"]

    figure = make_subplots(
        rows=5,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.025,
        row_heights=[0.31, 0.25, 0.20, 0.13, 0.11],
        specs=[
            [{"secondary_y": False}],
            [{"secondary_y": False}],
            [{"secondary_y": False}],
            [{"secondary_y": True}],
            [{"secondary_y": True}],
        ],
        subplot_titles=(
            f"QQQ 后复权 K线与 SMA{sma_window}",
            "TQQQ 后复权 K线与实际交易点",
            "策略资产、QQQ基准与累计投入",
            "每日收益率与策略仓位",
            "QQQ / TQQQ 成交量",
        ),
    )

    figure.add_trace(
        go.Candlestick(
            x=dates,
            open=data["qqq_open"],
            high=data["qqq_high"],
            low=data["qqq_low"],
            close=data["qqq_close"],
            name="QQQ K线",
            increasing_line_color=POSITIVE_COLOR,
            decreasing_line_color=NEGATIVE_COLOR,
            hovertext=(
                "QQQ<br>开盘：" + data["qqq_open"].map("{:,.2f}".format)
                + "<br>最高：" + data["qqq_high"].map("{:,.2f}".format)
                + "<br>最低：" + data["qqq_low"].map("{:,.2f}".format)
                + "<br>收盘：" + data["qqq_close"].map("{:,.2f}".format)
            ),
            hoverinfo="text+x",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data[sma_column],
            name=f"SMA{sma_window}",
            mode="lines",
            line={"color": SMA_COLOR, "width": 2},
            hovertemplate=f"SMA{sma_window}：%{{y:,.2f}}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    for column, name, color, dash in [
        ("bull_threshold", "牛市线", "#22c55e", "dash"),
        ("bear_threshold", "熊市线", "#ef4444", "dash"),
    ]:
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=data[column],
                name=name,
                mode="lines",
                line={"color": color, "width": 1.3, "dash": dash},
                hovertemplate=f"{name}：%{{y:,.2f}}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    buys = _marker_data(data, ["initial_buy", "buy"])
    sells = _marker_data(data, ["sell"])
    for points, name, symbol, color in [
        (buys, "策略买入信号", "triangle-up", POSITIVE_COLOR),
        (sells, "策略卖出信号", "triangle-down", NEGATIVE_COLOR),
    ]:
        figure.add_trace(
            go.Scatter(
                x=points["trade_date"],
                y=points["qqq_close"],
                name=name,
                mode="markers",
                marker={"symbol": symbol, "size": 11, "color": color},
                customdata=points[["action_reason", "total_value"]],
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>QQQ：%{y:,.2f}"
                    "<br>%{customdata[0]}<br>资产：%{customdata[1]:,.2f}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    figure.add_trace(
        go.Candlestick(
            x=dates,
            open=data["tqqq_open"],
            high=data["tqqq_high"],
            low=data["tqqq_low"],
            close=data["tqqq_close"],
            name="TQQQ K线",
            increasing_line_color="#34d399",
            decreasing_line_color="#fb7185",
            hovertext=(
                "TQQQ<br>开盘：" + data["tqqq_open"].map("{:,.2f}".format)
                + "<br>最高：" + data["tqqq_high"].map("{:,.2f}".format)
                + "<br>最低：" + data["tqqq_low"].map("{:,.2f}".format)
                + "<br>收盘：" + data["tqqq_close"].map("{:,.2f}".format)
            ),
            hoverinfo="text+x",
        ),
        row=2,
        col=1,
    )

    if not trades.empty:
        for side, name, symbol, color in [
            ("buy", "TQQQ 买入成交", "triangle-up", POSITIVE_COLOR),
            ("sell", "TQQQ 卖出成交", "triangle-down", NEGATIVE_COLOR),
        ]:
            points = trades[trades["side"] == side]
            figure.add_trace(
                go.Scatter(
                    x=pd.to_datetime(points["trade_date"]),
                    y=points["execution_price"],
                    name=name,
                    mode="markers",
                    marker={"symbol": symbol, "size": 12, "color": color},
                    customdata=points[["shares", "total_transaction_cost", "reason"]],
                    hovertemplate=(
                        "%{x|%Y-%m-%d}<br>成交价：%{y:,.4f}"
                        "<br>股数：%{customdata[0]:,.4f}"
                        "<br>交易成本：%{customdata[1]:,.2f}"
                        "<br>%{customdata[2]}<extra></extra>"
                    ),
                ),
                row=2,
                col=1,
            )

    for column, name, color, width in [
        ("total_value", "策略资产", STRATEGY_COLOR, 2.2),
        ("qqq_benchmark_value", "QQQ基准资产", QQQ_COLOR, 1.8),
        ("total_contributions", "累计投入", "#94a3b8", 1.4),
    ]:
        figure.add_trace(
            go.Scatter(
                x=dates,
                y=data[column],
                name=name,
                mode="lines",
                line={"color": color, "width": width},
                hovertemplate=f"{name}：%{{y:,.2f}}<extra></extra>",
            ),
            row=3,
            col=1,
        )

    strategy_colors = [
        POSITIVE_COLOR if value >= 0 else NEGATIVE_COLOR
        for value in data["daily_return"]
    ]
    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["daily_return"] * 100,
            name="策略每日收益率",
            marker_color=strategy_colors,
            opacity=0.72,
            hovertemplate="策略：%{y:+.2f}%<extra></extra>",
        ),
        row=4,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["qqq_daily_change"] * 100,
            name="QQQ每日涨跌",
            mode="lines",
            line={"color": QQQ_COLOR, "width": 1},
            hovertemplate="QQQ：%{y:+.2f}%<extra></extra>",
        ),
        row=4,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["tqqq_daily_change"] * 100,
            name="TQQQ每日涨跌",
            mode="lines",
            line={"color": TQQQ_COLOR, "width": 1},
            hovertemplate="TQQQ：%{y:+.2f}%<extra></extra>",
        ),
        row=4,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["exposure_ratio"] * 100,
            name="TQQQ仓位比例",
            mode="lines",
            fill="tozeroy",
            line={"color": "rgba(167,139,250,0.45)", "width": 1},
            fillcolor="rgba(167,139,250,0.10)",
            hovertemplate="仓位：%{y:.1f}%<extra></extra>",
        ),
        row=4,
        col=1,
        secondary_y=True,
    )

    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["qqq_volume"],
            name="QQQ成交量",
            marker_color=QQQ_COLOR,
            opacity=0.48,
            hovertemplate="QQQ量：%{y:,.0f}<extra></extra>",
        ),
        row=5,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Bar(
            x=dates,
            y=data["tqqq_volume"],
            name="TQQQ成交量",
            marker_color=TQQQ_COLOR,
            opacity=0.42,
            hovertemplate="TQQQ量：%{y:,.0f}<extra></extra>",
        ),
        row=5,
        col=1,
        secondary_y=True,
    )

    latest = data.iloc[-1]
    default_start = latest["trade_date"] - pd.DateOffset(years=3)
    figure.update_layout(
        template="plotly_dark",
        height=1100,
        margin={"l": 58, "r": 58, "t": 118, "b": 42},
        hovermode="x unified",
        hoverdistance=60,
        spikedistance=-1,
        dragmode="zoom",
        barmode="overlay",
        uirevision="quantlab-market-chart",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.055,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 10},
            "bgcolor": "rgba(7,17,31,0)",
            "groupclick": "toggleitem",
        },
        paper_bgcolor="#07111f",
        plot_bgcolor="#0b1728",
        font={"family": "Inter, system-ui, sans-serif", "color": "#dbeafe"},
        hoverlabel={"bgcolor": "#111827", "font_size": 12},
    )
    figure.update_xaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.12)",
        rangeslider_visible=False,
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        range=[default_start, latest["trade_date"]],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(148,196,255,0.72)",
        spikethickness=1,
    )
    figure.update_yaxes(
        showspikes=True,
        spikesnap="cursor",
        spikecolor="rgba(148,196,255,0.45)",
        spikethickness=1,
    )
    figure.update_yaxes(title_text="QQQ", row=1, col=1)
    figure.update_yaxes(title_text="TQQQ", row=2, col=1)
    figure.update_yaxes(title_text="资产", tickformat=",.3s", row=3, col=1)
    figure.update_yaxes(title_text="涨跌 %", row=4, col=1, secondary_y=False)
    figure.update_yaxes(
        title_text="仓位 %",
        range=[0, 110],
        row=4,
        col=1,
        secondary_y=True,
    )
    figure.update_yaxes(title_text="QQQ量", tickformat=".2s", row=5, col=1)
    figure.update_yaxes(
        title_text="TQQQ量",
        tickformat=".2s",
        row=5,
        col=1,
        secondary_y=True,
    )
    figure.update_annotations(font={"size": 13, "color": "#bfdbfe"})

    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    chart_html = figure.to_html(
        include_plotlyjs=True,
        full_html=False,
        div_id="market-chart",
        config={
            "responsive": True,
            "displaylogo": False,
            "scrollZoom": False,
            "doubleClick": "reset",
            "showTips": True,
            "modeBarButtonsToRemove": ["lasso2d", "select2d"],
            "modeBarButtonsToAdd": ["drawline", "drawopenpath", "eraseshape"],
            "toImageButtonOptions": {
                "format": "png",
                "filename": f"qqq_sma{sma_window}_tqqq_analysis",
                "height": 1320,
                "width": 1800,
                "scale": 1.5,
            },
        },
    )
    page = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QQQ / TQQQ 交互式策略图表</title>
  <style>
    :root { color-scheme:dark;font-family:Inter,"Segoe UI",system-ui,sans-serif;--bg:#07111f;--panel:#0d1728;--line:#22304a;--text:#eaf2ff;--muted:#8fa0b8;--blue:#4f8cff; }
    * { box-sizing:border-box; } html,body { margin:0;background:var(--bg);color:var(--text); }
    .toolbar { position:sticky;top:0;z-index:20;display:flex;align-items:center;justify-content:space-between;gap:16px;min-height:66px;padding:10px 14px;border-bottom:1px solid var(--line);background:#091321f2;backdrop-filter:blur(14px); }
    .heading { min-width:210px; } .title { font-size:14px;font-weight:750;letter-spacing:-.01em; } .hint { margin-top:3px;color:var(--muted);font-size:10px; }
    .controls { display:flex;align-items:center;justify-content:flex-end;gap:7px;flex-wrap:wrap; }
    .group { display:flex;align-items:center;gap:3px;padding:3px;border:1px solid var(--line);border-radius:10px;background:#0a1322; }
    button { min-height:30px;padding:0 10px;border:0;border-radius:7px;background:transparent;color:#aebbd0;font:600 11px/1 inherit;cursor:pointer;white-space:nowrap; }
    button:hover { color:#fff;background:#ffffff0b; } button.active { color:#fff;background:#315fba;box-shadow:0 4px 14px #1d4b9c55; }
    button.action { border:1px solid var(--line);background:#111d30; } button.action:hover { border-color:#5274a7;background:#17263e; }
    .chart-stage:fullscreen { overflow:auto;background:var(--bg); } .chart-stage:fullscreen .toolbar { position:sticky; }
    #market-chart { width:100%; }
    @media(max-width:820px) { .toolbar { align-items:flex-start;overflow-x:auto; } .heading { position:sticky;left:0;z-index:2;background:#091321;padding-right:10px; } .controls { min-width:max-content;flex-wrap:nowrap; } }
  </style>
</head>
<body>
  <main class="chart-stage" id="chart-stage">
    <header class="toolbar">
      <div class="heading"><div class="title">QQQ 牛熊缓冲 / TQQQ 回调策略</div><div class="hint">数据截至 __LATEST_DATE__ · 拖动框选缩放 · 双击复位</div></div>
      <div class="controls">
        <div class="group" aria-label="时间区间">
          <button data-range="3">3月</button><button data-range="6">6月</button><button data-range="12">1年</button><button class="active" data-range="36">3年</button><button data-range="all">全部</button>
        </div>
        <div class="group" aria-label="鼠标模式">
          <button class="active" data-mode="zoom">框选缩放</button><button data-mode="pan">拖动平移</button>
        </div>
        <button class="action" id="reset-view">复位</button>
        <button class="action" id="fullscreen">全屏</button>
        <button class="action" id="export-image">导出 PNG</button>
      </div>
    </header>
    __CHART__
  </main>
  <script>
    (() => {
      const chart = document.getElementById('market-chart');
      const stage = document.getElementById('chart-stage');
      const end = new Date('__LATEST_DATE__T00:00:00');
      const xAxes = ['xaxis','xaxis2','xaxis3','xaxis4','xaxis5'];
      function rangeUpdate(months) {
        const update = {};
        if (months === 'all') {
          xAxes.forEach(axis => update[axis + '.autorange'] = true);
        } else {
          const start = new Date(end);
          start.setMonth(start.getMonth() - Number(months));
          xAxes.forEach(axis => update[axis + '.range'] = [start.toISOString(), end.toISOString()]);
        }
        Plotly.relayout(chart, update);
        document.querySelectorAll('[data-range]').forEach(button => button.classList.toggle('active', button.dataset.range === String(months)));
      }
      document.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click', () => rangeUpdate(button.dataset.range)));
      document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click', () => {
        Plotly.relayout(chart, {dragmode: button.dataset.mode});
        document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active', item === button));
      }));
      document.getElementById('reset-view').addEventListener('click', () => rangeUpdate('36'));
      document.getElementById('fullscreen').addEventListener('click', async () => {
        if (!document.fullscreenElement) await stage.requestFullscreen(); else await document.exitFullscreen();
        setTimeout(() => Plotly.Plots.resize(chart), 120);
      });
      document.getElementById('export-image').addEventListener('click', () => Plotly.downloadImage(chart, {
        format:'png',filename:'qqq_tqqq_strategy_chart',width:1800,height:1200,scale:1.5
      }));
      document.addEventListener('fullscreenchange', () => {
        document.getElementById('fullscreen').textContent = document.fullscreenElement ? '退出全屏' : '全屏';
        setTimeout(() => Plotly.Plots.resize(chart), 120);
      });
    })();
  </script>
</body>
</html>
"""
    page = page.replace("__LATEST_DATE__", f"{latest['trade_date']:%Y-%m-%d}")
    page = page.replace("__CHART__", chart_html)
    path.write_text(page, encoding="utf-8")
    return path


def build_interactive_market_chart(
    daily,
    trades,
    qqq,
    tqqq,
    filename="public/interactive_market_chart.html",
):
    """生成以账户收益曲线为核心、带时间导航器的交互式图表。"""
    required = {
        "trade_date",
        "total_value",
        "qqq_benchmark_value",
        "total_contributions",
        "drawdown",
        "exposure_ratio",
        "bil_exposure_ratio",
        "action",
        "action_reason",
        "daily_profit",
        "position_status",
    }
    if not required.issubset(daily.columns):
        missing = ", ".join(sorted(required - set(daily.columns)))
        raise ValueError(f"daily 缺少账户收益图所需字段：{missing}")
    if daily.empty:
        raise ValueError("daily 不能为空")

    data = daily.copy()
    data["trade_date"] = pd.to_datetime(data["trade_date"])
    data = data.sort_values("trade_date").reset_index(drop=True)
    data["qqq_drawdown"] = (
        data["qqq_benchmark_value"] / data["qqq_benchmark_value"].cummax() - 1
    )
    dates = data["trade_date"]

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.075,
        row_heights=[0.74, 0.26],
        specs=[[{}], [{"secondary_y": True}]],
        subplot_titles=("账户价值", "回撤与 TQQQ / BIL 仓位"),
    )

    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["total_contributions"],
            name="累计投入",
            mode="lines",
            line={"color": "#64748b", "width": 1.6, "dash": "dot"},
            hovertemplate="累计投入：¥%{y:,.0f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["qqq_benchmark_value"],
            name="QQQ 定投基准",
            mode="lines",
            line={"color": QQQ_COLOR, "width": 2},
            hovertemplate="QQQ基准：¥%{y:,.0f}<extra></extra>",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["total_value"],
            name="策略账户",
            mode="lines",
            line={"color": "#8b7cff", "width": 3.2},
            fill="tonexty",
            fillcolor="rgba(139,124,255,0.08)",
            customdata=data[["daily_profit", "exposure_ratio", "position_status"]],
            hovertemplate=(
                "策略账户：¥%{y:,.0f}<br>当日盈亏：¥%{customdata[0]:+,.0f}"
                "<br>TQQQ仓位：%{customdata[1]:.1%}<br>当前持仓：%{customdata[2]}<extra></extra>"
            ),
        ),
        row=1,
        col=1,
    )

    buys = data[data["action"] == "buy"]
    sells = data[data["action"] == "sell"]
    for points, name, symbol, color in [
        (buys, "买入", "triangle-up", POSITIVE_COLOR),
        (sells, "清仓", "triangle-down", NEGATIVE_COLOR),
    ]:
        figure.add_trace(
            go.Scatter(
                x=points["trade_date"],
                y=points["total_value"],
                name=name,
                mode="markers",
                marker={"symbol": symbol, "size": 8, "color": color, "opacity": 0.9},
                customdata=points[["action_reason"]],
                hovertemplate=(
                    f"{name} · %{{x|%Y-%m-%d}}<br>账户：¥%{{y:,.0f}}"
                    "<br>%{customdata[0]}<extra></extra>"
                ),
            ),
            row=1,
            col=1,
        )

    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["drawdown"] * 100,
            name="策略回撤",
            mode="lines",
            fill="tozeroy",
            line={"color": NEGATIVE_COLOR, "width": 1.5},
            fillcolor="rgba(251,113,133,0.16)",
            hovertemplate="策略回撤：%{y:.2f}%<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["qqq_drawdown"] * 100,
            name="QQQ回撤",
            mode="lines",
            line={"color": "#38bdf8", "width": 1.2, "dash": "dot"},
            hovertemplate="QQQ回撤：%{y:.2f}%<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["exposure_ratio"] * 100,
            name="TQQQ仓位",
            mode="lines",
            line={"color": "rgba(53,211,153,0.72)", "width": 1.2},
            hovertemplate="TQQQ仓位：%{y:.1f}%<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=True,
    )
    figure.add_trace(
        go.Scatter(
            x=dates,
            y=data["bil_exposure_ratio"] * 100,
            name="BIL仓位",
            mode="lines",
            line={"color": "rgba(247,191,88,0.78)", "width": 1.2},
            hovertemplate="BIL仓位：%{y:.1f}%<extra></extra>",
        ),
        row=2,
        col=1,
        secondary_y=True,
    )

    latest = data.iloc[-1]
    default_start = latest["trade_date"] - pd.DateOffset(years=3)
    figure.update_layout(
        template="plotly_dark",
        height=740,
        margin={"l": 66, "r": 64, "t": 76, "b": 30},
        hovermode="x unified",
        hoverdistance=80,
        spikedistance=-1,
        dragmode="pan",
        uirevision="quantlab-equity-chart",
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.045,
            "xanchor": "left",
            "x": 0,
            "font": {"size": 10},
            "bgcolor": "rgba(7,17,31,0)",
        },
        paper_bgcolor="#07111f",
        plot_bgcolor="#0b1728",
        font={"family": "Inter, system-ui, sans-serif", "color": "#dbeafe"},
        hoverlabel={"bgcolor": "#111827", "font_size": 12},
    )
    figure.update_xaxes(
        showgrid=True,
        gridcolor="rgba(148,163,184,0.11)",
        rangebreaks=[{"bounds": ["sat", "mon"]}],
        range=[default_start, latest["trade_date"]],
        showspikes=True,
        spikemode="across",
        spikesnap="cursor",
        spikecolor="rgba(148,196,255,0.72)",
        spikethickness=1,
    )
    figure.update_xaxes(
        row=2,
        col=1,
        rangeslider={
            "visible": True,
            "thickness": 0.12,
            "bgcolor": "#0b1728",
            "bordercolor": "#263754",
            "borderwidth": 1,
        },
    )
    figure.update_yaxes(title_text="账户价值", tickprefix="¥", tickformat=".3s", row=1, col=1)
    figure.update_yaxes(title_text="回撤 %", row=2, col=1, secondary_y=False)
    figure.update_yaxes(
        title_text="TQQQ/BIL仓位 %", range=[0, 110], row=2, col=1, secondary_y=True
    )
    figure.update_annotations(font={"size": 12, "color": "#bfdbfe"})

    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    chart_html = figure.to_html(
        include_plotlyjs=True,
        full_html=False,
        div_id="equity-chart",
        config={
            "responsive": True,
            "displaylogo": False,
            "scrollZoom": False,
            "doubleClick": "reset",
            "modeBarButtonsToRemove": [
                "lasso2d",
                "select2d",
                "drawline",
                "drawopenpath",
                "eraseshape",
            ],
            "toImageButtonOptions": {
                "format": "png",
                "filename": "quantlab_account_equity",
                "height": 900,
                "width": 1800,
                "scale": 1.5,
            },
        },
    )

    total_contributions = float(latest["total_contributions"])
    final_value = float(latest["total_value"])
    profit = final_value - total_contributions
    benchmark = float(latest["qqq_benchmark_value"])
    cumulative_return = final_value / total_contributions - 1
    max_drawdown = float(data["drawdown"].min())
    position = "TQQQ" if latest["position_status"] == "TQQQ" else "现金"

    page = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>QuantLab 账户收益曲线</title>
  <style>
    :root { color-scheme:dark;font-family:Inter,"Segoe UI",system-ui,sans-serif;--bg:#07111f;--panel:#0d1728;--line:#22304a;--text:#eef5ff;--muted:#8fa0b8;--blue:#4f8cff; }
    * { box-sizing:border-box; } html,body { margin:0;background:var(--bg);color:var(--text); }
    .stage:fullscreen { overflow:auto;background:var(--bg); }
    .top { position:sticky;top:0;z-index:20;padding:12px 14px 10px;border-bottom:1px solid var(--line);background:#091321f2;backdrop-filter:blur(14px); }
    .top-row { display:flex;align-items:center;justify-content:space-between;gap:14px; }
    .title { font-size:15px;font-weight:780; } .hint { margin-top:4px;color:var(--muted);font-size:10px; }
    .controls { display:flex;align-items:center;justify-content:flex-end;gap:7px;flex-wrap:wrap; }
    .group { display:flex;gap:3px;padding:3px;border:1px solid var(--line);border-radius:10px;background:#0a1322; }
    button { min-height:30px;padding:0 10px;border:0;border-radius:7px;background:transparent;color:#aebbd0;font:600 11px/1 inherit;cursor:pointer;white-space:nowrap; }
    button:hover { color:#fff;background:#ffffff0b; } button.active { color:#fff;background:#315fba;box-shadow:0 4px 14px #1d4b9c55; }
    button.action { border:1px solid var(--line);background:#111d30; } button.action:hover { border-color:#5274a7;background:#17263e; }
    .metrics { display:grid;grid-template-columns:repeat(6,minmax(110px,1fr));gap:8px;margin-top:10px; }
    .metric { padding:8px 10px;border:1px solid #ffffff0d;border-radius:9px;background:#ffffff04; }
    .metric span { display:block;color:var(--muted);font-size:9px;letter-spacing:.04em; } .metric strong { display:block;margin-top:3px;font-size:13px;font-variant-numeric:tabular-nums; }
    .green { color:#35d399; } .red { color:#fb7185; } #equity-chart { width:100%; }
    @media(max-width:920px) { .top { overflow-x:auto; } .top-row { min-width:850px; } .metrics { min-width:850px; } }
  </style>
</head>
<body>
  <main class="stage" id="chart-stage">
    <header class="top">
      <div class="top-row">
        <div><div class="title">账户收益曲线</div><div class="hint">拖动下方时间导航条缩放 · Y轴自动适配可见区间 · 数据截至 __LATEST_DATE__</div></div>
        <div class="controls">
          <div class="group"><button data-range="3">3月</button><button data-range="6">6月</button><button data-range="12">1年</button><button class="active" data-range="36">3年</button><button data-range="all">全部</button></div>
          <div class="group"><button class="active" data-mode="pan">拖动平移</button><button data-mode="zoom">框选缩放</button></div>
          <button class="action" id="reset-view">复位</button><button class="action" id="fullscreen">全屏</button><button class="action" id="export-image">导出</button>
        </div>
      </div>
      <div class="metrics">
        <div class="metric"><span>策略账户</span><strong>¥__FINAL_VALUE__</strong></div>
        <div class="metric"><span>累计投入</span><strong>¥__CONTRIBUTIONS__</strong></div>
        <div class="metric"><span>累计盈利</span><strong class="green">¥__PROFIT__</strong></div>
        <div class="metric"><span>累计收益</span><strong class="green">__RETURN__</strong></div>
        <div class="metric"><span>QQQ基准</span><strong>¥__BENCHMARK__</strong></div>
        <div class="metric"><span>最大回撤 · 当前仓位</span><strong class="red">__MAX_DD__ · __POSITION__</strong></div>
      </div>
    </header>
    __CHART__
  </main>
  <script>
    (() => {
      const chart = document.getElementById('equity-chart');
      const stage = document.getElementById('chart-stage');
      const end = new Date('__LATEST_DATE__T00:00:00');
      const defaultStart = new Date('__DEFAULT_START_DATE__T00:00:00');
      const equitySeries = __EQUITY_SERIES__;
      const axes = ['xaxis','xaxis2'];
      let yScaleTimer;
      function eventRange(event={}) {
        for (const axis of axes) {
          if (event[axis + '.autorange'] === true) return [null,null];
          const range = event[axis + '.range'];
          if (Array.isArray(range) && range.length >= 2) return range;
          const start = event[axis + '.range[0]'];
          const finish = event[axis + '.range[1]'];
          if (start !== undefined && finish !== undefined) return [start,finish];
        }
        return null;
      }
      function autoScaleAccount(startValue,endValue) {
        const start = startValue ? new Date(startValue).getTime() : -Infinity;
        const finish = endValue ? new Date(endValue).getTime() : Infinity;
        const values = [];
        for (let i=0;i<equitySeries.dates.length;i+=1) {
          const time = new Date(equitySeries.dates[i]).getTime();
          if (time < start || time > finish) continue;
          for (const series of [equitySeries.strategy,equitySeries.benchmark,equitySeries.contributions]) {
            const value = Number(series[i]);
            if (Number.isFinite(value)) values.push(value);
          }
        }
        if (!values.length) return;
        const low = Math.min(...values), high = Math.max(...values);
        const padding = Math.max((high-low)*0.08,high*0.015,1);
        Plotly.relayout(chart,{'yaxis.range':[Math.max(0,low-padding),high+padding]});
      }
      function scheduleAutoScale(range,delay=45) {
        clearTimeout(yScaleTimer);
        yScaleTimer=setTimeout(() => autoScaleAccount(range[0],range[1]),delay);
      }
      function setRange(months) {
        const update = {};
        let range;
        if (months === 'all') {
          axes.forEach(axis => update[axis + '.autorange'] = true);
          range=[null,null];
        }
        else {
          const start = new Date(end); start.setMonth(start.getMonth() - Number(months));
          axes.forEach(axis => update[axis + '.range'] = [start.toISOString(),end.toISOString()]);
          range=[start,end];
        }
        Plotly.relayout(chart,update);
        scheduleAutoScale(range,0);
        document.querySelectorAll('[data-range]').forEach(button => button.classList.toggle('active',button.dataset.range === String(months)));
      }
      chart.on('plotly_relayouting',event => {
        const range=eventRange(event); if (range) scheduleAutoScale(range,25);
      });
      chart.on('plotly_relayout',event => {
        const range=eventRange(event); if (range) scheduleAutoScale(range,0);
      });
      document.querySelectorAll('[data-range]').forEach(button => button.addEventListener('click',() => setRange(button.dataset.range)));
      document.querySelectorAll('[data-mode]').forEach(button => button.addEventListener('click',() => {
        Plotly.relayout(chart,{dragmode:button.dataset.mode});
        document.querySelectorAll('[data-mode]').forEach(item => item.classList.toggle('active',item === button));
      }));
      document.getElementById('reset-view').addEventListener('click',() => setRange('36'));
      document.getElementById('fullscreen').addEventListener('click',async () => {
        if (!document.fullscreenElement) await stage.requestFullscreen(); else await document.exitFullscreen();
        setTimeout(() => Plotly.Plots.resize(chart),120);
      });
      document.getElementById('export-image').addEventListener('click',() => Plotly.downloadImage(chart,{format:'png',filename:'quantlab_account_equity',width:1800,height:900,scale:1.5}));
      document.addEventListener('fullscreenchange',() => {
        document.getElementById('fullscreen').textContent=document.fullscreenElement?'退出全屏':'全屏';
        setTimeout(() => Plotly.Plots.resize(chart),120);
      });
      requestAnimationFrame(() => scheduleAutoScale([defaultStart,end],0));
    })();
  </script>
</body>
</html>
"""
    replacements = {
        "__LATEST_DATE__": f"{latest['trade_date']:%Y-%m-%d}",
        "__DEFAULT_START_DATE__": f"{default_start:%Y-%m-%d}",
        "__EQUITY_SERIES__": json.dumps(
            {
                "dates": data["trade_date"].dt.strftime("%Y-%m-%d").tolist(),
                "strategy": data["total_value"].astype(float).tolist(),
                "benchmark": data["qqq_benchmark_value"].astype(float).tolist(),
                "contributions": data["total_contributions"].astype(float).tolist(),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "__FINAL_VALUE__": f"{final_value:,.0f}",
        "__CONTRIBUTIONS__": f"{total_contributions:,.0f}",
        "__PROFIT__": f"{profit:+,.0f}",
        "__RETURN__": f"{cumulative_return:+.2%}",
        "__BENCHMARK__": f"{benchmark:,.0f}",
        "__MAX_DD__": f"{max_drawdown:.2%}",
        "__POSITION__": position,
        "__CHART__": chart_html,
    }
    for placeholder, value in replacements.items():
        page = page.replace(placeholder, value)
    path.write_text(page, encoding="utf-8")
    return path
