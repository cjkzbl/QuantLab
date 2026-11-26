"""Build the self-contained account, risk, and allocation chart for Pages."""

from pathlib import Path

import plotly.graph_objects as go
from plotly.subplots import make_subplots


ASSET_COLORS = {"TQQQ": "#62d9c2", "BIL": "#73aafa"}


def build_interactive_market_chart(daily, filename="public/interactive_market_chart.html"):
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, vertical_spacing=.075,
        subplot_titles=("时间加权净值 · 对数轴", "账户资产与累计投入 · 万", "净值回撤", "每日收盘持仓"),
        row_heights=[.30, .26, .22, .22],
    )
    date = daily.trade_date
    strategy, benchmark = "#62d9c2", "#9cacc1"
    for row, column, name, color, shown in (
        (1, "nav", "50/50 策略", strategy, True), (1, "qqq_benchmark_nav", "QQQ 定投", benchmark, True),
        (2, "total_value", "50/50 策略", strategy, False), (2, "qqq_benchmark_value", "QQQ 定投", benchmark, False),
        (2, "total_contributions", "累计投入", "#e7b96f", True),
        (3, "drawdown", "50/50 策略", strategy, False), (3, "qqq_benchmark_drawdown", "QQQ 定投", benchmark, False),
    ):
        values = daily[column] / 10_000 if row == 2 else daily[column]
        spec = ",.2f" if row < 3 else ".2%"
        fig.add_trace(go.Scatter(
            x=date, y=values, mode="lines", name=name, showlegend=shown, legendgroup=name,
            line=dict(color=color, width=2 if name == "50/50 策略" else 1.3,
                      dash="dash" if column == "total_contributions" else "solid"),
            hovertemplate=f"%{{y:{spec}}}",
        ), row=row, col=1)
    for asset, color in ASSET_COLORS.items():
        fig.add_trace(go.Scatter(
            x=date, y=daily[f"weight_{asset.lower()}"], name=asset, stackgroup="weights",
            mode="lines", line=dict(color=color, width=.7), fillcolor=color, hovertemplate="%{y:.1%}",
        ), row=4, col=1)

    fig.update_layout(
        height=860, autosize=True, hovermode="x unified", hoverlabel=dict(namelength=-1),
        margin=dict(l=58, r=24, t=138, b=46),
        paper_bgcolor="#101b2a", plot_bgcolor="#101b2a",
        font=dict(family='"Microsoft YaHei", system-ui, sans-serif', color="#bccada", size=11),
        legend=dict(orientation="h", y=1.18, yanchor="top", x=0, font=dict(size=10)),
    )
    fig.update_annotations(font_size=12, font_color="#d9e5ef")
    fig.update_xaxes(gridcolor="#22334a", hoverformat="%Y-%m-%d", tickformat="%Y")
    fig.update_yaxes(gridcolor="#22334a", fixedrange=False, zerolinecolor="#344761")
    nav_ticks = [.5, 1, 2, 5, 10, 20, 50, 100]
    fig.update_yaxes(type="log", tickvals=nav_ticks, ticktext=[f"{n:g}×" for n in nav_ticks], row=1, col=1)
    fig.update_yaxes(tickformat=".0%", row=3, col=1)
    fig.update_yaxes(tickformat=".0%", range=[0, 1], row=4, col=1)
    fig.update_xaxes(rangeselector=dict(
        buttons=[dict(count=n, label=f"{n} 年", step="year", stepmode="backward") for n in (1, 3, 5)]
        + [dict(label="全期", step="all")],
        bgcolor="#20324a", activecolor="#2d5260", font=dict(color="#e0e9f1", size=10), y=1.045, x=1,
        xanchor="right",
    ), row=1, col=1)
    html = fig.to_html(full_html=False, include_plotlyjs=True, config={
        "responsive": True, "displaylogo": False, "modeBarButtonsToRemove": ["select2d", "lasso2d"],
    })
    path = Path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>50/50 策略收益与持仓 · BIL 防守</title><style>body{{margin:0;background:#101b2a}}</style>
<body>{html}</body></html>''', encoding="utf-8")
    return path
