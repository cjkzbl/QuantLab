# QQQ / TQQQ 行情获取

在 `api.txt` 中填写 QuantDash API Key，然后调用：

```python
from get_data import get_qqq_data, get_tqqq_data

qqq = get_qqq_data()
tqqq = get_tqqq_data()
```

两个函数均返回标的从上市至今的后复权日线 DataFrame。

直接运行脚本可获取并保存两个 CSV 文件：

```powershell
python get_data.py
```

输出文件为 `qqq_daily.csv` 和 `tqqq_daily.csv`。

也可以自行指定保存文件：

```python
from get_data import get_qqq_data, save_data

save_data(get_qqq_data(), "my_qqq.csv")
```

计算简单移动平均线：

```python
from get_data import load_data
from strategy import moving_average

qqq = load_data("qqq_daily.csv")
qqq["ma20"] = moving_average(qqq, window=20)
```

也可以直接运行策略文件，它会读取 `qqq_daily.csv` 并输出最近的
收盘价和 20 日均线：

```powershell
python strategy.py
```

默认策略使用 QQQ 的 225 日简单移动平均线择时，并交易 TQQQ：

- 初始投入 50 万元并在回测首日全仓买入 TQQQ；
- QQQ 当日收盘价高于 SMA225，下一交易日开盘全仓买入 TQQQ；
- QQQ 当日收盘价低于 SMA225，下一交易日开盘清仓 TQQQ；
- 每月首个交易日向现金池加入 1 万元；
- 现金池资金在下一次由空仓转为买入时一并投入。

运行策略后还会按照每日回测数据生成 `sma225_daily_curve.png`，曲线包含
策略总资产、QQQ 基准资产和累计投入。QQQ 基准使用相同资金流：首日投入
50 万元，之后每月投入 1 万元并买入持有 QQQ。

回测默认使用偏高的交易成本假设：单边佣金 0.10%、单边滑点 0.20%、
卖出附加费 0.10%，暂不计算资本利得税。策略与 QQQ 基准的期末资产均
按立即清仓并扣除交易成本后的净值计算。这是压力测试参数，不代表特定
券商的实际收费。

## 本地模拟仪表盘

使用已有 CSV 构建并启动本地页面：

```powershell
python local_app.py --serve
```

浏览器打开 `http://127.0.0.1:8000`。

先获取最新行情再启动：

```powershell
python local_app.py --refresh --serve
```

也可以只生成 `public/index.html` 和资金曲线，不启动服务：

```powershell
python local_app.py
```

API Key 可以保存在不会提交到 GitHub 的 `api.txt` 中，也可以设置环境
变量 `QUANTDASH_API_KEY`。

## GitHub Actions 自动部署

仓库包含 `.github/workflows/deploy-pages.yml`，它会：

- 推送到 `main` 时使用仓库内的 CSV 构建并发布页面；
- 每个美股交易日美东时间 18:30 更新行情并发布；
- 支持在 Actions 页面手动刷新和发布；
- 通过 GitHub Pages 提供静态模拟仪表盘。

首次部署：

1. 在 GitHub 创建仓库，并把本项目推送到 `main` 分支。
2. 打开仓库的 `Settings → Secrets and variables → Actions`。
3. 新建名为 `QUANTDASH_API_KEY` 的 Repository secret。
4. 打开 `Settings → Pages`，将 Source 设为 `GitHub Actions`。
5. 打开 `Actions`，选择 `Update simulation and deploy Pages`，点击
   `Run workflow`。

`api.txt`、本地生成的 `public` 目录和图片均已加入 `.gitignore`，不会
上传。GitHub Actions 会在运行时重新生成 Pages 文件。
