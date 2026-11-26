# QuantLab · QQQ / TQQQ 50/50

[查看策略看板](https://cjkzbl.github.io/QuantLab/)

本项目用 QQQ 提供趋势信号，回测 TQQQ 和 BIL 的组合。固定将资金各分一半给趋势规则和波动规则，两者的 TQQQ 目标相加，剩余资金全部配置 BIL。

- **趋势仓。** QQQ 收盘高于 SMA200 × 1.04 时开启，低于 SMA200 时退出，中间区域延续前一状态。开启时将这一半配置给 TQQQ。
- **波动仓。** 每月首个共同交易日收盘，用 TQQQ 最近 20 个交易日收益率的年化波动率估算仓位。仓内 TQQQ 比例为 `min(100%, 30% / 年化波动率)`，持续到下次更新。
- **执行。** 初始投入 1 万并买入 BIL。每月首个交易日开盘投入 1 万，按当时已生效的目标配置。收盘信号从下一交易日开盘生效，模拟使用开盘价和固定滑点实现目标权重。波动仓首日收盘的新目标在次日开盘生效。
- **成本。** 零佣金，TQQQ 单边滑点 0.05%，QQQ 和 BIL 单边滑点 0.01%，模型卖出费率 0.00206%，未计资本利得税和汇率变动。长期基准按同一现金流定投 QQQ。

## 本地运行

使用 Python 3.13。将 QuantDash Key 写入本地 `api.txt` 或设置 `QUANTDASH_API_KEY`。

```powershell
python -m pip install -r requirements.txt
python local_app.py --refresh --serve
```

浏览器访问 `http://127.0.0.1:8000`。行情缓存放在 `.market-data/`。已有缓存时可以用 `python local_app.py` 离线生成，或通过 `--data-dir` 读取包含 `qqq_daily.csv`、`tqqq_daily.csv` 和 `bil_daily.csv` 的目录。`--live-start-date YYYY-MM-DD` 可覆盖起点风险页的自选起点。

`reports/` 包含账户、逐笔交易、每日持仓及收益账本，`public/` 包含可离线使用的交互图和明细页。起点风险页从 2010-03-11 起使用真实 ETF 数据，提供三个独立时段和按季滚动的 1、3、5 年样本。净值与回撤剔除定投资金影响，金额按名义资金单位列示。

## Pages 和每日邮件

`.github/workflows/deploy-pages.yml` 在推送到 `main`、手动触发及美东周一至周五 18:30 的计划任务中刷新 QQQ、TQQQ 和 BIL，校验共同交易日，生成并发布 Pages。行情缓存留在运行环境。报告通过 Actions artifact 提供给邮件环节。

计划任务会在构建和部署后发出提醒。手动触发可用 `send_email` 选项控制发送。成功报告包含 TQQQ 和 BIL 的收盘持仓和下次开盘目标、QQQ SMA200 信号、波动率测算日和模拟账本附件。构建失败的消息附有运行记录。

GitHub Actions 所需 Secrets：

- `QUANTDASH_API_KEY`
- `SMTP_HOST`、`SMTP_PORT`（默认 `465`）、`SMTP_USERNAME`、`SMTP_PASSWORD`
- `EMAIL_TO`（多个收件人用逗号分隔）、`EMAIL_FROM`（可选，默认发件账号）

检查邮件排版和附件时使用 `python send_report_email.py --dry-run`。测试入口为 `python -m unittest discover -v`。
