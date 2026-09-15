# 实际实现边界
版本0.2.6。本表说明工程进度，不改变已批准最终目标。

| 模块 | 当前实际行为 | 未完成 |
|---|---|---|
| 上传 | 网页文件/文件夹、4MiB顺序分片、断点校验、原文件SHA-256和别名去重 | 10GB压力测试、断点垃圾回收、云端对象存储、病毒扫描 |
| 项目/任务 | SQLite WAL、事务、单运行锁、输入快照、暂停/恢复/取消/中断状态 | 多租户/生产任务队列、进程级资源隔离 |
| 解析 | TXT行号；DOCX正文/简单表格；PDF文字词坐标；RapidOCR/ONNX本机OCR及bbox/confidence；长文本按字符和UTF-8字节有界拆分；PDF 300秒上限与页面进度保留 | 批注修订完整解释、跨页/脚注及图形语义关系；单个超慢页面仍可能无进度可保留 |
| 图片/DWG | 常见图片本机OCR并建立视觉任务；DXF对象元数据；GNU LibreDWG本机DWG→DXF后按对象解析，转换和对象解析均成功时标记`OBJECT_METADATA`，否则`UNAVAILABLE`；原文件不改 | DWG Xref/自定义对象/字体完整恢复、复杂Layout；真实用户DWG仍需样本验收 |
| 模型 | mock端到端；DeepSeek文本非思考、DeepSeek V4 Flash Vision受限整页页面路由及Gemini文本minimal适配；客户可在本机页面配置OpenAI-compatible远端文本API或回环本地模型，端点与模型绑定运行身份；指定59页图纸与833页规格书的合并运行完成2/2文件、222/222视觉页和全部核验，JSON、缓存、usage和共同¥300闸门均实测 | 通用入口的供应商专用参数、视觉、自动模型发现和非回环LAN；施工准确率、局部高分辨率裁剪和L2升级 |
| 材料/QA | 原子文本要求到受控候选；同明确Tag属性比较；字段证据 | 完整产品选项、实体多视图归并、结构化数量映射和复杂条件 |
| 冲突 | 同Tag/属性/单位的不同值；按可用内部日期采用新值并保留差异 | 单位等值、广义跨专业冲突和设计状态解释 |
| 缺失 | 未解析/未分析/关联上下文缺失明确列出 | 自动判断所有设计缺失的完整性 |
| 审核/导出 | 来源原文/定位及页面图；接受/编辑/拒绝、CAS冲突检测、历史；固定英文JSON/XLSX工程审核视图，名称/数量/单位分列、证据随项、冲突双方原文来源并排；已保存CAD量算按需显示 | 量算候选的专用接受/驳回事件、全套专业交互、跨Run编辑继承（已暂缓） |
| 成本 | 原子预留、核销、未知费用保留、人工账单核对入口与不可变审计事件、预算超额冻结、不自动重试；当前合并DeepSeek运行8,501次调用全部结算¥107.047278且预留/未知/未决为0，原Gemini项目的¥0.237238未决预留保持不变；用户选择后由Windows DPAPI为当前用户加密保存live密钥并自动加载 | 自动读取供应商账单、外部OCR/CAD费用适配；DPAPI密文不能跨Windows用户或电脑迁移 |
| Codex | 小AGENTS、分目录规则、任务包、定向测试、有界摘要 | 不控制账户实际模型价格/总token，无实际子模型委派 |

默认演示模式只分析明确的DEMO标记；普通真实资料不会伪造材料清单。真实API需用户自行配置密钥、确认价格和开启开关，所有输出仍需审核。当前结果全部标为PARTIAL，不能声称全项目已经完整审查。

Render免费测试适配：代码/Blueprint/隔离设置/健康检查已实现；真实服务创建、平台构建、外网HTTPS验收未执行。没有可用公网网址。本机路径与业务能力不变。详见RENDER_VALIDATION.md。

