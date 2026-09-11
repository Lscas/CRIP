# CIRP v0.2.6：原文引用与独立核验

新增逐字段原句、低成本语义核验、编辑失效复验及Excel/JSON证据导出。默认mock只定位原句，不伪造核验通过。真实API模式沿用原有用户配置、便宜模型和300CNY预算。

启动：解压完整项目后双击`start-local.cmd`。保留旧`.local/`和`.env`；先停止并备份旧服务数据再更新代码，新增表会幂等创建，不重置任何预算。

打开结果可查看“原文引用与独立核验”；旧记录点击“不调用模型”的引用检查后可生成报告。只有显式配置真实API或点击付费重验才发送模型请求。

- [功能与边界](docs/EVIDENCE_VERIFICATION.md)
- [本轮验证记录](docs/EVIDENCE_VALIDATION.md)
- 定向：`python scripts/run_checks.py --area evidence`
- 全套：`python scripts/run_checks.py --area all`

完整图纸/OCR/DWG/Takeoff和全项目无漏项仍未完成；本轮未接入实际模型或进行施工准确率评测。

## 以下为历史版本说明，当前交付以上文为准

# CIRP v0.2.5：中文 / English 界面
页头“界面语言 / Display language”选择中文或English，即时切换。新建项目和审核抽屉也有选择器；当前浏览器保存偏好。
**只改显示，不改分析、上传、审核、预算、导出或项目数据。** 原始文件、材料名称、型号、证据、编辑JSON和导出保持原文，不用LLM翻译。
启动仍为`start-local.cmd`（Windows）或`./start-local.sh`。关闭旧进程后替换程序文件，必须保留自己的`.local/`、`.env`和数据目录。无需数据库迁移。
[语言功能与边界](docs/UI_LANGUAGE.md) · [实际验证](docs/UI_LANGUAGE_VALIDATION.md)。未连接到用户电脑或远端仓库，本包不代表已在其电脑更新。

## 沿用的本机与远程部署能力
以下是原有说明，历史版本测试结果不作为本轮新增验证：

# CIRP v0.2.4 · 全部应用服务在本机运行
面向施工工程师的文件审查原型。上传 → 基础解析 → 材料、检查/测试/报告、冲突、缺失信息 → 来源 → 人工审核 → 导出。

**Windows：安装 Python 3.11+，解压后双击 `start-local.cmd`。** 网页地址 `http://127.0.0.1:8000`，服务窗口须保持运行。首次需要安装依赖；依赖未改变时后续启动跳过安装。无需 GitHub、Render、Docker、Node、GPU或云数据库。

**当前交付是可运行代码和会话Linux环境验证，不是已在你的Windows电脑启动的服务器。** 模拟示例流程已验证；真实API、复杂图纸、OCR、DWG和完整Takeoff未验证/未实现，不宣传已完成全项目分析。

## 入口
| 用途 | 命令 |
|---|---|
| 本机模拟测试 | 双击 `start-local.cmd` |
| 本机真实API，须先配置密钥/实际价格 | `start-local.cmd --live` |
| 手机/办公室远程模拟测试，须装cloudflared | 双击 `start-online-test.cmd` |
| macOS/Linux | `./start-local.sh` |
| 检查但不安装不启动 | `python scripts/local_deploy.py --check` |

模拟模式不读取 `.env`，不调用收费API。原`python -m app`入口保持兼容，仍可能加载用户已启用的API配置；需要零费用启动时使用本版新入口。

[本机启动与数据说明](docs/LOCAL_DEPLOY.md) · [本轮实际验证](docs/LOCAL_VALIDATION.md) · [API接入](docs/PROVIDER_INTEGRATION.md) · [产品规格](docs/PRODUCT_SPEC.md)

## 先验证示例
新建项目后上传 `examples/demo/01_original.txt`、`02_revision.txt`。预期为两条材料、两条检查/报告、一条修订差异；状态PARTIAL。可查看来源、接受/修改/拒绝、导出Excel/JSON。真实资料在模拟模式下不生成虚假的示例材料。

## 数据与费用
数据库、上传文件、审核记录和预算在本机 `.local/`，正常重启保留；请自己备份。模型推理仍通过你配置的外部API，并非本地大模型。廉价非思考任务、累计300CNY预算和人工核验边界保留，Codex开发费用另计。本轮真实模型调用为0。

## 继续开发
先读 `AGENTS.md` 与 `docs/DEV_STATE.md`，使用任务上下文包，不重复扫描整个仓库：
```bash
python scripts/context_pack.py DEV-002
python scripts/run_checks.py --area local
python scripts/run_checks.py --area all
```
本轮本地Git分支`feature/local-deployment`，无远端推送或云服务创建。现有Render/Cloudflare代码保留，但不作为本机运行前提。
