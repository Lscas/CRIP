# v0.2.4 本机部署验证

## 结论
本机启动代码已实现并在会话 Linux/Python 3.13.5 的**现有依赖环境**验证。不是已在用户 Windows 电脑完成安装，也不是公网或常驻托管服务。

| 检查 | 实际结果 |
|---|---|
| Python 离线回归 | 235项通过，0失败/0错误/0跳过；其中本轮新增18项 |
| Cloudflare Worker 离线回归 | 11项通过；不是公网隧道验收 |
| 新启动器实际运行 | `--use-current-env --no-browser`启动成功，HTTP版本0.2.4、provider=mock，打印READY |
| 本机真实 HTTP | 首页/健康、项目创建、两文件上传、模拟分析、证据、审核、JSON/XLSX导出通过 |
| 模拟结果 | 2条材料、2条检查/报告、1条修订差异，状态PARTIAL |
| 重启数据保留 | 同一数据目录的项目、两份文件、5条结果和审核历史仍存在 |
| Host与跨站防护 | 非法Host=400、跨源修改=403 |
| 停止行为 | 启动器和子服务收到Ctrl+C后退出，未留下测试服务 |
| 真实模型/OCR/CAD调用 | 0次 |
| 云服务/远端仓库创建 | 0次 |

## 明确未通过或未验证
- 创建隔离`.venv`成功，但此环境软件包源未返回可安装的FastAPI版本，首次`pip install`失败；启动器正确停止，没有把安装失败说成成功。未验证联网干净安装或完整传递依赖锁。
- Windows CMD入口仅提供代码和命令构造测试，未在Windows实机运行。没有修改用户电脑的执行策略、防火墙、自启动、全局Python或Git设置。
- Playwright实际浏览器尝试因缺少Chromium可执行文件未完成；本轮没有浏览器截图/DOM验收。真实HTTP测试是独立验证，不冒充浏览器验收。
- 未执行Cloudflare Tunnel公网发布，没有实际公网URL。
- 没有真实API密钥/价格确认、施工语义准确性、10GB吞吐、24小时完成率、完整图纸/OCR/DWG/Takeoff验证。

## 复现
```bash
python scripts/check_spec_sync.py
python scripts/run_checks.py --area local
python scripts/run_checks.py --area all
python scripts/local_http_smoke.py
python scripts/local_deploy.py --use-current-env --check
```
最后一个`--use-current-env`仅适用于已具备兼容依赖的开发环境。用户正常使用`start-local.cmd`在项目`.venv`内安装，不修改全局Python。

记录：`reports/pytest-v0.2.4.xml`、`reports/worker-v0.2.4.txt`、`reports/local-http-v0.2.4.json`、`reports/local-launcher-v0.2.4.json`。这些均为合成数据和测试元数据。原项目文件、密钥、实际数据库和虚拟环境不进入源码ZIP或Git Bundle。
