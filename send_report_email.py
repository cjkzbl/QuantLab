"""Send the same 50/50 targets published on Pages through the existing SMTP account."""

import argparse
import html
import io
import json
import os
import smtplib
import ssl
import zipfile
from email.message import EmailMessage
from pathlib import Path


ASSETS = ("TQQQ", "BIL")
SUBJECT_PREFIX = "[QuantLab 50/50 BIL]"
SITE = "https://cjkzbl.github.io/QuantLab/"
ATTACHMENTS = ("summary.json", "trade_details.csv", "daily_positions.csv", "daily_changes.csv", "daily_full.csv")


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量或 GitHub Secret：{name}")
    return value


def load_summary(report_dir):
    file = report_dir / "summary.json"
    if not file.is_file():
        return None
    data = json.loads(file.read_text(encoding="utf-8"))
    return data if data.get("strategy_id") == "qqq-tqqq-5050-bil" else None


def zip_reports(report_dir):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ATTACHMENTS:
            path = report_dir / name
            if path.is_file():
                archive.write(path, arcname=name)
    return buffer.getvalue()


def value(number, spec):
    return f"{number:{spec}}" if number is not None else "—"


def build_message(report_dir, sender, recipients, *, build_result, deploy_result, workflow_url=""):
    report_dir = Path(report_dir)
    summary = load_summary(report_dir) if build_result == "success" else None
    status = f"报告 {build_result} · Pages {deploy_result}"
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    lines = [status]
    table_rows = ""
    signal_details = []

    if summary:
        date = summary["end_date"]
        action = summary["next_action"]
        message["Subject"] = f"{SUBJECT_PREFIX} {date} · {action}"
        lines.extend([f"最新共同收盘：{date}", f"下一交易日开盘：{action}", "", "标的 | 收盘持仓 | 开盘目标 | 日涨跌"])
        for asset in ASSETS:
            current = value(summary["current_weights"][asset], ".2%")
            target = value(summary["target_weights"][asset], ".2%")
            change = value(summary["latest_changes"][asset], "+.2%")
            lines.append(f"{asset} | {current} | {target} | {change}")
            table_rows += (
                f'<tr><td style="padding:9px 8px;border-top:1px solid #dde6ef"><strong>{asset}</strong></td>'
                f'<td align="right">{current}</td><td align="right"><strong>{target}</strong></td><td align="right">{change}</td></tr>'
            )
        signal_details = [
            "趋势与波动各占一半，TQQQ 之外的资金全部配置 BIL。首次跟踪本策略时可用开盘目标核对实际仓位。",
            f"趋势仓 {summary['trend_label']}，QQQ 后复权收盘 {value(summary['qqq_close'], ',.2f')}"
            f"（{value(summary['latest_changes']['QQQ'], '+.2%')}）",
            f"趋势开启线 {value(summary['qqq_entry_line'], ',.2f')}，退出线 {value(summary['qqq_sma'], ',.2f')}",
            f"TQQQ 月度波动率 {value(summary['active_volatility'], '.2%')}，取自 {summary['volatility_sample_date']} 收盘",
            f"波动仓内 TQQQ 比例 {value(summary['volatility_tqqq_ratio'], '.2%')}",
            f"最新一日收益 {value(summary['latest_daily_return'], '+.2%')}，剔除投入的盈亏 {value(summary['latest_daily_profit'], '+,.2f')}",
            f"累计投入 {value(summary['total_contributions'], ',.2f')}，期末模拟资产 {value(summary['final_value'], ',.2f')}",
            f"全期时间加权年化 {value(summary['annualized_return'], '.2%')}，最大回撤 {value(summary['max_drawdown'], '.2%')}",
            f"该日共 {summary['latest_trade_count']} 笔模拟成交。月度新增资金按开盘时已生效的目标投入。",
        ]
        lines.extend(["", *signal_details])
        intro = f"{date} 收盘 · {action}"
    else:
        message["Subject"] = f"{SUBJECT_PREFIX} 本次报告不可用"
        intro = "本次缺少可用的 BIL 防守版报告，请查看运行记录。"
        lines.append(intro)

    links = [("策略看板", SITE)]
    if workflow_url.startswith("https://"):
        links.append(("运行记录", workflow_url))
    lines += ["", *[f"{label}：{url}" for label, url in links]]
    message.set_content("\n".join(lines))
    link_html = "　".join(f'<a style="color:#187b69" href="{html.escape(url, quote=True)}">{label}</a>' for label, url in links)
    allocation = f'''<table width="100%" cellpadding="8" cellspacing="0" style="font-size:14px;background:#f0f6f8;border-radius:9px">
<thead><tr><th align="left">标的</th><th align="right">收盘持仓</th><th align="right">开盘目标</th><th align="right">日涨跌</th></tr></thead>
<tbody>{table_rows}</tbody></table>''' if table_rows else ""
    details = "".join(f'<p style="margin:7px 0">{html.escape(item)}</p>' for item in signal_details)
    message.add_alternative(f'''<!doctype html><html lang="zh-CN"><meta charset="UTF-8">
<body style="background:#eef3f7;color:#172a3a;font-family:Arial,'Microsoft YaHei',sans-serif;margin:0;padding:16px">
<div style="max-width:650px;margin:auto;background:#fff;border-radius:14px;padding:20px">
<div style="font-size:13px;color:#526779">QuantLab · 50/50 趋势与波动 · BIL 防守 · {html.escape(status)}</div>
<h2 style="font-size:21px;margin:10px 0 16px">{html.escape(intro)}</h2>
{allocation}<div style="font-size:14px;line-height:1.5;margin-top:14px">{details}</div>
<div style="margin-top:16px;font-size:13px">{link_html}</div></div></body></html>''', subtype="html")
    if summary:
        message.add_attachment(zip_reports(report_dir), maintype="application", subtype="zip", filename=f"quantlab-5050-{date}.zip")
    return message


