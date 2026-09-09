# CIRP · 施工文件审查原型 v0.2.2
这是**可运行的本地开发原型**，不是仅有规格的压缩包，也不是完整施工分析产品。
上传 → 文件清单／基础文字解析 → 单次联合抽取 → 四类候选 → 查看来源 → 人工审核 → JSON/XLSX导出。

**默认零费用mock模式**仅识别 `examples/demo` 的明确合成标记。普通真实文件在mock模式下会解析、标记未分析，不生成虚假的演示材料。真实API适配器已经编写，但本轮没有密钥、没有付费调用、没有验证施工准确率。完整图纸、OCR、DWG、Takeoff等仍未接入，运行结果明确为PARTIAL。

## Cloudflare远程测试
本版提供受保护的部署脚本，但本轮没有取得公网网址，原因是执行环境无Cloudflare登录凭据且外网DNS失败。
Windows先安装cloudflared后运行 `./start-remote.ps1`；需要Pages时本机完成 `npx --yes wrangler@4 login`，运行 `./start-remote.ps1 -Pages`。详见[部署步骤与边界](docs/CLOUDFLARE_DEPLOY.md)和[本轮实际验证](docs/DEPLOYMENT_VALIDATION.md)。
脚本只在公网401/200验证后显示实际网址；随机密码在启动终端生成。默认mock；源站保留在本机，关闭电脑后不可继续远程分析。

## 1. 启动（Windows PowerShell）
需要Python 3.11+；本轮在Linux/Python 3.13.5执行测试，Windows尚未实机验证。
```powershell
cd cirp
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.\.venv\Scripts\python.exe -m app
```
浏览器打开 `http://127.0.0.1:8000`。不要求Node、GPU、PostgreSQL或外部账户。不要无保护暴露公网；远程测试使用新增的start-remote脚本。

macOS/Linux：
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-app.txt
.venv/bin/python -m app
```
依赖文件约束兼容范围，不是完整传递依赖锁；本轮已装版本见reports/environment.json。

## 2. 先运行无费用演示
新建项目；选择 `examples/demo/01_original.txt` 与 `02_revision.txt`；点击开始分析。
预期生成2条材料、2条检查/报告、1条版本差异。修订日期较新文件中的示例属性覆盖旧属性，旧来源保留。每项可查看原文/定位、接受/编辑/拒绝和导出。状态PARTIAL是有意展示未完成能力，不是项目已全部审查。
示例为合成协议，不是任何真实项目的设计要求。

## 3. 用户API接入
复制 `.env.example` 为 `.env`，只在自己电脑填写地址、密钥、实际输入/输出单价，设置provider=deepseek、LIVE开关及PRICES_CONFIRMED；具体变量见模板和docs/PROVIDER_INTEGRATION.md。Key不进Git，不贴聊天、不放网页。
当前请求显式关闭thinking，最大输出2000，强模型/视觉升级未接入；Schema验证、跨项目证据、截断、未知usage等会阻止发布或暂停。300CNY按同项目累计持久化预留/核销；24h只是目标及停止新增任务规则。价格须用户按实际接口确认。

## 4. 测试与Codex
```bash
python -m pip install -r requirements-dev.txt
python scripts/doctor.py
python scripts/run_checks.py --area all
python scripts/context_pack.py DEV-002
```
Codex先读AGENTS和DEV_STATE，再读取任务包；不要每轮加载整个项目。参见 [Codex启动说明](docs/CODEX_START.md)。本包未替用户启动Codex、设置账户模型或调用低价子代理。

## 5. 目录
`app/`服务、预算、解析和适配器；`web/`无需构建的中文网页；`migrations/`本地SQLite；`spec/`90项需求和12个契约；`prompts/`8个任务模板；`tasks/`小任务卡；`scripts/`检查与上下文工具；`tests/`离线测试。
所有运行数据默认写 `.local/`，包括原文、临时上传、SQLite费用账户；该目录不进Git。不要删除数据库来“重置预算”。本轮不会迁移你的旧项目数据。

## 6. 交付与未完成
- [实际实现边界](docs/IMPLEMENTATION_STATUS.md)
- [验证记录](docs/VALIDATION_REPORT.md)
- [产品规格](docs/PRODUCT_SPEC.md)
- [架构决定](docs/adr/0004-local-prototype-adapters.md)
- [下一阶段](docs/DEVELOPMENT_PLAN.md)

源码ZIP不含.git、密钥、用户数据、虚拟环境。可选Git bundle保留基线main、规格标签和feature/bootstrap-prototype分支，不含远端设置；使用方法见docs/GIT_GOVERNANCE.md。

## 可选离线界面检查
```bash
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
python scripts/browser_smoke.py
```
该测试用内存API桥接，不产生模型请求；不替代本机实际HTTP浏览器安装验收。
