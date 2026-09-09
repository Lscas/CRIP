# Render免费测试；本机实际运行
**规格版本：** 0.2.3

## 状态
本轮提供可执行启动入口、render.yaml和离线测试。Render连接尚未授权，未创建云服务、未产生公网地址。不得把本文件或配置检查当作真实上线证明。

## 两个环境
- Render：网页和Python后端都在免费实例上，电脑关机也不影响网页被再次访问。仅模拟分析，不接真实API；休眠、重启、重新部署会丢失上传文件、SQLite和审核记录。输出及时导出；每天可能需要重新上传样例。
- 本机：继续用`python -m app`，持久化`.local`、项目10GB设计目标、DeepSeek配置和300CNY项目预算均不变。不向云端复制或同步正式数据和密钥。
- 原Cloudflare Pages/Tunnel方案保留：用于远程访问正在运行的本机后端；电脑关机后后端不可用。首选Render独立测试无需同时叠加Cloudflare。

## 发布入口
1. 使用本轮Git bundle克隆`feature/render-free-preview`分支。将该分支推送到用户自己的私有Git仓库；不上传`.env`、`.local`、密钥或正式资料。当前连接未找到CIRP远端仓库，本包不自动创建或公开代码。
2. Render连接获得授权后，新建Blueprint，选择该仓库和分支，使用根目录`render.yaml`。检查将创建的服务名称不与已有服务相同；若冲突，改成本次测试专用名称。必须确认只有一个Web Service且`plan=free`，不要添加磁盘、Postgres、Worker或付费套餐。
3. 初始发布完成后，在该服务Environment查看Render生成的`CIRP_PREVIEW_PASSWORD`，网页账号固定为`engineer`。不要把密码写进仓库、聊天、查询字符串或截图。
4. 只使用Render实际返回的服务网址。验收`GET /_health`返回200且只含status；首页及`/api/settings`未登录401，登录200。验证Host、同源写保护、上传、审核和导出。
5. 首页下载两个合成示例，创建项目后上传并分析，预期5条候选和PARTIAL。真实文件仅按现有解析能力处理，mock不会伪造材料。

如果使用Dashboard普通Web Service而非Blueprint：Runtime Python；Plan Free；Build为`python -m pip install -r requirements-app.txt`；Start为`python -m app.render_preview`；Health为`/_health`；Python 3.13.5；在平台设置至少24字符的随机`CIRP_PREVIEW_PASSWORD`。`RENDER_EXTERNAL_HOSTNAME`和PORT由Render注入。Render宿主未实测；若Python版本不可用或依赖安装失败应停下来排错，不升级收费套餐。

## 免费边界
截至2026-09-08核实，免费服务无请求15分钟后休眠、下一次访问时冷启动；本地文件每次休眠/重启/部署会丢失；免费实例小时由workspace合计共享。没有用外部轮询保活，不规避平台限制。
**Free计算实例不等于账户绝对零账单。** 带宽与构建分钟可能另计。严格零费用应使用没有付款方式且无付费资源的测试workspace；若账户已有付款方式，需要先检查平台用量和计费控制，本程序不能替Render设置全账户硬费用上限。发现创建步骤要求付费即停止。自动部署关闭以避免每次push构建。

## 测试环境保护
- Render入口直接构造隔离Settings，不调用dotenv；不继承本地数据目录、模型Key或origin token，强制mock。仍使用24字符以上密码、精确Host与CSRF检查。
- 云端每项目100MiB上传上限、4MiB分片，用于保护免费测试实例，不改变本机10GB产品目标。页面明确标注限额和数据易失性。此限制不构成512MB内存或任意PDF处理成功的保证；当前PDF解析仍有资源限制和PARTIAL。
- `/_health`是唯一无需密码的只读存活端点，只接受精确路径GET/HEAD，不返回文件、配置或模型信息。其他页面和API继续鉴权。
- 一个worker，临时数据存`/tmp/cirp-render-preview`。不部署付费数据库、持久化磁盘、cron，不启用常驻保活。
- 本轮没有支持真实DeepSeek云端测试：易失账本重启后会丢失累计费用，不能直接把live开关打开。真实API分析仍在本机进行。

## 本地检查和回滚
```bash
python -m pytest tests/app/test_render_preview.py
python scripts/check_spec_sync.py
python -m pytest
```
代码与平台分开回滚。代码基线`v0.2.2-cloudflare-preview`（以bundle真实tag为准）。回滚云端前确认不会改回无鉴权本地入口；需要停用时只暂停/删除本次创建的测试服务，不操作本机数据、现有Cloudflare站点或其他Render服务。

## 官方参考（2026-09-08核实）
- https://render.com/docs/free
- https://render.com/docs/blueprint-spec
- https://render.com/docs/deploy-fastapi
- https://render.com/docs/environment-variables
- https://render.com/docs/python-version
