# GitHub 每小时节点测速

每小时自动拉取订阅，逐个节点测下载速度，在 GitHub Actions 显示排名，保留 30 天的 CSV / JSON 测速文件。也可以手动触发。

使用 [Cloudflare 测速网站](https://speed.cloudflare.com/) 的官方项目所用下载端点。默认每个节点最多下载 10 MiB、请求最多 12 秒。Mihomo 原生解析订阅，支持其兼容的 Clash YAML、节点链接列表和 Base64 订阅，包括 VLESS、VMess、Hysteria2 等。

## 三步启用

1. 建议新建一个 GitHub **私有仓库**。把本目录内的文件放到仓库根目录，保留 `.github/workflows/speedtest.yml` 的完整路径。不要把外层 `github-proxy-speedtest` 文件夹也套进仓库。请确认隐藏的 `.github` 文件夹也已上传。
2. 打开仓库 **Settings → Secrets and variables → Actions → New repository secret**。Name 填 `SUBSCRIPTION_URL`，Secret 填你的原始订阅链接。兼容之前仓库里的 `CLASH_SOURCE_URL`，二者都有时优先使用 `SUBSCRIPTION_URL`。
3. 打开 **Actions → Hourly Proxy Speed Test → Run workflow**，先手动跑一次。之后在默认分支按 `17 * * * *` 自动运行，即每小时第 17 分钟。打开某次运行的 **Summary** 看表格，底部 **Artifacts** 下载该次完整结果。

如果你使用原来的仓库，其中已有每日运行的 `Update Clash Subscription` 工作流，这两份工作流会各自运行。只需要新测速任务时，可以在 Actions 中停用旧工作流，避免重复消耗流量。

### 订阅网站无法从 GitHub 访问时

部分订阅网站会拒绝 GitHub 机房访问。支持额外配置 `SUBSCRIPTION_BACKUP` Secret，存放加密保存的节点快照。任务仍优先下载最新订阅，仅在下载失败时使用备份；报告明确显示 `Subscription source: backup`、备份时间和刷新失败原因。备份不会自动更新，节点变化后需要更新该 Secret。

该 Secret 的内容是 JSON，包含 `captured_at`（ISO 8601 时间）和 `content_b64`（对原始订阅文件字节进行 Base64 编码）。它应通过 GitHub Secrets 保存，不要提交到公开仓库。GitHub 单个 Secret 有大小限制，只适合较小的订阅；也可以改用能访问订阅网站的 self-hosted runner。

## 结果怎么看

- `Mbps`：实际收到的字节数 × 8 ÷ 总请求秒数 ÷ 1,000,000，包含连接耗时，是短时单连接下载平均速度。`MiB/s` 是每秒二进制兆字节。
- `HTTP TTFB ms`：从请求开始到首字节的时间，包含代理连接、DNS、TLS 和服务器处理；不是 ICMP ping。
- `ok`：完整收到本次设定的数据量。
- `partial`：到达时间上限，但已收到至少 64 KiB。展示这段时间的平均速度，属于部分样本。
- `timeout`：超时且没有足够数据，速度为 `N/A`。
- `http_403`、`http_429` 等：测速端点返回拒绝或限流状态。不能仅凭此认定节点失效。
- `curl_7`：连接失败；`curl_35` / `curl_60`：TLS 相关错误；其他 `curl_数字` 可查 curl 错误码。
- `invalid_content` / `short_response`：返回内容或长度不符合预期，不计作测速成功。
- `skipped_limit` / `skipped_budget`：达到节点数量或整轮时间上限，未测该节点。

报告里的 `Direct endpoint check` 只是本次运行前直接请求 Cloudflare 1 KiB 的连通性对照。它不参与节点排名；所有节点测速都明确通过所选代理，不会因代理失败改为直连。

部分节点失败仍会保留其他节点结果。如果全部没有有效下载样本，任务标红，但仍上传结果。订阅下载或启动失败也会生成错误说明。

## 调整参数

在 **Settings → Secrets and variables → Actions → Variables** 中添加以下变量即可，无需改代码。

| 变量 | 默认值 | 含义 |
| --- | --- | --- |
| `DOWNLOAD_MIB` | `10` | 每个节点下载量，1 到 100 MiB |
| `TEST_TIMEOUT` | `12` | 单节点请求上限，2 到 60 秒 |
| `MAX_NODES` | `200` | 每轮最多测速节点数，1 到 500 |
| `RUN_BUDGET_SECONDS` | `1800` | 整轮节点测试时间预算，30 到 1800 秒 |
| `NODE_FILTER` | 空 | 仅测名称包含该文字的节点，忽略大小写；例如 `SG` |

每小时轮换测试起点。节点数量上限或时间预算都可能造成部分节点跳过，以报告为准。

假设每次都下载满 10 MiB，10 个节点一天约使用 **2.34 GiB**，100 个节点一天约 **23.44 GiB**，另有协议开销。节点多时建议用 `NODE_FILTER` 或调低 `DOWNLOAD_MIB`。

## 测速范围与运行限制

**GitHub 托管机器测的是 GitHub 机房 → 代理节点 → Cloudflare，不是你家宽带到节点的速度。** 适合观察从该机房访问节点的可用性和速度变化。需要衡量自己使用体验时，可以在自己电脑运行同一脚本，或把工作流改为合适的 self-hosted runner。

Cloudflare 比 Fast.com 更适合这里的定时小流量探测：请求大小明确，不必启动浏览器。此脚本只测下载及 HTTP 首字节时间，不复现 Cloudflare 网页的多轮完整算法，不测上传，也不代表 Netflix 解锁情况或 Fast.com 的速度。

GitHub 定时任务可能排队延迟或偶尔被丢弃，不保证精确每 60 分钟。工作流必须在默认分支。公开仓库长时间没有活动可能被自动停用定时任务。私有仓库还需留意账户的 Actions 分钟数及存储额度。

结果只出现在运行记录及 Artifacts 中，不会自动提交到仓库。报告包含节点名称、协议类型及测速数据，订阅地址、UUID、密码、生成的 Mihomo 配置不进入产物。请勿把账号或订阅链接写进节点名称；公开仓库的运行记录及报告可能被他人查看。

订阅应提供实际节点。如果 YAML 只有远程 `proxy-providers` 而没有 `proxies` 列表，需要改用上游原始订阅。节点是否可用仍取决于订阅参数和服务器状态；脚本不修复节点。

## 本地运行

需要 Python 3.11+、curl、Mihomo。Actions 已自动安装所需核心；Python 只使用标准库。

Windows 示例：

```powershell
python speedtest.py --mihomo 'D:\tools\mihomo.exe' --subscription-file 'D:\tools\subscription.txt'
```

Linux / macOS 示例：

```bash
python3 speedtest.py --mihomo /path/to/mihomo --subscription-file /path/to/subscription.txt
```

本地结果在 `results/summary.md`、`results/results.csv` 和 `results/results.json`。也支持通过环境变量 `SUBSCRIPTION_URL` 传入 HTTPS 订阅链接。不要将真实订阅文件加入仓库。

测试命令：

```bash
python -m unittest discover -s tests -v
```

## 实现依据

- [Cloudflare 官方测速项目默认配置](https://github.com/cloudflare/speedtest/blob/main/src/config/defaultConfig.ts)
- [Mihomo v1.19.30](https://github.com/MetaCubeX/mihomo/releases/tag/v1.19.30)，工作流固定版本并校验 SHA-256
- [GitHub Actions schedule 说明](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
