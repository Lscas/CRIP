# Cloudflare远程测试部署
**规格版本：** 0.2.2

## 当前交付状态
已提供受保护的Python源站、Cloudflare Pages Worker、受控资产构建、Windows/Linux启动脚本及离线测试。本轮执行环境没有Cloudflare账户授权或cloudflared，外网域名解析失败，因此没有创建真实Pages项目、没有取得公网URL、没有进行生产发布。Cloudflare平台命令/配额和Windows实机仍需在用户电脑验证。不存在可据此声称已上线的网址。

## 两种方式
1. **临时网页：Quick Tunnel。** 不需要Cloudflare账户；网页和Python API均运行在当前电脑，经临时HTTPS地址访问。
2. **Pages网页 + Tunnel源站。** Pages托管允许列表中的HTML/CSS/JS与鉴权代理；Python、SQLite、原文件、解析及后台任务仍在当前电脑。需要Cloudflare账户登录以及Node/npm。不是把整个Python应用复制进Pages，也不是24小时云托管。

两种方式都需要本机和终端持续运行，电脑睡眠/断网或停止脚本后远程功能不可用。Quick Tunnel地址会随重启变化；本脚本每次`--pages`新建一个独立测试项目，不覆盖已有站点、不修改域名、不升级付费套餐。测试项目仍受账户配额与既有计费规则约束。

## Windows最短步骤
需要Python 3.11+。解压完整项目，在包含`start-remote.ps1`的目录打开PowerShell。

先安装官方cloudflared：
```powershell
winget install --id Cloudflare.cloudflared -e
```
安装后重开终端。先进行无账号临时预览：
```powershell
.\start-remote.ps1
```
脚本会建立隔离Python环境、安装依赖、生成随机访问密码、启动Tunnel和后端，并做401/200公网探测。**只有探测通过才显示已验证地址。** 浏览器弹出验证时输入终端显示的账号`engineer`和临时密码。

需要正式Pages域名时，先安装Node.js/npm，完成本机Cloudflare登录：
```powershell
npx --yes wrangler@4 login
.\start-remote.ps1 -Pages
```
OAuth登录由你的浏览器完成，不需要把Cloudflare Token或模型Key交给聊天。多账户用户在本机指定`CLOUDFLARE_ACCOUNT_ID`，不要自动选错账户。
首次Pages发布为缺凭据的锁定部署。程序随后要求在新项目Settings > Runtime > Fail open / closed选择**Fail closed**，在终端输入CLOSED；这样Functions配额耗尽时不会跳过页面验证。若已在本机环境配置Cloudflare Pages编辑Token及Account ID，脚本会只针对本次新建项目通过API设置并验证fail_open=false，无需控制台手动设置。
随后通过stdin写入独立的Pages secrets并重新部署，只有公网接口验收通过才显示实际Pages地址和密码。

PowerShell若阻止运行本地脚本，不修改系统执行策略，可直接使用Python：
```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-app.txt
.\.venv\Scripts\python.exe scripts/remote_preview.py --pages
```
不需要Pages时删除最后的`--pages`。

## Linux/macOS
自行按Cloudflare官方说明安装cloudflared，再运行：
```bash
./start-remote.sh
# 已有Node/npm、Wrangler登录后：
./start-remote.sh --pages
```

## 安全与实际能力
- 随机访问密码与源站Token分离，至少24字符；秘密不出现在URL、源码包、静态网页或模型Prompt中。
- 全部API与页面受密码保护；Pages调用源站时移除用户Authorization/Cookie，注入独立源站Token。Worker不跟随源站重定向。
- 源站只监听127.0.0.1；严格Host名单；写请求检查Origin和X-CIRP-Client。不信任客户端伪造的转发头。
- Pages资产只包含6个明确文件，不上传.env、.git、Python代码、客户资料、SQLite或报告。
- 远程数据独立保存在`.local/preview-data`，不自动公开之前的`.local`工作区。
- 默认及本版远程入口只允许mock，即使本机.env启用了DeepSeek，也不会自动开始付费调用。原本的本地API接入方式不变。
- mock只识别`examples/demo`合成文件。其他真实资料只按已实现能力解析，未完成项显示PARTIAL；不是完整施工材料分析。
- 此版本仅面向持有密码的少量可信测试者，不是多用户生产认证，没有正式Malware/WAF/渗透测试或10GB压测。不要邀请不可信上传者。
- 单次上传仍是4MiB分片；10GB是项目设计容量，不是Pages静态资产上传大小。
- 页面轮询后台任务，不使用Quick Tunnel不支持的SSE。

## 停止、失败与数据
Ctrl+C结束本次Python与Tunnel进程。不要删除SQLite来重置预算。脚本不会删除项目数据或远端Pages项目。Pages页面可能仍能加载，但其后端停止后API返回源站离线，不能继续分析。
日志位于`.local/remote-preview/<运行编号>`。最终`verified-deployment.json`只在公网401/200及mock检查通过后生成，不含密码。没有该报告不代表已部署成功。
每次`--pages`都会创建新测试项目；不再使用时在Cloudflare控制台删除**对应测试项目**。项目名记录在该次日志目录的`pages-project.json`。不自动删除任何现有Cloudflare资源。
常见失败：cloudflared不在PATH；已有本机8000端口占用；网络禁止Tunnel连接；Cloudflare未登录/多账号未指定；Functions配额；已有cloudflared配置影响Quick Tunnel。脚本不修改既有Tunnel配置、不绕过网络策略。
若需要电脑关闭后也能测试，需另部署常驻Python源站及持久化存储，并将短期Tunnel替换为受管理Tunnel；本轮未创建或租用服务器。

## 验证与回滚
```bash
python scripts/run_checks.py --area remote
python scripts/run_checks.py --area all
python scripts/remote_preview.py --pages --check
```
检查工具存在不等于验证账户或公网。完整平台发布尚未在本轮验证。
代码回滚到Git标签`v0.2.1-bootstrap`；本次没有数据库迁移。公网回滚应先停止本次Tunnel，再在控制台移除对应测试部署，避免把无鉴权旧版对外发布。

## 官方资料（核实于2026-09-08）
- Cloudflare下载：https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/downloads/
- Quick Tunnel：https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/trycloudflare/
- Pages Direct Upload：https://developers.cloudflare.com/pages/get-started/direct-upload/
- Pages Advanced Mode：https://developers.cloudflare.com/pages/functions/advanced-mode/
- Wrangler Pages命令：https://developers.cloudflare.com/workers/wrangler/commands/pages/
- Functions Fail closed：https://developers.cloudflare.com/pages/functions/routing/
- Pages项目API：https://developers.cloudflare.com/api/resources/pages/
