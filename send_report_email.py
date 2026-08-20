import argparse
import html
import io
import json
import os
import smtplib
import zipfile
from email.message import EmailMessage
from pathlib import Path


def _required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量或 GitHub Secret：{name}")
    return value


def _load_summary(report_dir):
    path = report_dir / "summary.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _make_zip(report_dir):
    filenames = [
        "summary.json",
        "trade_details.csv",
        "daily_positions.csv",
        "daily_changes.csv",
        "daily_full.csv",
    ]
    buffer = io.BytesIO()
    included = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for filename in filenames:
            path = report_dir / filename
            if path.exists():
                archive.write(path, arcname=filename)
                included += 1
    return buffer.getvalue() if included else None


def _money(value):
    return f"{float(value):,.2f}" if value is not None else "无数据"


def _signed_money(value):
    return f"{float(value):+,.2f}" if value is not None else "无数据"


def _percent(value):
    return f"{float(value):+.2%}" if value is not None else "无数据"


def _number(value):
    return f"{float(value):,.2f}" if value is not None else "无数据"


def _regime_name(value):
    return {"bull": "牛市", "bear": "熊市", "neutral": "中性"}.get(
        str(value), str(value) if value is not None else "无数据"
    )


def send_email(report_dir, dry_run=False):
    host = os.getenv("SMTP_HOST", "").strip()
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    if not dry_run:
        host = host or _required_env("SMTP_HOST")
        username = username or _required_env("SMTP_USERNAME")
        password = password or _required_env("SMTP_PASSWORD")
    recipients = [
        address.strip()
        for address in (
            os.getenv("EMAIL_TO", "dry-run@example.com")
            if dry_run
            else _required_env("EMAIL_TO")
        ).replace(";", ",").split(",")
        if address.strip()
    ]
    if not recipients:
        raise RuntimeError("EMAIL_TO 中没有有效的收件地址")

    port = int(os.getenv("SMTP_PORT", "465"))
    sender = os.getenv("EMAIL_FROM", "").strip() or username
    build_result = os.getenv("BUILD_RESULT", "unknown")
    deploy_result = os.getenv("DEPLOY_RESULT", "unknown")
    workflow_url = os.getenv("WORKFLOW_URL", "").strip()
    summary = _load_summary(report_dir)
    success = build_result == "success" and deploy_result == "success"
    status_text = "成功" if success else "失败"
    data_date = summary.get("end_date", "无数据")
    next_action_text = summary.get("next_action_text", "无数据")
    next_action_reason = summary.get("next_action_reason", "无数据")
    daily_return_text = _percent(summary.get("latest_daily_return"))
    sma_window = summary.get("sma_window", 200)
    latest_sma = summary.get("latest_sma")
    bull_multiplier = summary.get("bull_multiplier", 1.04)
    bear_multiplier = summary.get("bear_multiplier", 0.97)
    bull_threshold = (
        float(latest_sma) * float(bull_multiplier) if latest_sma is not None else None
    )
    bear_threshold = (
        float(latest_sma) * float(bear_multiplier) if latest_sma is not None else None
    )
    dip_threshold = summary.get("dip_threshold", 0.01)
    qqq_change = summary.get("latest_qqq_daily_change")
    latest_regime = summary.get("latest_signal")
    regime_text = _regime_name(latest_regime)
    dip_triggered = (
        latest_regime == "bull"
        and qqq_change is not None
        and float(qqq_change) <= -float(dip_threshold)
    )
    dip_status = "已触发" if dip_triggered else "未触发"
    regime_color = {"bull": "#059669", "bear": "#e11d48"}.get(
        latest_regime, "#d97706"
    )
    action_color = {
        "buy": "#059669",
        "sell": "#e11d48",
        "hold": "#2563eb",
    }.get(summary.get("next_action"), "#2563eb")
    action_background = {
        "buy": "#ecfdf5",
        "sell": "#fff1f2",
        "hold": "#eff6ff",
    }.get(summary.get("next_action"), "#eff6ff")
    action_border = {
        "buy": "#a7f3d0",
        "sell": "#fecdd3",
        "hold": "#bfdbfe",
    }.get(summary.get("next_action"), "#bfdbfe")
    regime_background = {
        "bull": "#ecfdf5",
        "bear": "#fff1f2",
    }.get(latest_regime, "#fffbeb")

    message = EmailMessage()
    if success and summary:
        message["Subject"] = (
            f"[QuantLab] {data_date}｜{regime_text}｜下一交易日：{next_action_text}"
        )
    else:
        message["Subject"] = f"[QuantLab] 每日模拟{status_text} - {data_date}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    text = (
        f"QuantLab 每日交易简报｜{data_date}\n"
        f"运行状态：构建 {build_result} / 部署 {deploy_result}\n\n"
        f"【前一交易日收盘指标】\n"
        f"市场状态：{regime_text}\n"
        f"QQQ 收盘：{_number(summary.get('latest_qqq_close'))}\n"
        f"SMA{sma_window}：{_number(latest_sma)}\n"
        f"牛市确认线（SMA×{float(bull_multiplier):.2f}）：{_number(bull_threshold)}\n"
        f"熊市确认线（SMA×{float(bear_multiplier):.2f}）：{_number(bear_threshold)}\n"
        f"QQQ 单日涨跌：{_percent(qqq_change)}\n"
        f"TQQQ 单日涨跌：{_percent(summary.get('latest_tqqq_daily_change'))}\n"
        f"BIL 单日涨跌：{_percent(summary.get('latest_bil_daily_change'))}\n"
        f"回调买入条件：QQQ ≤ -{float(dip_threshold):.2%}（{dip_status}）\n"
        f"当前仓位：{summary.get('current_position', '无数据')}\n\n"
        f"【下一交易日操作】\n"
        f"操作：{next_action_text}\n"
        f"执行时间：下一交易日开盘\n"
        f"依据：{next_action_reason}\n\n"
        f"【账户摘要】\n"
        f"上一交易日实际动作：{summary.get('today_action_text', '无数据')}\n"
        f"当日策略盈亏：{_signed_money(summary.get('latest_daily_profit'))}\n"
        f"当日策略收益率：{daily_return_text}\n"
        f"策略资产：{_money(summary.get('final_value'))}\n"
        f"QQQ 基准：{_money(summary.get('qqq_benchmark_final_value'))}\n"
        f"累计投入：{_money(summary.get('total_contributions'))}\n"
        f"策略累计收益率：{_percent(summary.get('return_rate'))}\n"
        f"最大回撤：{_percent(summary.get('max_drawdown'))}\n"
        f"工作流：{workflow_url or '未提供'}\n"
    )
    message.set_content(text)
    message.add_alternative(
        f"""\
<!doctype html>
<html lang="zh-CN">
<body style="margin:0;background:#f3f6fa;color:#172033;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">
  <div style="display:none;max-height:0;overflow:hidden">{data_date} 收盘指标：{regime_text}；下一交易日：{html.escape(str(next_action_text))}</div>
  <div style="max-width:680px;margin:0 auto;padding:24px 12px">
    <div style="background:#0b1324;border-radius:16px 16px 0 0;padding:24px;color:#fff">
      <div style="font-size:12px;letter-spacing:.12em;color:#93a4bd">QUANTLAB DAILY BRIEF</div>
      <h1 style="margin:9px 0 5px;font-size:25px">每日交易简报</h1>
      <div style="color:#aab8cb;font-size:13px">前一交易日收盘：{data_date} · 构建 {build_result} · 部署 {deploy_result}</div>
    </div>

    <div style="background:#fff;padding:20px;border-left:1px solid #e3e8ef;border-right:1px solid #e3e8ef">
      <div style="font-size:12px;font-weight:700;color:#64748b;letter-spacing:.08em">下一交易日操作</div>
      <div style="margin-top:10px;padding:18px;border-radius:12px;background:{action_background};border:1px solid {action_border}">
        <div style="font-size:25px;font-weight:800;color:{action_color}">{html.escape(str(next_action_text))}</div>
        <div style="margin-top:7px;font-size:13px;color:#526176">执行时间：下一交易日开盘</div>
        <div style="margin-top:9px;font-size:14px;line-height:1.65">{html.escape(str(next_action_reason))}</div>
      </div>
    </div>

    <div style="background:#fff;padding:2px 20px 20px;border-left:1px solid #e3e8ef;border-right:1px solid #e3e8ef">
      <div style="display:flex;align-items:center;justify-content:space-between;margin:0 0 10px">
        <div style="font-size:12px;font-weight:700;color:#64748b;letter-spacing:.08em">前一交易日收盘指标</div>
        <span style="padding:5px 10px;border-radius:999px;background:{regime_background};color:{regime_color};font-size:12px;font-weight:800">{regime_text}</span>
      </div>
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;font-size:14px">
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">QQQ 收盘</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_number(summary.get("latest_qqq_close"))}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">SMA{sma_window}</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_number(latest_sma)}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">牛市线 · SMA×{float(bull_multiplier):.2f}</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_number(bull_threshold)}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">熊市线 · SMA×{float(bear_multiplier):.2f}</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_number(bear_threshold)}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">QQQ 单日涨跌</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_percent(qqq_change)}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">TQQQ 单日涨跌</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_percent(summary.get("latest_tqqq_daily_change"))}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">BIL 单日涨跌</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">{_percent(summary.get("latest_bil_daily_change"))}</td></tr>
        <tr><td style="padding:10px;border-bottom:1px solid #edf1f5;color:#64748b">回调买入阈值</td><td align="right" style="padding:10px;border-bottom:1px solid #edf1f5;font-weight:700">≤ -{float(dip_threshold):.2%} · {dip_status}</td></tr>
        <tr><td style="padding:10px;color:#64748b">当前仓位</td><td align="right" style="padding:10px;font-weight:700">{html.escape(str(summary.get("current_position", "无数据")))}</td></tr>
      </table>
    </div>

    <div style="background:#fff;padding:2px 20px 22px;border:1px solid #e3e8ef;border-top:0;border-radius:0 0 16px 16px">
      <div style="font-size:12px;font-weight:700;color:#64748b;letter-spacing:.08em;margin-bottom:10px">账户摘要</div>
      <table role="presentation" width="100%" cellpadding="8" cellspacing="0" style="border-collapse:collapse;background:#f7f9fc;border-radius:10px;font-size:13px">
        <tr><td style="color:#64748b">策略资产</td><td align="right"><strong>¥{_money(summary.get("final_value"))}</strong></td></tr>
        <tr><td style="color:#64748b">当日盈亏 / 收益率</td><td align="right"><strong>¥{_signed_money(summary.get("latest_daily_profit"))} / {daily_return_text}</strong></td></tr>
        <tr><td style="color:#64748b">上一交易日实际动作</td><td align="right"><strong>{html.escape(str(summary.get("today_action_text", "无数据")))}</strong></td></tr>
        <tr><td style="color:#64748b">累计投入 / 最大回撤</td><td align="right"><strong>¥{_money(summary.get("total_contributions"))} / {_percent(summary.get("max_drawdown"))}</strong></td></tr>
      </table>
      <div style="margin-top:18px;text-align:center;font-size:12px;color:#7b899d">
        <a href="{html.escape(workflow_url, quote=True)}" style="color:#2563eb;text-decoration:none">查看 GitHub Actions 运行记录</a>
        <span style="padding:0 7px">·</span>附件包含全部交易及每日账本 CSV
      </div>
      <div style="margin-top:12px;text-align:center;font-size:11px;color:#9aa5b5">历史模拟不代表未来收益，请在实际交易前核对行情和成交条件。</div>
    </div>
  </div>
</body>
</html>
""",
        subtype="html",
    )

    report_zip = _make_zip(report_dir)
    if report_zip:
        message.add_attachment(
            report_zip,
            maintype="application",
            subtype="zip",
            filename=f"quantlab-report-{data_date}.zip",
        )

    if dry_run:
        print(
            f"邮件试生成成功：主题={message['Subject']}，"
            f"收件人={message['To']}，附件={bool(report_zip)}"
        )
        return message

    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(message)

    print(f"邮件已发送至：{', '.join(recipients)}")


def main():
    parser = argparse.ArgumentParser(description="发送 QuantLab 每日模拟邮件")
    parser.add_argument("--report-dir", default="reports", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="只生成邮件，不连接 SMTP")
    args = parser.parse_args()
    send_email(args.report_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