## v0.2.4 本机部署
FR-LOCAL-001新增启动和隔离配置：可运行Linux HTTP验证，Windows提供CMD入口但未实机验证；不提升施工识图/提取能力，不改变mock边界。详见LOCAL_VALIDATION.md。

## v0.2.5：只增加显示语言
This section is historical. D-25 supersedes the bilingual runtime behavior with English-only application presentation.
FR-UI-002已实现：中英菜单/按钮/已知系统提示/状态标签、无刷新切换、浏览器偏好、弹窗与编辑器输入保留。后端业务代码、模型Prompt正文、预算与供应商配置、数据库迁移、导出字段和数据均未修改。视觉/OCR/DWG/完整Takeoff等原有限制不变。

## v0.2.6：字段原句与独立核验
新增字段引用、精确原文切片、PDF词坐标映射及受限原图预览；低成本非思考语义复核接口与持久化任务；人工编辑后失效与局部复用；JSON/XLSX原句表。Gemini live验证覆盖基础连接、记账和1份真实PDF的两次局部运行；随后DeepSeek V4 Flash隔离项目完整处理144/144文字片段并完成字段核验，243次调用全部结算¥3.109899、未决0。独立视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59页，生成220条证据片段（其中59条来自视觉；整体50个`EXTRACTED`、170个`NEEDS_REVIEW`）、379条待审核记录和599次已结算调用（¥8.047433，预留/未知/未决0）；逐字段核验为31条`SUPPORTED`、9条`PARTIAL`、13条`UNSUPPORTED`、52条`PENDING`和274条`NON_DOCUMENT`。仍未评估施工准确性。此版本之后已接入本机OCR、整页视觉任务、DWG/DXF对象元数据和PDF几何审计；完整选项映射、跨专业关系、局部图纸裁剪、自动材料净量及施工准确率仍未完成。旧数据无词图时只能片段级图面定位；新解析才能产生词级坐标。

当前合并终验`RUN-7a2f9a8c090d40bdb802fdde17747d56`已处理指定59页图纸与833页规格书，产生1,964条证据和10,926条待人工审核记录（3,224材料、1,785检查、93冲突、5,824缺失/非文档说明），完成10,926份逐记录核验。8,501次调用全部结算¥107.047278，预留/未知/未决均为0；完整JSON/XLSX已通过结构、引用、内部跳转及公式注入安全验收。PDF几何审计221页但仅10页有比例标定，未形成材料净量候选，继续保持`PARTIAL`边界。

The fixed-English reviewer export regenerated a 30,795,937-byte JSON file and a 2,555,273-byte XLSX file from the same saved run. The workbook has five core sheets containing 3,224 material/equipment items, 1,785 inspection items, 93 conflicts, and 5,824 missing-information items. Exact duplicate conflict statements are removed before expansion; all 93 conflicts remain complete and produce 218 distinct side-by-side comparison rows with zero identical A/B rows. English numeric, parenthesized, minimum, and single-item prefixes are separated from quantity columns. Verified PDF private-use font glyphs are mapped to readable degree and micro symbols. Chinese display text, non-English punctuation, internal IDs, machine-style underscore codes, raw `null`, unsupported private-use glyphs, duplicated quantities, generic visual placeholders, raw JSON/formatting code, formulas, hyperlinks, and whitespace-only strings all validate at zero. Each of the five sheets was rendered and inspected. Excel shows up to three 320-character evidence excerpts per item; JSON and the app retain complete evidence. PDF paper-space geometry is absent from the output, and no Quantity Takeoffs sheet appears because this saved run has no CAD takeoff candidate. The legacy English compatibility cache was generated locally with Qwen and is guarded against number loss, generic disclaimers, translation collisions, and missing translations; no DeepSeek call was made. Saved evidence, human reviews, and budget state were not modified. The application, key page, and local launcher now default to English-only user/developer presentation.

无输出热修复（当时）：本机PDF解析上限由120秒调整为300秒，解析子进程周期原子保存已完成页面；超时Run保留已有片段并明确列出剩余未处理范围。只有缺失/未处理项时，网页首次自动显示该类别。当时未增加OCR，也未改变mock或付费API边界；后续OCR与整页视觉增量以上表和当前说明为准。

