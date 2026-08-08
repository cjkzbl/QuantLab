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


def build_interactive_market_chart(
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
        title={
            "text": (
                f"QQQ SMA{sma_window} / TQQQ 策略分析"
                f"　·　数据截至 {latest['trade_date']:%Y-%m-%d}"
            ),
            "x": 0.02,
        },
        template="plotly_dark",
        height=1320,
        margin={"l": 62, "r": 215, "t": 112, "b": 50},
        hovermode="x unified",
        dragmode="zoom",
        barmode="overlay",
        legend={
            "orientation": "v",
            "yanchor": "top",
            "y": 1,
            "xanchor": "left",
            "x": 1.01,
            "font": {"size": 10},
            "bgcolor": "rgba(15,23,42,0.78)",
            "bordercolor": "rgba(148,163,184,0.25)",
            "borderwidth": 1,
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
    )
    figure.update_xaxes(
        row=1,
        col=1,
        rangeselector={
            "buttons": [
                {"count": 1, "label": "1月", "step": "month", "stepmode": "backward"},
                {"count": 3, "label": "3月", "step": "month", "stepmode": "backward"},
                {"count": 6, "label": "6月", "step": "month", "stepmode": "backward"},
                {"count": 1, "label": "1年", "step": "year", "stepmode": "backward"},
                {"count": 3, "label": "3年", "step": "year", "stepmode": "backward"},
                {"step": "all", "label": "全部"},
            ],
            "bgcolor": "#172033",
            "activecolor": "#334155",
            "font": {"color": "#e2e8f0"},
            "x": 0,
            "y": 1.01,
        },
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
    figure.write_html(
        path,
        include_plotlyjs=True,
        full_html=True,
        config={
            "responsive": True,
            "displaylogo": False,
            "scrollZoom": True,
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
    return path
