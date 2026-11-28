"""Send the same 50/50 targets published on Pages through the existing SMTP account."""

import argparse
import json
import math
import os
import smtplib
import ssl
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from email.message import EmailMessage
from pathlib import Path


ASSETS = ("TQQQ", "BIL")
SUBJECT_PREFIX = "[QuantLab 50/50 BIL]"
ACTIONS = ("按新目标调仓", "延续目标仓位")


def required_env(name):
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"缺少环境变量或 GitHub Secret：{name}")
    return value


def load_summary(report_dir):
    try:
        data = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
        if data["strategy_id"] != "qqq-tqqq-5050-bil" or data["next_action"] not in ACTIONS:
            return None
        if date.fromisoformat(data["end_date"]).isoformat() != data["end_date"]:
            return None
        weights = data["target_weights"]
        if not isinstance(weights, dict) or set(weights) != set(ASSETS):
            return None
        if any(type(weight) not in (float, int) or not 0 <= weight <= 1 for weight in weights.values()):
            return None
        if math.isclose(sum(weights.values()), 1.0, abs_tol=1e-8, rel_tol=0):
            return data
    except (OSError, KeyError, TypeError, ValueError):
        return None
    return None


def build_message(report_dir, sender, recipients, *, build_result, deploy_result, workflow_url="", require_summary=False):
    summary = load_summary(Path(report_dir)) if build_result == "success" else None
    if require_summary and summary is None:
        raise RuntimeError("缺少有效的 50/50 BIL 策略目标仓位")
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    if summary:
        signal_date = summary["end_date"]
        action = summary["next_action"]
        tqqq = (Decimal(str(summary["target_weights"]["TQQQ"])) * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        bil = Decimal("100.00") - tqqq
        allocation = f"TQQQ {tqqq:.2f}% / BIL {bil:.2f}%"
        message["Subject"] = f"{SUBJECT_PREFIX} {allocation} · {action}"
        lines = [allocation, f"下一交易日开盘 · {action}", f"信号来自美东 {signal_date} 收盘"]
    else:
        message["Subject"] = f"{SUBJECT_PREFIX} 本次目标仓位不可用"
        lines = ["本次未生成有效的目标仓位。", f"构建 {build_result} · Pages {deploy_result}"]
        if workflow_url.startswith("https://"):
            lines.extend(["运行记录", workflow_url])
    message.set_content("\n".join(lines))
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
    build_result = os.getenv("BUILD_RESULT", "success" if dry_run else "unknown")
    message = build_message(
        report_dir, sender, recipients,
        build_result=build_result,
        deploy_result=os.getenv("DEPLOY_RESULT", "unknown"),
        workflow_url=os.getenv("WORKFLOW_URL", ""),
        require_summary=dry_run and build_result == "success",
    )
    if dry_run:
        print(f"邮件试生成成功 · {message['Subject']}")
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
    parser = argparse.ArgumentParser(description="发送 QuantLab 50/50 目标仓位提醒")
    parser.add_argument("--report-dir", type=Path, default=Path("reports"))
    parser.add_argument("--dry-run", action="store_true", help="只生成邮件，不连接 SMTP")
    args = parser.parse_args()
    send_email(args.report_dir, args.dry_run)


if __name__ == "__main__":
    main()