Gemini live适配：用户明确选择Google Gemini 3.6 Flash时，Key只允许发往Google官方OpenAI兼容基址；3.6不能关闭thinking，因此固定最低`reasoning_effort=minimal`并保守计入隐藏输出token。启动前要求确认人民币预算预留上界；2026-09-12用户明确授权后，可选择Windows DPAPI当前用户加密保存并自动加载Key，不写明文`.env`、网址、命令行、日志或Git。2026-09-11合成文本完成1次真实调用：839输入token、35输出token、账本支出¥0.007605、未知调用0。随后59页真实PDF首次运行在6/144后进入`PAUSED_PROVIDER`；用户授权重试在7/144后再次因`HTTPStatusError`暂停。PDF项目累计12条调用记录、已结算¥0.526795、2条未决并保留¥0.237238；程序没有自动重试。结果只证明账户连通、真实PDF上传/文本解析、局部响应结算和失败关闭保护，不证明施工准确率或完整大文件吞吐。

DeepSeek V4 Flash切换：用户指定live复测使用`deepseek-v4-flash`。本机127.0.0.1网页入口固定DeepSeek官方基址及非思考JSON模式；用户选择后由Windows DPAPI为当前用户加密保存并在后续自动加载Key，`--replace-key`更换、`--forget-key`删除。按2026-09-11官方峰值美元价和10 CNY/USD安全倍数预留输入¥4.40/百万、输出¥13.20/百万，并设置1秒请求开始间隔。文字运行在独立项目完成144/144文字片段：生成60条材料、16条检查、1条冲突和213条缺失/未处理记录，所有290条均待人工审核。随后绘图视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59页并产生379条待审核记录（79材料、26检查、274缺失）；视觉与文字运行均验证连接、结构化输出、字段核验和本机记账，不证明施工准确率或自动材料净量。

网页轮询稳定性修复：真实DeepSeek运行生成290条记录后，旧网页每2.5秒重叠加载完整记录集，导致CPU争用和内存增长。现改为刷新串行、活跃运行不请求完整记录、终态只加载一次；审核或手动核验后仍强制刷新。新增有界摘要分页接口后，10,926条合并结果按类别和偏移读取；该修复不改变模型响应、账本、审核或导出内容。

HTTP诊断与恢复修复：提取和语义核验使用同一安全错误入口。非成功响应按400参数、401鉴权、403权限/账户前置条件、404模型/接口、408超时、429配额/限流及5xx暂时故障分类；只保存状态、短机器码、格式受限的`Retry-After`与请求ID，不保存供应商错误正文或请求内容。网络超时、传输失败和成功状态下的非法JSON也分别记录。所有此类请求仍保留费用预留并暂停，不自动重试。网页现要求用户确认已查供应商账单后逐条登记未收费或实际人民币费用，并保留审计事件；项目未决调用清零前，开始、创建、处理、恢复和付费请求出口全部关闭。恢复只处理未完成片段；同一Tag聚合身份稳定，新证据改变候选时保留审核历史并重新待审。Gemini本机环境默认15秒请求开始间隔；服务关停可打断节流等待并禁止随后预留/发送。仍不自动读取Google账单，也不改变300CNY预算。

大规格书发布与核验修复：候选审核摘要在发布前限制为Schema允许的400字符，完整属性仍保留在证据抽取中；只有本地发布阶段失败且不存在待抽取证据的运行可原地恢复，已经完成的付费任务不重发。逐字段核验按记录引用ID定向加载证据，分析发布时不再重复预生成核验。模型只允许安全去除回显的`claim`、`label`、`evidence_ids`、`context`或压缩过长理由；凡经修复均降为`NEEDS_CONTEXT`，未知字段、错误路径、越界或不精确引用仍以`MODEL_OUTPUT_REJECTED`失败关闭。
