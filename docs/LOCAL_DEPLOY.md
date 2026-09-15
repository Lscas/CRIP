# 本机部署与每日测试
**规格版本：** 0.2.4

## 部署结构
网页、Python API、单进程后台任务、上传文件和 SQLite 均在同一电脑。无 Docker、Node、Redis、云数据库、GitHub 或 Render 前置条件。生成模型仍遵循 API-first 方案；“应用服务本机运行”不意味着下载本地大模型。

本轮在会话 Linux 工作区实现并验证启动链；无法直接控制用户 Windows 电脑，也不提供一个声称长期有效的工作区公网地址。

## Windows 最短路径
1. 将完整源码 ZIP 解压到一个固定目录，例如 `C:\CIRP`。先确认该目录包含 `app`、`scripts` 和 `start-local.cmd`；不要只复制启动脚本。
2. 安装 Python 3.11 或更高版本。双击 `start-local.cmd`。
3. 首次启动在项目内创建 `.venv`，从软件包源安装依赖。成功后自动打开 `http://127.0.0.1:8000`。后续依赖未变化且检查通过时不再联网安装。
4. 保留该终端窗口。关闭窗口、按 Ctrl+C、电脑休眠或关机会停止服务；再次运行同一个入口可读取原数据。

不会修改 PowerShell 执行策略，不要求管理员权限，不修改路由器或系统防火墙，不终止占用端口的其他程序。端口占用时在 PowerShell 运行：

```powershell
.\start-local.cmd --port 8002
```

## macOS / Linux
```bash
./start-local.sh
# 不自动打开浏览器
./start-local.sh --no-browser
```

## 模拟模式与真实 API
默认模拟模式不读取 `.env`，不继承环境中的 API 密钥和付费开关。它只识别 `examples/demo` 的合成标记。真实文件可以按已有能力解析，但不会伪造材料结果。

演示步骤：新建项目 → 上传 `examples/demo/01_original.txt` 和 `02_revision.txt` → 分析 → 两条材料、两条检查/报告、一条版本差异 → 查看来源 → 审核 → JSON/XLSX 导出。运行状态 PARTIAL 是能力边界，不是错误地声明全项目已完成。

使用真实模型时，在本机按 `docs/PROVIDER_INTEGRATION.md` 设置 `.env` 的API地址、密钥、实际单价、`CIRP_PRICES_CONFIRMED` 和 `CIRP_LIVE_API_ENABLED`，然后执行：

```powershell
.\start-local.cmd --live
```

`--live` 只开启本机入口。配置不完整则拒绝启动，不打印密钥；启动本身不向模型发送请求，点击分析后才可能发生费用。DeepSeek使用非思考，Gemini 3.6 Flash使用最低minimal推理；短输出和每项目累计300CNY预算不变。当前选择DeepSeek V4 Flash：Windows优先运行`start-deepseek-live.ps1`；首次运行由仅限127.0.0.1的页面输入并确认预算。选择保存后，Key只写入`.local/credentials/`下的Windows DPAPI当前用户密文，后续自动启动；不写明文`.env`、网址、命令行或日志。运行`scripts/deepseek_local_setup.py --replace-key`可更换，`--forget-key`可删除。Gemini对应入口仍为`start-gemini-live.ps1`和`scripts/gemini_local_setup.py`。

## 数据和更新
默认数据在当前工程 `.local/`：原文件、SQLite数据库、审核、预算、任务进度。不要靠删除SQLite重置费用。升级前正常停止服务，备份整个数据目录，不只复制数据库主文件；解压新代码不能覆盖或删除 `.local` 和用户自己的 `.env`。

显式指定稳定数据目录：
```powershell
.\start-local.cmd --data-dir "D:\CIRP-data"
```
默认模拟模式使用项目 `.local`；不读取 `.env` 中的自定义数据路径。原来使用自定义路径时应传 `--data-dir`；`--live` 则沿用已配置的路径。

## 在办公室或手机远程测试
使用已有的 Cloudflare Quick Tunnel 入口，不需要代码先推Git，不创建Render服务或Pages项目。网页和后端仍在本机；Cloudflare只中转HTTPS流量。

先在本机安装官方 `cloudflared`：
```powershell
winget install --id Cloudflare.cloudflared -e
```
重新打开终端或设置 `CIRP_CLOUDFLARED` 为真实可执行路径，然后双击 `start-online-test.cmd`。或：
```powershell
.\start-local.cmd --remote
```
这使用独立8001端口、`.local/preview-data`和随机访问密码。只有公网401/200验证成功后才显示实际网址。此入口保持**仅模拟测试**，不能与`--live`结合，不会公开正式资料目录。临时网址可能随重启变化；固定域名/长期隧道不在本轮部署中。

本机必须联网且不休眠。`127.0.0.1`只代表正在访问它的电脑，不能把它当成手机的远程网址。不要直接将无鉴权本机端口转发到公网。

## 诊断
```powershell
# 不安装、不启动、不读密钥
py -3 scripts/local_deploy.py --check
# 只做首次隔离安装
py -3 scripts/local_deploy.py --install-only
```
依赖安装失败不会继续启动；日志保存在 `.local/launcher/install.log`。只检查相关失败，不需要反复把整个仓库送给Codex。

开发/测试环境已经装好兼容依赖时可用 `--use-current-env`，此选项不会安装任何包。本轮HTTP验收使用此路径；独立首次安装未通过，不等于应用逻辑测试失败。

## 官方参考
核查日期：2026-09-09。
- Cloudflare Quick Tunnels：https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
