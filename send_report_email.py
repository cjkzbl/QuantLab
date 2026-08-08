import argparse
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


def _percent(value):
    return f"{float(value):+.2%}" if value is not None else "无数据"


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

    message = EmailMessage()
    message["Subject"] = f"[QuantLab] 每日模拟{status_text} - {data_date}"
    message["From"] = sender
    message["To"] = ", ".join(recipients)

    text = (
        f"QuantLab 每日工作流运行{status_text}\n"
        f"构建状态：{build_result}\n"
        f"部署状态：{deploy_result}\n"
        f"数据日期：{data_date}\n"
<<<<<<< HEAD
        f"策略参数：SMA{summary.get('sma_window', '无数据')}\n"
=======
>>>>>>> a72cec78bff35892eaca41bffef6fd208bc5f17e
        f"策略资产：{_money(summary.get('final_value'))}\n"
        f"QQQ 基准：{_money(summary.get('qqq_benchmark_final_value'))}\n"
        f"累计投入：{_money(summary.get('total_contributions'))}\n"
        f"策略累计收益率：{_percent(summary.get('return_rate'))}\n"
        f"最大回撤：{_percent(summary.get('max_drawdown'))}\n"
        f"当前仓位：{summary.get('current_position', '无数据')}\n"
        f"最新信号：{summary.get('latest_signal', '无数据')}\n"
        f"工作流：{workflow_url or '未提供'}\n"
    )
    message.set_content(text)
    message.add_alternative(
        f"""\
<!doctype html>
<html lang="zh-CN"><body style="font-family:system-ui,sans-serif">
  <h2>QuantLab 每日工作流运行{status_text}</h2>
  <table cellpadding="7" style="border-collapse:collapse">
    <tr><td>构建状态</td><td><strong>{build_result}</strong></td></tr>
    <tr><td>部署状态</td><td><strong>{deploy_result}</strong></td></tr>
    <tr><td>数据日期</td><td>{data_date}</td></tr>
<<<<<<< HEAD
    <tr><td>策略参数</td><td>SMA{summary.get("sma_window", "无数据")}</td></tr>
=======
>>>>>>> a72cec78bff35892eaca41bffef6fd208bc5f17e
    <tr><td>策略资产</td><td>{_money(summary.get("final_value"))}</td></tr>
    <tr><td>QQQ 基准</td><td>{_money(summary.get("qqq_benchmark_final_value"))}</td></tr>
    <tr><td>累计投入</td><td>{_money(summary.get("total_contributions"))}</td></tr>
    <tr><td>策略累计收益率</td><td>{_percent(summary.get("return_rate"))}</td></tr>
    <tr><td>最大回撤</td><td>{_percent(summary.get("max_drawdown"))}</td></tr>
    <tr><td>当前仓位</td><td>{summary.get("current_position", "无数据")}</td></tr>
    <tr><td>最新信号</td><td>{summary.get("latest_signal", "无数据")}</td></tr>
  </table>
  <p><a href="{workflow_url}">查看 GitHub Actions 运行记录</a></p>
  <p>构建成功时，本邮件附件包含全部交易、每日持仓和每日涨跌 CSV。</p>
</body></html>
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