def send_email(report_dir, dry_run=False):
    username = os.getenv("SMTP_USERNAME", "").strip()
    if not dry_run:
        username = required_env("SMTP_USERNAME")
    sender = os.getenv("EMAIL_FROM", "").strip() or username or "dry-run@example.com"
    recipient_text = os.getenv("EMAIL_TO", "").strip() if dry_run else required_env("EMAIL_TO")
    recipients = [item.strip() for item in (recipient_text or "dry-run@example.com").replace(";", ",").split(",") if item.strip()]
    if not recipients:
        raise RuntimeError("EMAIL_TO 中没有有效的收件地址")
    message = build_message(
        report_dir, sender, recipients,
        build_result=os.getenv("BUILD_RESULT", "success" if dry_run else "unknown"),
        deploy_result=os.getenv("DEPLOY_RESULT", "unknown"),
        workflow_url=os.getenv("WORKFLOW_URL", ""),
    )
    if dry_run:
        print(f"邮件试生成成功 · {message['Subject']} · 附件 {sum(1 for _ in message.iter_attachments())}")
        return message

    host = required_env("SMTP_HOST")
    password = required_env("SMTP_PASSWORD")
    port = int(os.getenv("SMTP_PORT", "").strip() or "465")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as smtp:
            smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(username, password)
            smtp.send_message(message)
    print(f"已发送：{message['Subject']}")
    return message


def main():
    parser = argparse.ArgumentParser(description="发送 QuantLab 50/50 每日模拟提醒")
    parser.add_argument("--report-dir", type=Path, default=Path("reports"))
    parser.add_argument("--dry-run", action="store_true", help="只生成邮件，不连接 SMTP")
    args = parser.parse_args()
    message = send_email(args.report_dir, args.dry_run)
    if args.dry_run and os.getenv("BUILD_RESULT", "success") == "success" and not list(message.iter_attachments()):
        raise RuntimeError("缺少新策略的日报或附件")


if __name__ == "__main__":
    main()
