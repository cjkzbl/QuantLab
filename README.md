<<<<<<< HEAD
# QuantLab：QQQ SMA200 / TQQQ 模拟策略
=======
# QuantLab：QQQ SMA225 / TQQQ 模拟策略
>>>>>>> a72cec78bff35892eaca41bffef6fd208bc5f17e

这是一个只使用 QQQ 和 TQQQ 后复权日线数据的静态模拟项目。

策略规则：

- 回测首日投入 50 万，并一次性全仓买入 TQQQ；
<<<<<<< HEAD
- QQQ 当日收盘高于 SMA200，下一交易日开盘持有或全仓买入 TQQQ；
- QQQ 当日收盘低于 SMA200，下一交易日开盘清仓 TQQQ；
=======
- QQQ 当日收盘高于 SMA225，下一交易日开盘持有或全仓买入 TQQQ；
- QQQ 当日收盘低于 SMA225，下一交易日开盘清仓 TQQQ；
>>>>>>> a72cec78bff35892eaca41bffef6fd208bc5f17e
- 每月首个交易日向现金池投入 1 万元；
- 空仓后再次出现买入信号时，将现金池资金一次性买入；
- 默认成本为单边佣金 0.10%、单边滑点 0.20%、卖出附加费 0.10%；
- 暂不计算资本利得税；
- 使用相同资金流、长期持有 QQQ 的结果作为基准。

## 本地运行

使用仓库现有 CSV 生成报告和静态页面：

```powershell
python local_app.py
```

<<<<<<< HEAD
默认使用 SMA200。也可以临时指定其他周期进行对比，例如：

```powershell
python local_app.py --sma-window 225
```

生成后启动本地页面：

```powershell
python local_app.py --serve
```

先更新最新行情，再生成和启动：

```powershell
python local_app.py --refresh --serve
```

浏览器访问 `http://127.0.0.1:8000`。

QuantDash API Key 可以写入不会提交到 GitHub 的 `api.txt`，也可以设置环境变量：

=======
生成后启动本地页面：

```powershell
python local_app.py --serve
```

先更新最新行情，再生成和启动：

```powershell
python local_app.py --refresh --serve
```

浏览器访问 `http://127.0.0.1:8000`。

QuantDash API Key 可以写入不会提交到 GitHub 的 `api.txt`，也可以设置环境变量：

>>>>>>> a72cec78bff35892eaca41bffef6fd208bc5f17e
```powershell
$env:QUANTDASH_API_KEY = "你的 API Key"
```

行情脚本也可以单独运行：

```powershell
python get_data.py
```

## 详细账本

运行 `python local_app.py` 后，根目录的 `reports` 文件夹会生成：

| 文件 | 内容 |
| --- | --- |
| `summary.json` | 回测日期、最终资产、收益、最大回撤、当前仓位和信号 |
| `trade_details.csv` | 每笔买卖的信号日、交易日、成交价、股数、佣金、滑点、附加费、现金流和已实现盈亏 |
| `daily_positions.csv` | 每日现金、股数、平均成本、持仓成本、市值、清算净值、未实现盈亏和仓位比例 |
| `daily_changes.csv` | QQQ/TQQQ 每日涨跌、策略每日盈亏、收益率、累计收益、回撤、费用和 QQQ 基准 |
| `daily_full.csv` | 汇总以上字段的完整每日账本 |

静态网站还会生成三个完整明细页面，并提供 CSV 下载：

- `public/reports/trades.html`
- `public/reports/positions.html`
- `public/reports/changes.html`

`reports` 和 `public` 都是运行时生成目录，不提交到 Git；GitHub Actions 每次运行时会重新生成。

## GitHub Actions 自动更新

工作流文件是 `.github/workflows/deploy-pages.yml`。

- 推送到 `main`：使用仓库现有 CSV 重新生成并部署；
- 美东时间周一至周五 18:30：下载最新行情、运行策略、部署 Pages 并发邮件；
- 手动运行：可以选择是否更新行情、是否发送邮件；
- 定时任务和手动刷新需要 `QUANTDASH_API_KEY`。

GitHub Pages 地址：

```text
https://cjkzbl.github.io/QuantLab/
```

## 配置每日邮件

邮件使用标准 SMTP 发送，密码和地址全部保存在 GitHub Secrets，不写入代码。

打开：

```text
GitHub 仓库 → Settings → Secrets and variables → Actions
```

添加以下 Repository secrets：

| Secret | 含义 | 示例 |
| --- | --- | --- |
| `SMTP_HOST` | SMTP 服务器 | `smtp.qq.com` |
| `SMTP_PORT` | SMTP SSL 端口 | `465` |
| `SMTP_USERNAME` | 发件邮箱账号 | `name@qq.com` |
| `SMTP_PASSWORD` | SMTP 授权码或应用密码 | 不要使用网页登录密码 |
| `EMAIL_FROM` | 发件地址，可省略 | `name@qq.com` |
| `EMAIL_TO` | 收件地址；多个地址用逗号分隔 | `receiver@example.com` |

常见配置：

- QQ 邮箱：`smtp.qq.com`、端口 `465`，密码填写 QQ 邮箱生成的授权码；
- Gmail：`smtp.gmail.com`、端口 `465`，密码填写 Google 应用专用密码；
- 其他邮箱：填写服务商提供的 SMTP SSL 地址、端口和授权码。

邮件包含：

- 本次构建和部署状态；
- 数据日期、策略资产、QQQ 基准、累计投入；
- 累计收益率、最大回撤、当前仓位和最新信号；
- 成功构建时附带全部 CSV 账本的 ZIP 压缩包；
- 本次 GitHub Actions 运行记录链接。

设置完成后，在 Actions 中手动运行一次工作流，并保持：

```text
refresh = true
send_email = true
```

即可同时测试行情刷新、Pages 部署和邮件发送。
