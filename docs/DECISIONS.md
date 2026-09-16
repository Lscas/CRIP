# 决策台账
**规格版本：** 0.2.0

优先顺序：用户本轮明确决定 > 本台账及需求 JSON > 产品说明 > 技术建议。新增决策必须同步更新需求和测试；用户没有决定的数字不能写成性能承诺。

| ID | 决策 | 状态／来源 |
|---|---|---|
| D-01 | 交付可运行网页原型：上传、分析、查看、审核、导出；不是仅CLI或训练权重 | 用户选A |
| D-02 | 商业新建，全项目全专业，工程师为主要用户，先预施工和投标 | 已确认 |
| D-03 | 永久材料详细到文件指定的厂家／产品／型号；临时材料只列名称 | 已确认 |
| D-04 | 多个允许选项全部保留，标明其中之一；不猜最终选中项 | 用户第2项 |
| D-05 | 不分析谁供货；不因责任归属排除项目需要的项 | 用户第3项 |
| D-06 | 设计净量，不做价格，不加损耗或采购取整；人工核验 | 用户第4项 |
| D-07 | 同内容采用内部Revision日期最新值；不按上传时间，保留新旧差异 | 最后澄清第3项 |
| D-08 | 只有阻断当前判断的信息缺失才报警，正常未选型不自动报缺陷 | 用户第6项同意 |
| D-09 | 当前运行逐项审核；跨运行人工修改继承暂不做 | 用户第7项 |
| D-10 | 单项目累计直接处理费用暂定300人民币；24小时目标；并行项目1 | 最后预算澄清 |
| D-11 | 可用外部API，无额外隐私／地区限制；不免除密钥、权限与防误删 | 用户第9项 |
| D-12 | 正式历史项目评测、准确率承诺暂不考虑；基本程序校验保留 | 用户第10项 |
| D-13 | 改范围／规则／预算／验收先获负责人批准；实现记录可同步更新 | 用户第11项 |
| D-14 | DeepSeek V4 Flash暂为首选，用户提供API，供应商可替换 | 最后澄清 |
| D-15 | 简单任务用低成本模型；能不用模型就不用；减少重复上下文和输出 | 本轮“开始生成” |
| D-16 | 输入PDF、DOCX、TXT、图片、DWG；约10GB设计目标 | 已确认，不擅自重加Excel和邮件 |

## 工程初值，不是用户验收数字
文本联合抽取采用32k UTF-8输入字节工程包络/8k输出，页面视觉仍为2k输出、字段核验仍为1.4k输出；关系任务12k/4k；付费文本与视觉请求均保持串行；一次局部修复；价格按高峰未命中预留；剩余20%提示。配置可以通过PR调整，但不得突破用户选择的项目上限和产品范围。

## 本轮执行授权边界
已获授权生成修订规格、契约参考代码、离线测试和Git模板。未提供可用密钥，未调用付费服务；未操作用户Git仓库，未部署或宣称应用已完成。产品原型是下一开发阶段的交付物。

## D-17：开始搭建及Codex上下文经济性
用户“在codex中生成也尽可能节省token。开始搭建项目”授权执行既定原型的开发。可采用可逆本地适配器交付运行骨架；不授权付费调用、扩大预算或改变业务验收。开发采用短入口、局部任务卡、脚本生成与定向测试；不声称已打开用户Codex或调用不存在的子模型。

## D-19：免费云端测试，本机实际运行
用户明确“使用render或者cloudfgalre的免费额度，实际运营暂时放在本机”。本轮选择Render测试部署适配，不新增Oracle或收费主机要求。免费计算不保证账户所有费用为零，真实创建前核查工作区/配额。云端仅mock、样例测试、易失数据与保护性100MiB测试限额，本机容量和真实API路径不变。正式云端发布需要账户和源码仓库授权；不创建未授权公开代码仓库。

## D-20：所有应用服务可运行本机
用户“继续部署，所有服务可以运行在本机”授权本机部署入口及验证，不再将远端GitHub/Render作为先决条件。前后端/任务/SQLite/文件本机持久化，外部模型API策略不变。不自动开启付费或公开正式资料；保留受保护远程模拟选项。未操作用户实际电脑或账户，不能宣称已安装到Windows。

## D-21：中英文显示切换，业务不变
用户明确授权网页支持中文/英文选项，并要求不改变任何已有功能。批准仅增加显示词典、浏览器偏好与语言入口；不翻译原始业务内容、不改导出契约、不增加LLM调用。

## D-22 原文引用与核验
用户在提出“交叉核对所有输出、每项可引用原句、先澄清”的需求后回复“更新”，授权实施上轮建议。执行成本优先默认A；不扩大数据外传范围，不启用更贵模型，不改变300CNY预算、日期优先、人工审核和中英显示规则。不明确要求打包原件，因此导出只携带引用内容和原文件定位。

## D-23 本机加密保存API密钥
2026-09-12用户明确要求“保存密钥，避免反复输入”。本机DeepSeek/Gemini live入口因此允许把密钥保存为Windows DPAPI当前用户加密文件，并在后续启动时自动读取；不写明文`.env`、网址、命令行、日志、Git或源码包。`--replace-key`强制重新输入，`--forget-key`删除本机加密密钥。更换Windows用户或电脑后需重新输入。

## D-24 英文简洁导出
2026-09-13用户明确要求所有导出自有文字为英文，材料或设备名称与数量分列且不显示ID，清理空白和格式代码，冲突双方原文与来源并排，证据放在对应项目内，并移除看不出材料净量作用的PDF几何审计表。源文件证据不翻译，只规范显示空白；没有明确数量时保持空白，不猜测为零。此决定更新D-21和D-22中“导出契约不变”的旧边界，但不改变网页语言切换、保存数据、人工审核、预算或模型调用。

## D-25 English-only user and developer presentation
On 2026-09-13 the user directed CIRP to assume that all users and developers use English. English is now the only selectable application language; legacy saved Chinese preferences cannot reactivate Chinese UI. The web app, local credential page, launcher/dependency diagnostics, public settings and cost descriptions, and reviewer exports use English CIRP-owned text. Original source-document evidence remains faithful to its source. This supersedes D-21 for current behavior and updates D-24's former boundary around the web language switch without changing saved data, review history, provider calls, or budget state.

## D-26 Focused material and QA reviewer scope
On 2026-09-13 the user required material and equipment names to identify tangible items rather than dimensions, gauges, headings, generic keywords, or descriptive clauses. Stated size, material, rating, specification section, and quantity remain separate properties. Tests and inspections must be executable QA activities, with specification section and stated performer shown when available; shop drawings, wiring diagrams, schedules, ordinary submittals, and unrelated administrative reports are excluded. Conflict and Missing Information are removed from current assembly, reviewer UI, and readable exports. Legacy stored records and source evidence remain untouched for audit and recovery.

## D-27 Bounded local acceleration and user-selected project budget
On 2026-09-15 the user approved stage timing, two local parser/OCR workers, selective vision pages, exact-evidence-scope verification batching, an in-analysis percentage and estimated finish time, and unlocking the fixed budget so each project can select its own range. CIRP therefore offers 1, 2, or 4 local document workers (default 2), but paid model requests remain serial and ledger-protected. The UI uses a stage-weighted elapsed-time estimate and says Estimating until enough progress exists; it is not a completion guarantee. New projects default to CNY 300; a local user may set CNY 0.01 through CNY 1,000,000. A new limit cannot be below settled plus outstanding cost, every change is audit-recorded, and a valid change may clear a budget freeze without resetting spent, reserved, unknown, or unresolved charges. This supersedes D-10 only for the fixed numeric cap; the cumulative ledger and pre-dispatch gate remain mandatory.

## D-28 Measured single-PDF concurrency, adjacent extraction batches, and deterministic quality
On 2026-09-15 the user selected four P0 updates and invoked ponytail minimalism: establish a measured baseline, merge adjacent extraction fragments, make 2/4 workers useful for one large PDF, and add deterministic quality checks. CIRP reuses the existing parser subprocesses, SQLite ledger, extraction schema, and assembly filters; it adds no queue or dependency. One PDF is processed in bounded four-page process chunks and merged in page order. At most four physically adjacent fragments and 8.8 KB of source text share one serial paid extraction task with a stable batch recovery family; legacy single-evidence recovery remains single. Clear descriptor/generic material names, non-executable QA items, duplicate properties, entity-name properties, and property evidence outside the requirement scope are removed or downgraded before publication. The reproducible fixture benchmark is evidence for local timing and request count only, not customer-file throughput, live-model quality, construction accuracy, or billing savings.
