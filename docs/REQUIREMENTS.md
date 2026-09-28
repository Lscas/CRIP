# 完整需求表（自动生成）

规格版本：0.2.6

权威来源：`spec/requirements.json`。修改JSON后重新生成，不手改此表。所有产品业务功能仍未实现；superseded保留历史语义，不作为现行规则。

## PRD-USER-001 · 主要用户

优先级：P0；状态：planned

系统首要面向商业新建项目工程师。

- 项目创建和 UI 文案明确 Engineer 为主要角色。

## PRD-FLOW-001 · 自动项目级分析

优先级：P0；状态：planned

用户一次性批量上传资料后，系统无需用户逐项提问即可自动生成结果。

- 用户可通过一次 Analyze Project 操作启动完整流水线。

## PRD-SCOPE-001 · 全项目全专业分析

优先级：P0；状态：planned

所有专业均进入材料、QA/QC、冲突和缺失分析，不以能上传代替能分析。

- 未知专业保留并显示待分类。
- 每专业记录已分析、失败、未支持的范围。
- 原型限制不得伪装为已完成能力。

## FR-INGEST-001 · 支持输入格式

优先级：P0；状态：planned

首版支持 PDF、DOCX、TXT、常见图片和 DWG。

- 每种格式均有成功、部分成功、失败三态处理。

## FR-INGEST-002 · 10GB 输入目标

优先级：P0；状态：planned

单项目目标规模约10GB；文件量、页数和衍生数据另计，不承诺任意10GB在预算内完成。

- 可恢复上传、文件哈希与清单。
- 总原始字节数可查询；预算不足显示部分完成。

## FR-INGEST-003 · 文件 Manifest

优先级：P0；状态：planned

系统为每个文件保存哈希、类型、大小、上传状态和处理状态。

- 重复哈希可被识别；所有文件均出现在 Coverage Report。

## FR-INGEST-004 · 失败隔离

优先级：P0；状态：planned

单文件失败不得导致整个项目分析终止。

- 失败文件被列出，其他文件继续处理。
- A document that fails with a concurrent Windows PermissionError is retried exactly once after its document batch finishes; other failure classes are not retried automatically.

## FR-PARSE-001 · PDF 结构提取

优先级：P0；状态：planned

提取 PDF 文字、页码、Bounding Box、图片、表格和版面信息。

- Evidence 可定位到页和 bbox。
- Native PDF pages retain a deterministic page type, specification section or clause context, and detected table-row coordinates when available.
- A generic-name multi-page PDF carries the latest exact RFI/Submittal scope to continuation pages until another exact workflow heading replaces it.

## FR-PARSE-002 · OCR fallback

优先级：P0；状态：implemented

对扫描或低文本密度页面使用 OCR/vision fallback。

- OCR 结果保存方法和 confidence。

## FR-PARSE-003 · DOCX 结构

优先级：P0；状态：planned

保留 DOCX 标题、段落、列表、表格和图片关系。

- 输出 Canonical fragments 具有结构 metadata。
- A DOCX table containing consecutive exact RFI/Submittal sections splits role and status boundaries into separate evidence fragments while retaining native element and line locators.

## FR-PARSE-004 · TXT 解析

优先级：P1；状态：planned

TXT 保留原始内容、编码和行号。

- Evidence 可定位到行范围。

## FR-PARSE-005 · 图片理解

优先级：P0；状态：planned

常见图片支持 OCR、方向和局部视觉分析。

- 文本和 bbox 可用于 Evidence。

## FR-PARSE-006 · DWG 合法转换

优先级：P0；状态：implemented

DWG 通过受支持的本机开源或可配置CAD转换组件派生并标记解析等级。

- 转换器可用且派生 DXF 对象解析成功时标记 OBJECT_METADATA；转换器未配置、转换失败或对象解析不可用时标记 UNAVAILABLE。
- 原 DWG 不改写；派生文件、转换器标识和兼容性警告可追溯，不将部分对象解析冒充为完整 CAD 语义。

## FR-PARSE-007 · EML and Outlook MSG email parsing and workflow evidence

优先级：P0；状态：implemented

Parse RFC-style EML and bounded local Outlook MSG headers and visible body text while preserving RFI, Submittal and attachment boundaries.

- EML and MSG headers plus preferred visible body text become separately locatable evidence without active-content execution or remote fetches.
- A whitespace-only text/plain alternative does not suppress a non-empty safe HTML alternative; any non-empty plain text remains preferred without merging conflicting alternatives.
- Attachments, nested messages and non-root multipart/related resources are inventoried and inert before explicit import; related selects matched start/Content-ID or first child as its sole body.
- EML traversal stops at attachment boundaries; attached-email bodies and nested files cannot enter parent evidence, inventory or workflow roles before import.
- Only complete exact labels enter locators: Official Response sets an RFI response; known RFI Status and Submittal response/status values allow terminal punctuation, while longer prose stays neutral.
- An allowlisted RFI role or Submittal status immediately following an exact same-line identifier is separated from that identifier across body, Subject and filename fallback.
- Workflow IDs require digits; alphabetic project or discipline prefixes and aliases normalize across body, Subject and filename; spaced CSI IDs persist and roleless RFI stays unknown.
- Primary routing ranks an explicit Subject only when it contains a valid digit-bearing workflow ID, then the earliest body heading, then filename; other identifiers stay references and cannot donate role/status.
- Up to six bounded reply, forward or bracketed enterprise labels may precede an explicit workflow Subject without weakening identifier rules.
- Current body/history stay separate; addresses cannot create groups, while routing/history evidence remains reviewable but skips model extraction and cannot create current requirements or properties.
- An Outlook-style From block with Sent, Date, To or Subject starts quoted history whether labels share a line or use adjacent lines.
- Visible HTML text outside a closed blockquote remains current message evidence even when quoted history appears between two current reply passages.
- Exact Gmail, Yahoo and Proton quote classes plus Outlook divRplyFwdMsg delimit HTML history; text after a matching close resumes as current evidence.
- Exact -- plus trailing space and Gmail gmail_signature create reviewable signature evidence excluded from workflow routing and extraction; closed HTML signatures resume current text.
- Message-ID, In-Reply-To and References accept only bounded RFC-style angle-bracketed local@domain tokens and store accepted values only as local hashes; raw identifiers are never persisted.
- Multiple accepted Message-IDs set a conflict flag while missing or malformed values remain standalone; multi-value In-Reply-To preserves each exact accepted relationship.
- Exact ancestors precede replies; cycles retain every member and remain ambiguous.
- MSG parsing reuses the existing email evidence and workflow path through the pinned MIT-licensed python-oxmsg package; mailbox synchronization, automatic attachment recursion and semantic thread inference remain unsupported.

## FR-WORKFLOW-001 · Deterministic workflow relationships

优先级：P0；状态：implemented

Build a bounded reviewer index for exact RFI, Submittal and email relationships without model calls or guessed matches.

- Normalize exact digit-bearing RFI/Submittal aliases; preserve explicit roles, allowlisted same-line metadata, statuses and ambiguity.
- Retain each exact Submittal disposition; keep keyword-prefixed prose neutral, link exact references, preserve conflicts and infer no precedence.
- Route Email by a valid digit-bearing Subject, then earliest body heading, then filename; other IDs are references and routing headers cannot donate role/status.
- Email threads link only exact hashed relationships from bounded RFC-style angle-bracketed Message-ID tokens; arbitrary header words cannot link messages, external references do not expose raw identifiers, and a message that references its own exact ID is ambiguous.
- An email containing multiple distinct Message-ID values is marked ambiguous, while an email with no Message-ID remains a valid standalone item.
- A multi-value In-Reply-To header links every locally present exact accepted hashed identifier into the same review thread without semantic inference.
- Exact local email ancestors appear before replies with deterministic sibling ordering; a parent/reference cycle is ambiguous and retains every member.
- An explicitly imported EML or MSG attachment links to its parent email inside the analysis-run workflow view, without inheriting workflow role, approval or authority.
- The reviewer endpoint projects only workflow-relevant parser-summary fields before Python decoding and does not load page, vision, CAD or geometry arrays.
- Use duplicate-free 500-group pages; terminal runs reuse a bounded process-local index and active runs remain uncached.
- Versioned RFI/Submittal/Other corrections append audit events and invalidate the index; summaries, evidence, Email provenance and candidates stay immutable, with no parse, model or cost action.

## FR-INGEST-005 · Read-only Autodesk and Procore import

优先级：P0；状态：implemented

Allow a customer-configured read-only Autodesk APS or Procore connection to browse and selectively import project documents into the existing immutable upload pipeline.

- Only fixed official API bases are called with the configured bearer token; the token is never returned by CIRP and optional persistence uses Windows current-user DPAPI.
- The connector lists remote folders/files and imports only a user-selected file; it does not create, update or delete remote content.
- Imported bytes pass through the existing project capacity, 4 MiB chunk, SHA-256 and duplicate-content controls.
- Only trusted HTTPS download hosts are accepted and a bearer token is not forwarded to a different signed-storage host.
- All automated validation uses synthetic httpx MockTransport responses and makes no live Autodesk or Procore request.

## FR-INGEST-006 · Selected local email attachment import

优先级：P0；状态：implemented

Let a reviewer inspect inert EML or Outlook MSG attachments and explicitly import one or more selected attachments through the existing immutable upload pipeline.

- Attachment listing returns only bounded metadata/SHA-256; unnamed parts use a standard MIME extension so supported imports stay analyzable, while raw bytes are never returned.
- One explicit request may import 1 to 100 selected attachments; the email is parsed once and every index and SHA-256 is validated before any upload is created.
- Every selected attachment passes through project capacity, chunking, hashing and duplicate-content controls and never starts analysis automatically.
- Every import event preserves the parent email, attachment index, content type and expected SHA-256 even when the document content is deduplicated.
- Duplicate imports collapse to one counted parent-email/attachment relationship; immutable parsed-Email metadata keeps it visible even when a run overlay changes the parent's effective workflow classification.
- Attached emails require another explicit selection before their body or nested attachments are visible; neither may contribute to the parent email analysis, and active content and remote resources remain inert.

## FR-DATA-001 · 统一文档模型

优先级：P0；状态：planned

所有输入转化为 Canonical Document Model。

- Document、Fragment、Evidence 字段通过 Schema/DB validation。

## FR-CLASS-001 · 自动文档分类

优先级：P0；状态：planned

自动识别文档类型、专业、CSI、系统、设备、区域和 Revision。

- 用户可查看并修正分类。
- Deterministic parsing preserves explicit RFI question/response, Submittal status and EML body/header source roles before semantic classification, including allowlisted metadata immediately after an exact same-line identifier.
- For a completed or partial run, a reviewer can apply or reset an exact RFI/Submittal/Other overlay with optimistic versioning, append-only audit and immediate workflow-index invalidation without hiding parsed Email attachment provenance; other classification dimensions remain planned.

## FR-REVISION-001 · 内部日期最新优先

优先级：P0；状态：planned

同一适用对象、位置、属性和条件的有效候选，按文件内部Revision日期确定当前采用值。

- 上传时间不参与排序。
- 同日矛盾、日期缺失或Revision次序与日期冲突时待核验。
- 支持单Sheet和单条款替换，不整包误废止。

## FR-REVISION-002 · 范围化修订

优先级：P0；状态：planned

最新规则只作用于明确可比较范围，保留历史及差异。

- 不比较不同设备、不同选项和不同工况的属性。
- RFI问句不是修订指令，选项广告不是指定选型。
- 同一候选混合有效证据与RFI问句、被拒Submittal、邮件头或引用历史时，无效来源不得保留为直接依据。

## FR-REVISION-003 · Submittal 不自动覆盖设计

优先级：P0；状态：superseded

较新的 Submittal 不因日期自动覆盖设计要求。

替代需求：FR-REVISION-004
- 不一致产生 deviation/conflict。

## FR-REQ-001 · 原子 Requirement

优先级：P0；状态：planned

每段文档可提取原子、结构化、可追踪 Requirement。

- CONFIRMED Requirement 至少有一个 Evidence ID。

## FR-ROUTE-001 · CSI/Topic 路由

优先级：P0；状态：planned

Requirement 按 CSI、专业、系统、设备和区域路由。

- 同一 Requirement 可有多标签。

## FR-MATERIAL-001 · 永久材料与产品选项

优先级：P0；状态：planned

列出永久材料设计要求、产品、厂家、型号和允许选项；无文件依据不虚构。

- 多个选项显示ONE_OF，不自行选中。
- 设计要求、送审产品、选择状态分开保存。
- 属性标题、尺寸、gauge和泛称不得作为材料名；尺寸、规格及数量保留在独立字段。

## FR-MATERIAL-002 · 临时材料清单

优先级：P0；状态：planned

输出临时材料名称和状态。

- 首版不要求临时材料品牌型号。

## FR-MATERIAL-003 · 明确与推导分层

优先级：P0；状态：planned

区分 CONFIRMED、INFERRED_TO_VERIFY 和 CONDITIONAL。

- UI 和 Export 保留状态，不合并为普通材料。

## FR-MATERIAL-004 · Assembly 候选

优先级：P0；状态：planned

识别施工 Assembly 并生成受控配套材料候选。

- Concrete equipment pad 可产生 formwork/rebar/anchor/grout 等可审核候选；不得编造规格。

## FR-MATERIAL-005 · 按 CSI 组织

优先级：P0；状态：planned

Material Register 主要按 CSI Division/Section 组织。

- 支持按专业、系统、设备、区域筛选。

## FR-TAKEOFF-001 · 设计净量与计算追踪

优先级：P1；状态：planned

只输出设计净量，区分安装实例与重复图面；不加损耗、包装取整或采购备用量。

- 直接数量、表格数量不强制要求比例尺。
- 几何量算必须具备比例、公式、输入和来源。
- 量算未实现或不能确定时quantity为null。

## FR-TAKEOFF-002 · 人工核验

优先级：P0；状态：planned

所有自动数量初始为待人工核验。

- 未审核数量在 Export 中有状态。

## FR-TAKEOFF-003 · 不确定时停止

优先级：P0；状态：planned

几何量算无法确认Scale/单位/范围时quantity=null并标待核验；显式或Schedule数量不要求无关比例尺。

- 未知不等于0。
- 明确文档数量可无Scale。
- 缺几何依据不输出伪精确数值。

## FR-INSPECTION-001 · 完整 QA/QC 类型

优先级：P0；状态：planned

提取 Inspection、Testing、Reports、Startup、Commissioning、Training 和 Closeout 要求。

- 覆盖 Product Spec 列出的全部类型。
- 图纸、示意图和通用送审不得仅因test、verify或report关键词成为QA活动。

## FR-INSPECTION-002 · 仅上传文件作为项目依据

优先级：P0；状态：planned

项目 QA/QC 要求必须来自上传资料。

- 外部知识不得生成 CONFIRMED 项。

## FR-INSPECTION-003 · 结构化字段

优先级：P0；状态：planned

每项包含 timing、frequency、criteria、standard、party、report 和 source。

- 输出通过 inspection schema。

## FR-CONFLICT-001 · 设计差异与真实冲突

优先级：P0；状态：superseded

比较同一内容不同来源，区分已按最新版采用的变更、未解冲突及选项差异；不裁定商业供货责任。

替代需求：FR-EXPORT-002
- 最新版采用保留双方证据。
- 相同值不同单位经换算后不误报警。
- 供货责任不作为首版分析任务。

## FR-CONFLICT-002 · 不自动裁决

优先级：P0；状态：superseded

系统只报告冲突，不自动决定正确设计。

替代需求：FR-REVISION-004
- 用户必须看到双方证据和 unresolved 状态。

## FR-CONFLICT-003 · 影响关联

优先级：P1；状态：superseded

冲突关联受影响材料、QA/QC 和 Missing 项。

替代需求：FR-EXPORT-002
- Conflict 输出包含 affected IDs。

## FR-MISSING-001 · 只报告阻断性缺失

优先级：P0；状态：superseded

仅在缺少信息阻碍当前输出或判断时生成独立缺失项，区分未上传、未解析和未检索到。

替代需求：FR-EXPORT-002
- 未指定型号但性能完整只显示待选型。
- 引用标准未上传不从外网或模型记忆补条款。

## FR-EVIDENCE-001 · 可点击证据

优先级：P0；状态：planned

正式输出可定位至原文件、页、Sheet、Section、Cell 或 bbox。

- Evidence Viewer 打开正确来源。

## FR-COVERAGE-001 · 覆盖审计

优先级：P0；状态：planned

显示每个文件、页面/Sheet 和 Fragment 的处理状态。

- Coverage Report 总数与任务记录一致。

## FR-REVIEW-001 · 逐项人工审核

优先级：P0；状态：planned

所有输出初始待审核，用户可 Accept/Edit/Reject。

- 审核保存原值、新值、用户、时间和原因。

## FR-REVIEW-002 · 本次运行内审核记录

优先级：P0；状态：planned

保留本次结果的人工编辑记录；暂不实现跨分析运行自动继承或合并人工修改。

- 模型候选没有审核权限。
- 重新分析创建独立快照，不覆盖旧审核。

## FR-EXPORT-001 · 原型导出

优先级：P1；状态：planned

原型至少导出XLSX、JSON，CSV可复用；PDF和打包证据暂为后续项。

- 导出不再调用模型。
- 显示选项关系、依据、版本、数量核验和运行完整性。

## FR-UI-001 · 项目 Dashboard

优先级：P1；状态：planned

Show run coverage, current materials and QA review results, analysis percentage, estimated finish time, and human-review progress.

- Dashboard data comes from persisted run/register state and is not invented by a model.
- While analysis is active, show a stage-weighted percentage and an elapsed-time ETA; label it as an estimate and show Estimating when there is not enough progress data.

## FR-API-001 · Provider abstraction

优先级：P0；状态：implemented

所有模型调用通过 ModelGateway。

- 业务模块不得直接依赖供应商 SDK。

## FR-API-002 · 结构化输出

优先级：P0；状态：planned

模型输出必须使用版本化 JSON Schema 并验证。

- 无效输出重试或进入失败队列。

## FR-API-003 · 最低足够成本路由

优先级：P1；状态：planned

能程序化的不调用LLM；简单语义任务走低成本模型的最低可用推理模式，复杂任务按条件升级。

- 分类与字段提取不得依赖供应商默认推理强度：能关闭时显式关闭，不能关闭时显式采用最低支持级别并计入推理token。
- 无证据不升级更强模型来猜测。
- 高价模型默认禁用。

## FR-API-004 · 有限数据发送

优先级：P0；状态：planned

不把 10GB 项目整体发送给单次模型请求。

- 每次调用记录 Evidence IDs 和输入范围。

## FR-API-005 · 自有批量任务编排

优先级：P1；状态：planned

产品端排队、分批和限流；不假定供应商具有Batch、background或文件检索能力。

- A run can use 1, 2, or 4 bounded local parser/OCR workers across documents.
- A single PDF can use the selected workers across bounded page chunks while preserving page order and equivalent output.
- A concurrent document PermissionError receives one isolated retry using the selected page-worker count, without retrying model work or unrelated parser failures.
- Up to four adjacent fragments/8.8 KB batch only inside one ordinary or exact workflow scope; an RFI/Submittal ID, role, status or family change closes the batch, and routing-only Email evidence closes locally.
- Paid model calls remain serial and every call is recorded in the project ledger.

## FR-API-006 · Customer-configurable OpenAI-compatible model

优先级：P0；状态：implemented

A local customer can start CIRP with a customer-selected OpenAI-compatible text API or a loopback local model without changing application code.

- Remote endpoints require HTTPS, an API key, and positive customer-confirmed CNY token rates; HTTP and an empty key are allowed only for a loopback local model.
- The provider identity binds the API base URL and model so a run cannot resume under a different custom configuration.
- The generic route uses chat/completions structured text only, sends no provider-specific reasoning parameter, and does not enable page-image transfer.
- The normal local application includes a dedicated Settings section, not a modal dialog or separate setup server, where the customer can select Mock, DeepSeek, Gemini, a custom HTTPS OpenAI-compatible API, or a loopback local model and safely restart the single local service without deleting project data or model-call history.
- A settings change is blocked during active analysis, semantic verification, or any unresolved model call; API keys are never returned and optional persistence uses Windows current-user DPAPI with a non-secret active-profile file.
- A normal local start reuses an explicitly saved active profile, while an explicit --mock start bypasses it without deleting the saved profile or project data.
- Every open application tab detects a completed same-port service restart and reloads its non-secret configuration, so an older Mock tab cannot hide the active provider's replacement-key controls.

## FR-PROMPT-001 · Prompt Git 版本化

优先级：P0；状态：planned

生产 Prompt 必须进入仓库并带版本。

- Analysis Run 可追溯到 prompt version。

## FR-MODEL-001 · 模型调用可追溯

优先级：P0；状态：planned

记录 provider、model、token、cost、latency、retry。

- Each Analysis Run can export a provider/model/token/cost/latency/retry summary.
- The reviewer UI reports aggregate stage time, provider wait time, model response time, and processed page counts.

## NFR-SECURITY-001 · 基础安全

优先级：P0；状态：planned

无额外地区或零保留限制，但保留密钥保护、项目隔离、输入安全及基本访问控制。

- 无密钥进入Git。
- 只有用户明确选择保存时，API密钥才可写入Windows DPAPI当前用户加密文件；不得写入明文.env、网址、命令行或日志。
- 不执行上传文档内指令或宏。
- 不自动将客户资料用于跨项目训练。

## NFR-SECURITY-002 · Prompt injection 防护

优先级：P0；状态：planned

文档内容不得成为系统指令或执行未授权工具。

- 安全测试覆盖恶意文档指令。

## NFR-SECURITY-003 · 审计日志

优先级：P0；状态：planned

上传、分析、审核、导出和删除均记录审计事件。

- 事件包含 actor、time、object、action。
- A workflow-classification correction records actor, time, run/document object, before/after values and the review note in an append-only event.

## NFR-RELIABILITY-001 · 任务幂等可恢复

优先级：P0；状态：planned

处理任务支持重试、恢复和幂等。

- 同一 job retry 不产生无控重复输出。

## NFR-OBS-001 · 可观测性

优先级：P1；状态：planned

记录解析、模型、成本、错误、审核和质量指标。

- 可按项目和任务查询。

## QLT-EVAL-001 · 正式施工Gold评测暂缓

优先级：P0；状态：deferred

用户暂不要求历史项目Gold集或准确率商业承诺；保留未来入口。

- 不将合同测试通过表述为施工准确率通过。

## QLT-EVAL-002 · 正式质量门槛暂缓

优先级：P0；状态：deferred

删除未经批准的95%准确率、50%省时等硬门槛；基础契约和负例回归仍必须运行。

- 不要求付费API评测才能检查规格包。
- 对接后能力探针属于集成检查而非施工准确率评测。

## DEV-SPEC-001 · 审批后同步变更

优先级：P0；状态：planned

业务范围、规则、预算和权限变化须产品负责人先批准；同PR更新规格、实现状态和适用测试。

- 本地检查可检测缺文件和变更清单，不能代替人类审批。
- 规格先行时显式spec_only，产品仍planned。

## DEV-SPEC-002 · Requirement ID

优先级：P0；状态：planned

每项需求有不可复用唯一 ID。

- 检查脚本拒绝重复 ID。

## DEV-SPEC-003 · Traceability

优先级：P0；状态：planned

Requirement 映射到模块和测试。

- P0 requirement 必须出现在 traceability。

## DEV-SPEC-004 · 版本绑定

优先级：P0；状态：planned

App、Spec、Schema 和 Prompt bundle 版本可查询。

- UI 或 health endpoint 显示版本。

## FR-REVISION-004 · 最新采用并留痕

优先级：P0；状态：planned

程序按可比较范围及内部日期选择当前展示值，不宣称合同或法律裁决。

- LATEST_APPLIED保存selected_evidence_id。
- 无法比较时UNRESOLVED，不能用上传时间兜底。
- 同一来源对同一Submittal编号保存多个不同显式状态时必须保持歧义并供人工比对，不能自动选择其中之一。
- RFI答复、Submittal或Email正文是否使候选保持条件性，必须检查全部剩余直接证据，不能只看第一条。

## PRD-PROTOTYPE-001 · 网页可运行原型

优先级：P0；状态：planned

目标交付包括上传、启动分析、结果、来源查看、人工审核和导出的网页原型。

- 上传、启动、进度、四类候选、来源、人工审核和导出在网页内可用。
- 每种未实现格式和能力明确显示PARTIAL；真实API与工程准确率未测时不得宣称通过。

## PRD-RESP-001 · 不判断采购责任

优先级：P0；状态：planned

项目所需项不因业主供货或其他方提供而排除，不生成责任裁定。

- 可以保留原文责任说明，但不推断义务归属。

## FR-OPTIONS-001 · 互斥选项组

优先级：P0；状态：planned

每组允许产品保留所有选项，ONE_OF仅一项作为实际选择。

- 无证据selected_option_ids为空。
- 选择数量作用于组，不把三个选项数量相加。

## FR-INSTANCE-001 · 实物与图面分离

优先级：P0；状态：planned

实物实体、设计类型、图面出现、组件和数量依据分离。

- 同门在平面与表格两次出现不能双计。
- 相同文字用于两个位置不能直接合并为一个实例。

## FR-FIELD-EVIDENCE-001 · 字段级依据

优先级：P0；状态：planned

型号、性能、数量和测试频率分别链接支持它的证据。

- 行级证据不能自动支持所有字段。
- 证据必须存在于本项目本运行输入中。
- 字段同时引用有效和无效工作流来源时，只删除无效直接引用，不得连带删除仍有有效依据的字段。

## FR-SCHEMA-001 · 模型候选与服务端记录分层

优先级：P0；状态：planned

候选仅含业务字段；运行版本、用户身份和审核结果由服务端附加。

- 模型写ACCEPTED或伪造meta时Schema拒绝。
- 正式记录外层必须有全部来源版本。

## FR-COVERAGE-002 · 语义覆盖

优先级：P0；状态：planned

任务成功和提取完整分别记录，零抽取、跨页表格与脚注有原因或复查状态。

- PENDING、FAILED、LIMITED不计入完整分析。
- 每个识别区域有analysis disposition。

## FR-CONTEXT-001 · 继承和例外

优先级：P0；状态：planned

抽取上下文保留Section标题、前置定义、否定、例外、数量单位和表格脚注。

- 拆分不能丢失unless/except条件。
- 长段超限须结构化拆分而非截断。
- Detected specification sections, clauses, and table rows remain attached to their evidence locators.

## FR-ROUTE-002 · 零token任务

优先级：P0；状态：planned

哈希、MIME、精确编号、单位运算、JSON校验、去重键、合计、导出由程序执行。

- 无模型路由调用。
- 模糊分类才升级cheap。

## FR-ROUTE-003 · Flash最低推理默认

优先级：P0；状态：implemented

cheap角色默认使用deepseek-v4-flash且thinking disabled；用户明确选择Gemini 3.6 Flash时使用reasoning_effort minimal，输出短JSON。

- 不得依赖供应商默认思考模式；Gemini 3无法关闭推理时采用其最低支持级别。
- 推理token计入输出预算，不重复计算。
- 不要求输出思维链。

## FR-ROUTE-004 · 受控升级

优先级：P0；状态：planned

只对证据完整但关系模糊、修复失败、跨对象矛盾的任务升级。

- 一次局部修复上限。
- 推理优先同Flash低effort；高价模型需额外启用。

## FR-TOKEN-001 · 单次联合抽取

优先级：P0；状态：planned

同片段首读联合抽取材料/QA/报告候选，避免三个Agent分别读同一原文。

- 大结果改小语义单元。
- 逐Fragment记录清单，不以检索前K替代全量首读。

## FR-TOKEN-002 · 短上下文和缓存

优先级：P0；状态：planned

只发送当前语义块和必要邻接；稳定Prompt前缀；应用结果缓存带完整版本隔离。

- 缓存键含tenant/project/input snapshot/prompt/model/schema/retrieval/rules。
- 缓存未命中按最坏费用预算。

## FR-TOKEN-003 · 输出上限与完整性

优先级：P0；状态：planned

限制输出token而不静默截断材料；遇length或空JSON须细分重排队。

- 列表仅返回证据ID与短支持理由。
- 不复制整本规范或累计聊天历史。

## FR-VISION-001 · 视觉专用路由

优先级：P0；状态：planned

纯文本模型不得假装读图；数字文字优先，必要图形进入视觉/OCR流程。

- Vision candidates require a provider capability probe and are limited to large-format, low-text graphic, or dense-graphic pages.
- High-resolution local crops preserve full-page coordinates and transforms.

## FR-CAPABILITY-001 · 实际API能力验证

优先级：P0；状态：planned

用户提供接口的文本、JSON、thinking开关、视觉和计费分别核查。

- 文档支持不等于用户密钥已开通。
- 无视觉时未读图区域明确标记。

## FR-BUDGET-001 · User-selected project budget limit

优先级：P0；状态：superseded

Each project has a user-selected cumulative CNY processing limit across runs and retries; new paid work is blocked before the selected limit is exceeded.

替代需求：FR-MODEL-BUDGET-REMOVAL-001
- A new project defaults to CNY 300 and accepts a user-selected limit from CNY 0.01 through CNY 1,000,000.
- The limit cannot be set below settled plus outstanding cost; unknown charges remain reserved and retries require a new reservation.
- Every changed limit is audit-recorded, and a valid user change may unlock a budget-frozen project without resetting spent cost.

## FR-BUDGET-002 · 先预留再调用

优先级：P0；状态：superseded

任务发出前原子预留最坏费用，完成后核销；未知账单不提前释放。

替代需求：FR-MODEL-BUDGET-REMOVAL-001
- 并行worker不能同时越限。
- 超时重试可能重复计费，必须重新预留。

## FR-BUDGET-003 · 价格可配置

优先级：P0；状态：superseded

官方价格仅参考快照；中转价和图像/OCR/CAD价必须单独确认。

替代需求：FR-MODEL-BUDGET-REMOVAL-001
- 未确认实际单价不开启付费。
- 缓存折扣、离峰折扣不用于最坏预算。

## FR-BUDGET-004 · 部分完成透明

优先级：P0；状态：superseded

预算或时限阻断必须显示已完成、未完成、阻断原因和已花费用。

替代需求：FR-MODEL-BUDGET-REMOVAL-001
- 不自动扩预算。
- 低优先任务仍入队，不隐瞒未执行。

## FR-DEADLINE-001 · 24小时目标和单项目

优先级：P0；状态：planned

最多一个活跃项目，24小时从分析启动计时作为目标及软停止控制。

- 上传时长分开显示。
- 到时不宣称完成，停止新增工作并保留待办。

## FR-REVIEW-003 · 无需跨运行合并

优先级：P0；状态：planned

新运行不自动承接人工值；原运行不被覆盖。

- 只实现当前运行接受、修改、拒绝。

## FR-ASSEMBLY-001 · 受控推导范围

优先级：P0；状态：planned

组件规则触发永久/临时候选；QA清单不能由通用组件模板虚构。

- 配筋规格无项目证据保持空。
- 模板可列名称，不自动锁定胶合板。

## FR-INPUT-STATUS-001 · 支持格式明确

优先级：P0；状态：planned

保持用户指定PDF/DOCX/TXT/图片/DWG；XLSX和邮件不自动重加为首版输入。

- 图片枚举支持类型，其他类型报UNSUPPORTED而不丢失清单。

## FR-TESTING-001 · 质量要求与报告关系

优先级：P0；状态：planned

测试、验收、报告分字段建关联，未知标准正文只保存引用。

- 报告期限保留相对触发事件。
- 只有引用编号不生成正文要求。

## DEV-APPROVAL-001 · 人类变更批准

优先级：P0；状态：planned

开发LLM不能将proposal自行改成批准或更改预算/上线边界。

- 记录用户批准证据。
- CI不能信任PR作者自己填写approved作为唯一依据。

## DEV-LOWCOST-001 · 开发代理同样分级

优先级：P0；状态：implemented

重复字段改名、模板和小测试可交给低价开发模型；架构与审批交人工或更强审查。

- 子任务只提供相关ID、文件片段和测试上下文。
- 未配置子模型时不声称已委派。

## DEV-TRACE-001 · 实现状态真实

优先级：P0；状态：planned

planned不要求虚构代码路径；implemented必须有真实代码、测试和报告。

- 契约示例通过不等于业务已实现。
- Traceability标记contract_only或product_test。

## DEV-CHANGE-001 · 变更差异检查

优先级：P0；状态：planned

PR含可解析变更清单，涉及行为的配置/Prompt/代码变化必须关联需求和测试。

- 明确spec_only、implementation和bugfix三种。
- 手工语义审查仍必要。

## DEV-CHECK-001 · 可运行离线校验

优先级：P0；状态：implemented

离线脚本执行Schema定义检查、正负例、Prompt存在性、版本和Traceability。

- 不调用付费API。
- 失败时非零退出。

## FR-REMOTE-001 · 受保护的单用户远程测试入口

优先级：P0；状态：implemented

实现远程鉴权、静态部署白名单、同源代理和默认mock启动脚本。

- 所有页面和业务API无凭据返回401；无保护配置拒绝启动；Render免费profile仅/_health GET/HEAD公开无业务信息的存活结果。
- Pages不上传用户数据/密钥，源站秘钥不发给浏览器。
- 本地模拟端到端及Worker负例测试通过；不据此声称公网完成。

## FR-DEPLOY-001 · Cloudflare实际发布与公网验收

优先级：P0；状态：planned

在用户授权账户或Quick Tunnel运行环境创建真实可访问入口，通过未登录401和已登录200验收后记录实际URL。

- 真实部署响应返回URL，不拼接网址冒充部署。
- 验证公网身份保护与mock状态，显示源站依赖和功能边界。
- 没有Cloudflare授权或外网时如实报告阻断。

## FR-RENDER-001 · 免费Render测试适配

优先级：P0；状态：implemented

一个明确free的测试Web Service；独立临时数据、强制mock、鉴权、公开最小存活检查和易失性提示；本机设置不变。

- 平台配置无收费数据库/磁盘，关闭自动部署。
- 不加载本机.env或API Key，测试上传限额可见。
- 业务API与页面401/200，health无敏感信息，样例流程通过。

## FR-RENDER-LIVE-001 · Render真实发布验收

优先级：P0；状态：planned

在已连接的用户Render及源码仓库中创建Free测试服务；使用平台实际返回URL验收，禁止用配置生成替代上线。

- 确认实际计划free和工作区计费配额。
- 验证公网health=200、页面未登录401及登录200。
- 没有平台授权时如实保留未部署状态。

## FR-LOCAL-001 · 一键本机部署入口

优先级：P0；状态：implemented

无需远端仓库或云账户启动网页、API、SQLite和单进程任务；默认隔离付费配置，真实API仅显式live入口。

- 提供Windows CMD及macOS/Linux入口；首次隔离安装，失败不得宣布启动。
- 默认模拟入口不读取.env；端口冲突拒绝启动、不杀其他进程。
- The live-key setup refuses an occupied application port before loading, saving, forgetting, or passing a credential to a child process.
- 已有依赖环境真实HTTP示例流程和重启持久化通过；未验证平台/依赖安装如实报告。
- 原远程预览保护和预算不变，不将本机端口公开。

## FR-UI-002 · English-only user and developer presentation

优先级：P1；状态：implemented

Assume users and developers work in English. The application, setup flow, launcher diagnostics, public status text, and reviewer exports present CIRP-owned text in English without changing project data, analysis, review, or budget behavior.

- English is the only selectable display language; an old saved zh-CN preference cannot switch the interface back to Chinese.
- The key-entry page, local launcher, dependency checks, public settings, and cost descriptions use English application-owned messages.
- The change does not clear unsaved project names, review notes, filters, upload selection, or project/run state and does not add a network or model call.
- Original source-document quotations remain faithful to the source; fixed reviewer output otherwise uses concise English and fails closed when required legacy business text cannot be translated safely.
- API paths, canonical status codes, request headers, algorithms, saved project data, human reviews, and model-call history do not change.
- The English interface remains readable on narrow screens without a translation API, new front-end framework, or runtime dependency.

## FR-CITATION-001 · 逐字段原文定位

优先级：P0；状态：implemented

材料属性、检查要求、设计差异字段绑定原文字符范围和稳定证据；引用正文由程序切片取得。

- 文本引用精确匹配且唯一；空、伪造或重复难以定位的引文不通过。
- PDF新解析保留词级字符范围与坐标；点击可显示受限尺寸的原图区域。
- 普通文字匹配不代表语义支持；非原句依据单独显示。

## FR-VERIFY-001 · 独立低成本语义核验

优先级：P0；状态：implemented

新增独立核验状态，与项目要求状态和人工批准状态分离。

- Programmatic checks validate citation bounds, text fragments, and numeric risk; the small model verifies at most four fields per request and can combine fields from records with the same exact evidence scope.
- Semantic verification uses the configured model's lowest available reasoning mode and records provider usage without a CIRP monetary gate.
- 无密钥、预算不足、未知费用、截断、假引文均不得标为通过；模拟模式不伪造语义核验。
- 不会以此声称无漏项、设计正确、现场合规或全项目反证检索完成。

## FR-VERIFY-002 · 人工修改后失效与重验

优先级：P0；状态：implemented

核验绑定候选内容、上下文、证据快照；人工修改不继承不再适用的核验结果。

- 修改字段后重建免费引用检查，语义结果失效；未变字段在语义上下文与证据也未变时复用。
- 付费重验需显式入队，进程中断、过期任务和内容变化不会静默重复收费。
- 旧记录可查看，旧预算及审核历史不删除；新增表为幂等可加迁移。

## FR-EXPORT-002 · 面向工程审核的英文简洁导出

优先级：P0；状态：implemented

JSON和Excel以固定英文只输出可直接审核的材料/设备与可执行测试/检查；名称、设计属性、规格章节、数量、执行方和对应证据分列，不显示Conflict、Missing、内部ID或原始结构代码。

- 导出读取已保存结果，不调用模型。
- Excel固定使用英文Summary、Materials & Equipment和Inspections & Tests表；有已保存CAD量算候选时才增加Quantity Takeoffs，不输出Conflict、Missing Information、独立Evidence、Field Verification、Citations或PDF Geometry Audit表。
- 材料或设备名称、设计属性、规格章节、数量和单位分列；尺寸、厚度、规格和明确数量不得充当名称，数量无明确依据时保持空白且不得写为零。
- Tests & Inspections只列可执行QA活动，并分列规格章节、执行方、见证方、时机、频率和验收标准；接线图、Shop Drawings、进度资料和普通Submittal不得充当测试。
- JSON与Excel不显示内部记录/证据/调用ID、原始候选JSON、字段路径、哈希或格式代码；证据紧随对应项目且优先显示实际支持句，所有文本不作为公式执行。

## FR-QA-001 · Evidence-grounded project questions

优先级：P0；状态：implemented

A user may ask an English question about a selected completed analysis run and receive a concise answer based only on bounded, project-scoped evidence or the deterministic complete-run workflow index, with exact source quotations immediately after evidence-based model answers.

- Retrieval is limited to immutable evidence from the explicitly selected project and PARTIAL or COMPLETED run; a run from another project is rejected.
- A live or local model call occurs only after the user submits a question, uses the configured model with no automatic retry, records provider usage, and is blocked by any unresolved model call; a terminal evidence snapshot remains usable when its analysis provider differs from the currently configured question model.
- An answered response cites only evidence supplied to that request and every published quote must match the immutable source text exactly and uniquely; invalid citations fail closed after cost settlement.
- Mock mode never fabricates an answer and makes no model call; it may display locally retrieved context as such.
- The browser renders the answer as plain text and places each source passage directly after it with a control that opens the existing evidence viewer.
- Run-scoped local full-text ranking must not discard a later exact RFI, Submittal, Email or source-locator match because earlier evidence contains a common term; existing databases are backfilled, and runtimes without FTS5 use a complete compatibility scan.
- Question retrieval may use only capped, audited construction/workflow equivalents with whole-word matching and direct-term priority; it must not add an opposite workflow disposition or make an additional model request.
- An explicit comparison or a question naming at least two supported source families selects distinct document/workflow sources before same-source fillers within the existing evidence-count and character limits; a normal single-source question retains relevance-only order.
- Before the evidence-count and character limits are applied, exact duplicate source text from the same source document, source family and semantic Section/Sheet/Paragraph scope contributes at most one model-context passage. Identical text from another document or another semantic locator scope remains eligible; a physical Page change alone does not manufacture another semantic passage.
- When source diversification supplements an explicit Email comparison, FTS recognizes a generically named document whose first visible header is From, To, Cc or Subject and whose text contains a Subject header, in addition to native EML/MSG and Email-locator evidence. The complete-scan compatibility path and FTS path retain the same conservatively classified Email source under a full primary-candidate limit.
- When source diversification supplements an explicit RFI or Submittal comparison, FTS recognizes a line-leading RFI, Request for Information, Submittal or Submission heading at the start of a generically named evidence fragment or after a newline. Prefixed RFI identifiers and Submission headings remain available when the primary candidate limit is full, and the complete-scan compatibility path agrees without fuzzy workflow inference.
- An answered comparison contains a separate plain-English finding for every source family named in the question, and each finding's readable type and file name must match one or two exact citations rendered directly with that finding; missing families and mismatched sources fail closed.
- Drawing is a first-class comparison source family alongside Specification, RFI, Submittal and Email. An explicit Drawing/Sheet/Dwg/Detail identifier or plural Drawings, Sheets, Plans or Plan Set wording requests that family, while a generic singular sheet lookup does not and Shop Drawing remains Submittal unless another drawing source is separately named. Eligible evidence is classified conservatively from an exact Sheet locator, native CAD extension, explicit drawing file name or line-leading drawing marker after higher-confidence Email, RFI, Submittal and Specification checks; model-vision narration remains excluded.
- Every explicit numeric literal in an answered summary occurs in its top-level or nested exact quotations, and every numeric literal in a source finding occurs in that finding's own quotations; unsupported calculations, conversions and inferred values fail closed.
- Within an ordinary answer and each source finding, every non-identifier numeric value attached to an explicit RFI or Submittal identity occurs with that same normalized identity in one cited statement, or the citation locator supplies exactly that one matching identity when the statement omits it. Another workflow item in the same quotation and a multi-identity locator cannot donate a dimension, quantity or other numeric value; comparison summaries rely on their individually validated source findings.
- Within an ordinary answer and each source finding, a number claimed under an explicitly cited size/diameter/dimensions, thickness, length, width, height, depth, quantity, pressure, strength, flow, capacity, temperature or weight role remains paired with that same normalized role. When an RFI or Submittal is named, the identity, role and number remain together; a different role or workflow item in the same citation cannot donate the value. The guard activates only for roles explicitly present or conservatively inferred from a dimensional pipe/duct/tube/conduit or insulation phrase and performs no unit conversion, arithmetic or full engineering interpretation.
- Every recognized measurement unit in an ordinary answer or source finding remains paired with the same explicit number. When a bounded measurement role and RFI/Submittal identity are present, identity, role, number and unit remain together in the cited scope; another value, property or workflow item cannot donate the unit. Recognized alphabetic units may touch their number or use a space. Formatting-only inch/inches/in./quotation-mark and feet/foot/ft equivalents normalize, while dimensional, pressure, flow, percentage, weight and temperature units remain distinct and no conversion is performed.
- Every explicitly labelled six-digit CSI/MasterFormat specification section in an ordinary answer or source finding occurs as one complete section in that scope's exact quotations or evidence locators. Spaces, hyphens and dots between two-digit groups are formatting-equivalent; groups from different sections cannot be recombined. When an RFI or Submittal is named, its identity and section remain together in one cited statement or one exact locator, while an unlabelled Submittal identifier is not treated as a specification-section claim.
- Every explicitly written Revision or Rev label in an ordinary answer or source finding occurs as one complete label in that scope's exact quotations. Prefix punctuation and letter case are formatting-equivalent while the normalized label remains exact; labels cannot be recombined or borrowed from another workflow or source finding. When an RFI or Submittal is named, its identity and revision remain together in one cited statement or one exact identity locator; revision dates, unlabelled words and evidence metadata alone do not create a revision-label claim, and the validator does not infer which revision controls.
- Every explicit Sheet, Drawing, Dwg or Detail identifier in an ordinary answer or source finding occurs as one complete identifier in that scope's exact quotations or Sheet locator. Prefix spelling, punctuation, letter case and dash glyphs are formatting-equivalent while identifier letters, digits, dots, slashes and separators remain exact; slash-form detail callouts remain whole, and identifiers cannot be recombined or borrowed from another workflow or source finding. When an RFI or Submittal is named, its identity and drawing identifier remain together in one cited statement or one exact identity locator; unlabelled codes do not create a drawing claim, and the validator does not infer applicability, discipline, revision or authority.
- Every explicit dotted Paragraph/Para, Clause or Article identifier in an ordinary answer or source finding occurs as one complete typed identifier in that scope's exact quotations or Paragraph locator. Paragraph and Para plus prefix punctuation and letter case are formatting-equivalent, while Paragraph, Clause and Article remain distinct and the dotted value remains exact; components cannot be recombined or borrowed from another workflow or source finding. When an RFI or Submittal is named, its identity and typed clause identifier remain together in one cited statement or one exact identity locator; unlabelled, undotted and date-shaped values do not create a clause claim, and the validator does not infer hierarchy, applicability or authority.
- Every explicit conventional email address in an ordinary answer or source finding occurs as one complete address in that scope's exact quotations. Domain letter case is formatting-equivalent while the local part remains exact; local and domain components cannot be recombined or borrowed from another workflow or source finding. Explicit From, To, Cc, Bcc and Reply-To headers plus bounded sender/recipient prose remain paired with the same address. When an RFI or Submittal is named, its identity and address remain together in one cited statement or one exact identity locator; the validator does not infer mailbox ownership, thread participation, identity equivalence or authority.
- Every explicit full date in an answered summary occurs as one date in its top-level or nested exact quotations, and every full date in a source finding occurs in that finding's own quotations. ISO and English month-name forms are formatting-equivalent, while ambiguous slash dates retain their written order; recombined date components, slash-to-ISO interpretation, date arithmetic, chronology and authority inference fail closed.
- Every explicit due, issued, submitted, received, sent, reviewed, approved, revision or response date in an answered summary or source finding retains the same date-role pairing in one citation. Different lifecycle dates in one RFI, Submittal or Email may support a date question without becoming conflicting current statuses, but multiple cited dates for the claimed role and ordinary status/disposition questions remain ambiguous; no event order is treated as current-state authority.
- When an explicit RFI or Submittal date claim names a workflow identity, its normalized identity, date role and full date occur together in one cited statement, or the citation locator supplies exactly that one workflow identity. A date attached to another identity or an unscoped multi-identity locator fails closed; distinct workflow identities may retain independent dates for the same role, while multiple dates for one identity and role remain ambiguous.
- Every explicit RFI or Submittal identifier in an answered summary occurs in its top-level or nested exact quotations or their source locators, and every such identifier in a source finding occurs in that finding's own quotations or locators. Pure-numeric RFI leading zeros are formatting-equivalent, while prefixed, compound and Submittal identifiers remain exact; only numeric occurrences inside a fully matched workflow identifier may be grounded by its locator, and every occurrence outside that identifier remains quote-bound even when it has the same value.
- Every explicit RFI Question or Response role in an answered summary or source finding remains paired with the same exact RFI identity in one cited statement or one exact one-identity locator. Answer, Reply and Official Response are Response wording equivalents; the opposite role, another RFI, a multi-identity locator, Request for Information wording alone, Response Date and Response Time cannot supply the claimed role.
- Every explicit bounded workflow disposition in an answered summary occurs in its top-level or nested exact quotations, and every disposition in a source finding occurs in that finding's own quotations; conservative parser-aligned equivalents are allowed, but opposite states and unsupported qualifiers fail closed after cost settlement without retry.
- Within one ordinary answer or one source finding, distinct explicit dispositions in its citations or matching retrieved evidence remain ambiguous and cannot be silently reduced to one state; recognized composite phrases remain one state, exact workflow identities and separate comparison findings stay isolated, and no chronology or authority is inferred.
- Before evidence retrieval or model dispatch, a strict direct status or disposition question naming one exact RFI or Submittal identifier checks the complete run-level workflow index. One distinct explicit primary status from the bounded vocabulary applicable to that workflow type returns a deterministic answer with every source file and detected-or-manual classification origin rendered directly under it; multiple statuses or one malformed multi-state value return insufficient evidence, while a missing, unknown or inapplicable status stays on the ordinary evidence path. A detected source receives an exact parser-text citation only when the matching bounded status phrase is tied to the exact workflow identity in one statement or a fragment or locator scoped to that identity alone; manual corrections, another workflow's phrase and file-level detections without an exact source phrase remain visibly uncited rather than receiving a fabricated quotation. Duplicate sources with one status do not block, Email-derived workflow contexts participate, and no model call or budget action is added.
- An exact unfiltered plural count or list question for RFIs, Submittals and/or Emails uses the complete terminal-run workflow index before evidence retrieval and returns deterministic RFI/Submittal identifiers and unique analyzed Email files with an explicit workflow-index basis. Lists state the complete total, expose at most the first 50 values per category and direct larger inventories to the paginated reviewer; identifier-specific, document-source, date, content and other multi-filter questions do not take this shortcut, and no model call or budget action is added.
- A strict count or list question filtered by one bounded explicit status applicable to every requested workflow type may use the same complete index for RFI and Submittal identifiers. Only groups with one distinct explicit detected-or-manual status supporting the requested disposition enter the confirmed result; groups containing that disposition plus another explicit status are excluded from the result and reported separately as ambiguous, while relationship state, missing documents and reference-only identifiers never manufacture a status. Email-derived workflow contexts participate, lists retain complete totals with at most 50 confirmed and 50 ambiguous values per category, and no evidence retrieval, model call or budget action is added.
- Question task identity normalizes only repeated whitespace so equivalent spacing variants reuse one settled response without changing the submitted prompt; letter case, punctuation and identifiers remain significant, and exact settled task identities from before normalization remain recoverable.
- A question containing an explicit pure-numeric RFI identifier uses the parser's exact leading-zero equivalence through one bounded local identifier supplement; bare numbers, prefixed or compound RFI identifiers and Submittal identifiers remain exact, and no provider call is added.

## FR-QA-PROJECT-KNOWLEDGE-001 · Maintained saved project knowledge

优先级：P0；状态：implemented

Project questions automatically use the newest saved usable analysis without requiring the user to select an Analysis Run.

- When run_id is omitted, the server selects the newest PARTIAL or COMPLETED analysis belonging to the project; an explicit run_id remains supported and project-scoped.
- A read-only project knowledge response reports availability, the selected run, terminal status, document count, active update state and whether the saved snapshot matches current project files, without returning evidence or credentials.
- The browser enables project questions from saved knowledge independently of the Analysis Run history selector and sends no run_id; a newer terminal analysis automatically becomes the default.
- Newly uploaded files do not silently enter old knowledge. The browser identifies the saved analysis as out of date until a new analysis completes, while immutable historical evidence and reviews remain available.
- Automatic selection changes no grounding, exact-citation, project isolation, budget, unresolved-call, cache, failed-document or no-automatic-retry rule.

## FR-QA-EMAIL-SUBJECT-001 · Ground Email subject answers

优先级：P0；状态：implemented

Explicit Email subject answers must remain tied to one complete Subject header from Email evidence in the same answer or source-finding scope.

- Every explicit Subject, Email subject or Subject line claim in an ordinary answer or source finding equals one complete Subject header in that scope's exact quotations from evidence conservatively classified as Email; an RFI or Submittal form's Subject field cannot ground an Email-subject answer. Case, repeated whitespace, outer quotation marks and answer-ending punctuation outside the subject value are formatting-equivalent, while punctuation quoted inside the subject, reply prefixes and the remaining value stay exact; headers cannot be recombined or borrowed across source findings.
- An answered question explicitly asking for one or more Email subjects states each subject explicitly in the answer or corresponding source findings; the validator does not infer current-thread position, sender authority or semantic paraphrase equivalence.

## FR-QA-EMAIL-PARTICIPANT-001 · Ground Email participant answers

优先级：P0；状态：implemented

Explicit Email participant display names must remain complete and paired with the same From, To, Cc, Bcc or Reply-To role from Email evidence in the same answer or source-finding scope.

- Every explicit Email sender, recipient, From, To, Cc, Bcc or Reply-To display-name claim in an ordinary answer or source finding equals one complete same-role header value in that scope's exact quotations from evidence conservatively classified as Email; an RFI or Submittal form's From or To field cannot ground an Email-participant answer. Case, repeated whitespace and outer quotation marks are formatting-equivalent, while names cannot be recombined, role-swapped or borrowed across source findings.
- An answered sender or recipient question using Email evidence states each requested role explicitly as a supported display name or already validated address in the answer or corresponding source findings; the validator does not infer identity from an address, company, title, signature, thread position or authority.

## FR-QA-EMAIL-DATE-001 · Ground Email Date-header answers

优先级：P0；状态：implemented

Explicit Email date and Date-header answers must remain tied to one complete Date header value from Email evidence in the same answer or source-finding scope.

- Every explicit Email date or Date-header claim in an ordinary answer or source finding equals one complete Date header value in that scope's exact quotations from evidence conservatively classified as Email; a Received value, body date or RFI/Submittal form Date field cannot ground the claim. Case, repeated whitespace, outer quotation marks and answer-ending punctuation outside the value are formatting-equivalent, while weekday, date, time, seconds, numeric offset and comments stay exact; headers cannot be recombined or borrowed across source findings.
- An answered question explicitly asking for one or more Email dates or Date headers states each header explicitly in the answer or corresponding source findings. A natural sent-date question states that complete Date header or a separately labelled exact Sent date; the validator does not decode or unfold new source formats, convert time zones, infer delivery or transport time, choose the current thread message or determine authority.

## FR-QA-WORKFLOW-SUBJECT-001 · Ground RFI and Submittal Subject answers

优先级：P0；状态：implemented

Explicit RFI and Submittal form Subject answers must remain tied to one complete Subject value and the same exact workflow identifier in the same answer or source-finding scope.

- Every explicit RFI/Submittal form Subject claim in an ordinary answer or source finding equals one complete Subject value paired with the same exact workflow identifier in that scope's exact quotations from evidence conservatively classified as RFI or Submittal. One exact locator identity may supply an omitted source identifier, while a multi-identity locator, another workflow item or an Email Subject header cannot ground the claim. Case, repeated whitespace, outer quotation marks and answer-ending punctuation outside the value are formatting-equivalent; values cannot be recombined or borrowed across source findings.
- An answered question explicitly asking for one or more RFI/Submittal subjects states each exact workflow identifier and complete subject in the answer or corresponding source findings; the validator does not infer applicability, status, revision, authority or semantic paraphrase equivalence.

## FR-QA-WORKFLOW-SUBJECT-LOCAL-001 · Answer exact workflow Subject questions locally

优先级：P0；状态：implemented

A strict direct Subject question naming one exact RFI or Submittal should reuse the complete workflow index and existing immutable primary-form evidence before model dispatch.

- The shortcut recognizes only bounded English direct-question forms naming one exact RFI or Submittal identifier, uses the complete terminal-run workflow index to select primary form documents, and reads only existing non-vision evidence. One distinct complete explicit Subject returns with exact inline citations and no provider or budget action; different values return insufficient evidence with the conflicting exact quotations.
- Email documents and Email Subject headers cannot satisfy an RFI/Submittal form-Subject shortcut. More than 32 primary documents or 512 Subject-bearing passages fails closed, while no supported Subject falls through to the ordinary evidence-grounded question path. The shortcut adds no parser, provider call, dependency, second index, applicability, status, revision, authority or semantic-paraphrase inference.

## FR-QA-EMAIL-HEADER-LOCAL-001 · Answer unambiguous single-Email headers locally

优先级：P0；状态：implemented

Strict Subject, sender, recipient and Date questions should use existing current-header evidence locally when the complete selected run contains exactly one analyzed Email.

- Bounded English questions for Subject, sender/From, recipient/To or Date use the complete terminal-run workflow index to confirm exactly one analyzed Email, then read only existing non-vision evidence under its current EMAIL > HEADERS locator. One distinct explicit value returns with exact inline citations and no provider or budget action.
- An otherwise-unspecified header question against multiple Email files returns insufficient evidence instead of choosing a message. Quoted history and body text cannot donate a header; conflicting current values, a missing supported header or more than 32 current-header passages fails closed. The shortcut adds no mailbox access, provider call, dependency, parser change, second index, delivery-time, thread-position, identity, authority or semantic inference.

## FR-QA-EMAIL-HEADER-NAMED-001 · Target local Email headers by exact file name

优先级：P0；状态：implemented

A strict header question naming one exact analyzed .eml or .msg file should use that file's existing current-header evidence locally, including in a multi-Email run.

- Bounded English Subject, sender/From, recipient/To and Date questions may name one exact .eml or .msg file. The complete terminal-run workflow index is matched case-insensitively without fuzzy search; a unique match uses only existing non-vision EMAIL > HEADERS evidence and retains exact citations, conflict handling, the 32-passage cap and zero provider or budget action.
- File names containing spaces require quotation marks. Directory separators and wildcard tokens do not enter the local shortcut. Zero matches and duplicate exact file names return insufficient evidence before source evidence is read; the implementation does not scan the filesystem, access a mailbox, add a dependency or infer identity, authority, delivery time or thread position.

## FR-QA-RFI-CONTENT-LOCAL-001 · Answer exact RFI Question and Response requests locally

优先级：P0；状态：implemented

A strict direct request for one exact RFI Question or Official Response should reuse existing parser-scoped project evidence before model dispatch.

- Bounded English direct-question forms name one exact RFI and request either its Question or Response. The complete terminal-run workflow index must identify exactly one primary source with that explicit role, and only existing non-vision passages whose locator contains the same exact RFI identity and role may return locally with exact inline citations and zero provider or budget action.
- Current Email-body RFI scopes may participate, while quoted Email history cannot donate content. Multiple same-role primary sources return insufficient evidence before evidence access; more than 32 matching passages or 18,000 characters fails closed. Missing parser-scoped content falls through to ordinary grounded Q&A, and the shortcut does not interpret requirements, compare items, decide chronology or select controlling authority.

## FR-QA-RFI-SPEC-SECTION-LOCAL-001 · Answer exact RFI Spec Section requests locally

优先级：P0；状态：implemented

A strict direct request for one exact RFI Spec Section should reuse explicit labelled evidence from that RFI's indexed primary sources before model dispatch.

- Bounded English direct-question forms name one exact RFI and request its Spec or Specification Section. Existing non-vision evidence from at most 32 indexed primary files may return locally only when an explicit Spec Section or Specification Section label is scoped to that exact RFI. Matching normalized values across Question, Response and current Email-body primary sources merge with every exact inline citation and zero provider or budget action.
- Different explicit values, another or multiple RFI identities in the same value, quoted Email history, vision narration, more than 32 primary files or more than 32 candidate passages return insufficient evidence or remain on ordinary grounded Q&A as appropriate. Unlabelled CSI numbers cannot supply the field; the shortcut does not infer applicability, hierarchy, revision, status or controlling authority and adds no dependency or second index.

## FR-QA-WORKFLOW-DRAWING-REFERENCE-LOCAL-001 · Answer exact workflow drawing-reference questions locally

优先级：P0；状态：implemented

Strict questions asking which explicit drawings, sheets or details one exact RFI or Submittal references should reuse its indexed primary evidence before model dispatch.

- Bounded English questions name one exact RFI or Submittal and request drawings/sheets, details, or all drawing references. Existing non-vision evidence from at most 32 indexed primary files may return at most eight distinct explicitly prefixed Sheet, Drawing, Dwg or Detail identifiers only when each statement is scoped to that exact workflow item. Equivalent prefixes and dash glyphs deduplicate by complete identifier, current Email-body primary scopes may participate, and every result retains exact inline citations with zero provider or budget action.
- Another or multiple workflow identities in the supporting statement, quoted Email history, vision narration, more than 32 primary files, more than 32 candidate passages or more than eight distinct requested identifiers fail closed. Missing explicit references remain on ordinary grounded Q&A; the shortcut does not infer an unlabelled code, applicability, discipline, revision, chronology, authority or controlling source and adds no dependency or second index.

## FR-QA-WORKFLOW-CLAUSE-REFERENCE-LOCAL-001 · Answer exact workflow paragraph and clause-reference questions locally

优先级：P0；状态：implemented

Strict questions asking which explicit Paragraph, Clause or Article identifiers one exact RFI or Submittal references should reuse its indexed primary evidence before model dispatch.

- Bounded English questions name one exact RFI or Submittal and request Paragraph/Para, Clause, Article or all three reference types. Existing non-vision evidence from at most 32 indexed primary files may return at most eight distinct explicitly typed dotted identifiers only when each statement is scoped to that exact workflow item. Para and Paragraph deduplicate while Paragraph, Clause and Article remain distinct; current Email-body primary scopes may participate, and every result retains exact inline citations with zero provider or budget action.
- Another or multiple workflow identities in the supporting statement, quoted Email history, vision narration, date-shaped or unlabelled values, more than 32 primary files, more than 32 candidate passages or more than eight distinct requested identifiers fail closed. Missing explicit references remain on ordinary grounded Q&A; the shortcut does not infer applicability, hierarchy, revision, chronology, authority or controlling source and adds no dependency or second index.

## FR-QA-SUBMITTAL-FIELD-LOCAL-001 · Answer explicit Submittal fields locally

优先级：P0；状态：implemented

A strict direct request for one exact Submittal Spec Section, Description or Review Comments value should reuse explicit labelled project evidence before model dispatch.

- Bounded English direct-question forms name one exact Submittal and request Spec Section, Description or Review Comments. The complete terminal-run workflow index must identify exactly one primary source, and only an explicit supported label inside non-vision evidence scoped to that exact Submittal may return locally with exact inline citations and zero provider or budget action.
- A value may continue across non-label lines in the same immutable passage. Different explicit values, multiple primary sources and more than 32 candidate passages return insufficient evidence. Current Email-body Submittal scopes may participate, while quoted Email history and unlabeled nearby text cannot. The shortcut does not infer a field from Subject or a CSI number, choose a revision, interpret review intent or decide controlling authority.

## FR-QA-EMAIL-EXTENDED-HEADERS-LOCAL-001 · Answer Cc, Bcc and Reply-To headers locally

优先级：P0；状态：implemented

Strict Cc, Bcc and Reply-To questions should reuse existing current Email-header evidence locally for the only analyzed Email or one exact named Email file.

- Bounded English Cc, Bcc and Reply-To questions extend the existing local Email-header path for both one otherwise-unspecified analyzed Email and one exact named .eml or .msg file. Only existing non-vision EMAIL > HEADERS evidence may return one complete header value with exact inline citations and zero provider or budget action.
- Multiple Email selection, exact filename, missing value, conflicting current values, quoted-history exclusion and the 32-passage ceiling retain the existing fail-closed behavior. Ordinary model-answer validation requires an explicit supported same-role name or address for Cc/Bcc/Reply-To intent. The implementation does not expand distribution lists, infer actual delivery or readership, resolve identities, access a mailbox or add a dependency or second index.

## FR-QA-WORKFLOW-DATE-LOCAL-001 · Answer exact RFI and Submittal dates locally

优先级：P0；状态：implemented

Strict date-role questions for one exact RFI or Submittal should reuse existing workflow-scoped project evidence before model retrieval.

- Bounded English questions for one exact RFI/Submittal Due, Issued, Submitted, Received, Sent, Reviewed, Approved, Revision or Response date reuse the complete workflow index and existing non-vision evidence. One distinct full date returns with exact inline citations and zero provider or budget action.
- Equivalent ISO and English month-name formats merge. Different dates, another workflow identity, ambiguous multi-identity scope, quoted Email history, vision narration, more than 32 primary files or more than 512 role-bearing candidate passages fail closed. Missing, plural, comparison and interpretive questions stay on ordinary grounded Q&A; no deadline, chronology, current-state or authority inference is added.

## FR-QA-EMAIL-WORKFLOW-RELATION-LOCAL-001 · Answer exact Email workflow relationship questions locally

优先级：P0；状态：implemented

Strict questions asking which RFIs or Submittals one Email references, or which Emails reference one exact workflow item, should reuse the complete workflow index and exact current Email evidence before model retrieval.

- Bounded English questions may target the only analyzed Email or one exact named .eml or .msg file and request RFI, Submittal or both relationship types. Indexed primary context and explicit reference members return separately labelled with exact citations from current non-vision EMAIL > HEADERS or EMAIL > BODY text and zero provider or budget action.
- A reverse question naming one exact RFI or Submittal may list at most eight associated Email files, each labelled as primary context or explicit reference and supported by one exact current header/body citation. Pure-numeric RFI leading zeros normalize while prefixed, compound and Submittal identifiers remain exact.
- Multiple otherwise-unspecified Emails, missing or duplicate exact file names, more than eight requested associations, more than 32 current passages per Email, or any indexed association lacking exact current source text returns insufficient evidence. Quoted history, signatures, filenames and vision narration cannot prove a relationship; the implementation does not infer applicability, authority, chronology or contractual effect and adds no dependency or second index.

## FR-QA-WORKFLOW-DOCUMENT-RELATION-LOCAL-001 · Answer exact workflow project-file relationship questions locally

优先级：P0；状态：implemented

Strict questions asking which analyzed project files contain one exact RFI or Submittal identifier should reuse the complete workflow index and exact immutable source text before model retrieval.

- Bounded English questions naming one exact RFI or Submittal may list at most eight associated analyzed files across supported source formats. Primary workflow sources and explicit references are labelled separately, and every returned file has one exact non-vision source quotation for that same normalized identifier with zero provider or budget action.
- Email members reuse the current-header/body evidence boundary, so quoted history and signatures cannot prove a relationship. Non-Email members exclude vision narration. Pure-numeric RFI leading zeros normalize while prefixed, compound and Submittal identifiers remain exact.
- Missing exact source text, duplicate associated file names, more than eight files or more than 32 candidate passages in one file returns insufficient evidence. Filenames and classification labels cannot substitute for source text; the implementation does not infer applicability, authority, chronology or contractual effect and adds no dependency or second index.

## FR-QA-EMAIL-THREAD-LOCAL-001 · Answer exact Email thread membership questions locally

优先级：P0；状态：implemented

Strict questions asking which messages share the indexed thread of one exact named EML or MSG file should reuse the complete hash-only Email workflow index before evidence retrieval.

- Bounded English questions naming one exact .eml or .msg file list at most eight messages in the existing deterministic parent-before-reply order with answer_basis WORKFLOW_INDEX, zero evidence retrieval and zero provider or budget action.
- File matching is exact and case-insensitive; names containing spaces require quotes, and path or wildcard input is never expanded. A standalone indexed message is reported honestly, while unresolved external references are shown only as a count and raw Message-ID values are never exposed.
- Missing or duplicate exact file names, no indexed thread, multiple thread membership, incomplete or duplicate members, more than eight messages, an unsupported state, or an ambiguous duplicate/self-reference/cycle returns insufficient evidence. Ambiguous groups retain bounded file names and parser-generated warnings for review; the implementation does not connect to a mailbox or infer participants, chronology beyond exact headers, delivery, authority or contractual effect.

## FR-QA-EMAIL-ATTACHMENT-RELATION-LOCAL-001 · Answer exact imported Email attachment relationship questions locally

优先级：P0；状态：implemented

Strict bidirectional questions between one exact named Email and its explicitly imported analyzed attachments should reuse the complete attachment-provenance workflow index before evidence retrieval.

- A bounded English question naming one exact .eml or .msg file lists at most eight explicitly imported and analyzed attachment files with each source attachment index and validated MIME type. A reverse question naming one exact imported attachment file lists at most eight parent Email files with the same provenance metadata. Both directions use answer_basis WORKFLOW_INDEX with zero evidence retrieval and zero provider or budget action.
- File matching is exact and case-insensitive; names containing spaces require quotes, while paths and wildcard tokens are rejected rather than interpreted or expanded. An empty result states only that no indexed explicit import-and-analysis relationship exists and never claims the Email contained no attachments.
- Missing or duplicate exact names, duplicate parent names or source indexes, malformed relationship members or MIME metadata, and more than eight results fail closed. Repeated identical import records remain one relationship with an explicit count. Import provenance does not transfer workflow role, status, approval or authority from an Email to its attachment or in reverse; the implementation adds no mailbox access, evidence/model call, dependency or second index.

## FR-QA-WORKFLOW-PARTY-LOCAL-001 · Answer exact workflow party fields locally

优先级：P0；状态：implemented

Strict questions for one exact RFI or Submittal Assigned To, Responsible Party, Submitted By or Reviewed By field should reuse explicit workflow-scoped primary evidence before model retrieval.

- Bounded English questions name one exact RFI or Submittal and one supported party field. Existing non-vision evidence from at most 32 indexed primary files may return locally only when an explicit Assigned To/Assignee, Responsible Party/Responsible, Submitted By/Submitter or Reviewed By/Reviewer label is scoped to that exact workflow item. One distinct complete printed value returns with exact inline citations and zero provider or budget action; equivalent case and whitespace merge, while different values return insufficient evidence with every quotation.
- Current Email-body primary workflow scopes may participate. Email From/To headers, quoted history, signatures, Ball in Court, company mentions, general responsibility prose, another or multiple workflow identity, vision narration, more than 32 primary files or more than 32 candidate passages cannot produce the local answer. Missing explicit fields stay on ordinary grounded Q&A.
- The shared final answer validator preserves the exact workflow identity, field role and complete value in ordinary answers and source findings, rejects cross-item or cross-source borrowing and conflicting cited values, and requires an answered strict party question to state the supported scoped field explicitly. The implementation does not infer identity, responsibility, authority, approval, action ownership or role equivalence and adds no dependency, parser change or second index.

## FR-QA-ACTION-FIELD-LOCAL-001 · Answer exact workflow and Email action fields locally

优先级：P0；状态：implemented

Strict questions for one exact RFI/Submittal or one selected Email Action Required, Next Action or Action Item field should reuse explicit scoped project evidence before model retrieval.

- Bounded English questions name one exact RFI/Submittal and request Action Required/Required Action, Next Action or Action Item. Existing non-vision evidence from at most 32 indexed primary files may return locally only when the corresponding explicit line-leading field is scoped to that exact workflow item. Action Required aliases normalize together while the other roles remain distinct; one complete value returns with exact inline citations and zero provider or budget action, and conflicts fail closed.
- The only analyzed Email or one exact named .eml/.msg file supports the same roles only from current non-vision EMAIL > BODY evidence. Multiple unspecified Emails, missing or duplicate exact names, conflicting values, more than 32 current-body passages, headers, quoted history, signatures and ordinary prose fail closed. Recognized strict workflow and Email questions with no supported explicit field do not fall through to inferred prose answers.
- The shared final-answer validator preserves the exact workflow identity, field role and complete value, or the exact current Email-body field role and value, in ordinary answers and source findings. It rejects omissions, role swaps, cross-item or cross-source borrowing and conflicting cited values. The implementation does not infer applicability, responsibility, approval, chronology, authority or action ownership and adds no dependency, parser change or second index.

## FR-QA-WORKFLOW-METADATA-LOCAL-001 · Answer exact workflow coordination metadata locally

优先级：P0；状态：implemented

Strict questions for one exact RFI or Submittal Ball in Court, Priority, Discipline or Location field should reuse explicit workflow-scoped primary evidence before model retrieval.

- Bounded English questions name one exact RFI or Submittal and one supported coordination field. Existing non-vision evidence from at most 32 indexed primary files may return locally only when an explicit line-leading Ball in Court/Ball-In-Court, Priority, Discipline or Location label is scoped to that exact workflow item. Each role remains distinct; one complete value returns with exact inline citations and zero provider or budget action, while equivalent Ball-in-Court punctuation and case/whitespace merge and different values fail closed.
- Current Email-body primary workflow scopes may participate. Assigned To, Responsible Party, Email Priority/Importance headers, quoted history, signatures, ordinary prose, another or multiple workflow identity, vision narration, more than 32 primary files or more than 32 candidate passages cannot produce the local answer. A recognized strict question with no supported explicit field fails closed before ordinary retrieval.
- The shared final-answer validator preserves the exact workflow identity, field role and complete value in ordinary answers and source findings. It rejects omissions, role swaps, Email-prefixed substitution, cross-item or cross-source borrowing and conflicting cited values. The implementation does not infer responsibility, applicability, routing, urgency, trade, place, approval, chronology, authority or action ownership and adds no dependency, parser change or second index.

## FR-QA-WORKFLOW-IMPACT-LOCAL-001 · Answer exact workflow impact fields locally

优先级：P0；状态：implemented

Strict questions for one exact RFI or Submittal Cost Impact, Potential Cost Impact, Schedule Impact or Potential Schedule Impact field should reuse explicit workflow-scoped primary evidence before model retrieval.

- Bounded English questions name one exact RFI or Submittal and one supported impact field. Existing non-vision evidence from at most 32 indexed primary files may return locally only when an explicit line-leading Cost Impact/Potential Cost Impact or Schedule Impact/Potential Schedule Impact label is scoped to that exact workflow item. Potential labels normalize within the matching role, cost and schedule remain distinct, one complete value returns with exact inline citations and zero provider or budget action, and different values fail closed.
- Current Email-body primary workflow scopes may participate. Email-prefixed fields, headers, quoted history, signatures, ordinary cost or delay prose, another or multiple workflow identity, vision narration, more than 32 primary files or more than 32 candidate passages cannot produce the local answer. A recognized strict question with no supported explicit field fails closed before ordinary retrieval.
- The shared final-answer validator preserves the exact workflow identity, impact role and complete value in ordinary answers and source findings. It rejects omissions, role swaps, Email-prefixed substitution, cross-item or cross-source borrowing and conflicting cited values. The implementation does not estimate, calculate, convert, predict or infer cost, duration, applicability, responsibility, approval, chronology, authority or contractual effect and adds no dependency, parser change or second index.

## FR-QA-WORKFLOW-FIELD-LOCAL-001 · Answer exact company-specific workflow fields locally

优先级：P0；状态：implemented

Strict questions naming an otherwise-unregistered printed field and one exact RFI or Submittal should read only an exact same-label value from bounded workflow-scoped primary evidence before model retrieval.

- A bounded English question uses the literal form the <label> field for one exact RFI or Submittal. The label contains one through six English words and at most 60 characters; known Status, Subject, date, action, impact, party and reference terms remain on their dedicated contracts. Existing non-vision evidence from at most 32 indexed primary files may return one complete one-line value locally only from a line-leading exact case/whitespace-equivalent label under that workflow locator, with exact citations and zero provider or budget action.
- Current Email-body primary workflow scopes may participate. A shorter, synonymous or Email-prefixed label, general prose, another or multiple workflow identity, Email headers, quoted history, signatures, vision narration, more than 32 primary files or more than 32 candidate passages cannot produce the local answer. Missing labels and conflicting explicit values fail closed before ordinary retrieval.
- The shared final-answer validator preserves the exact requested label, workflow identity and complete value in ordinary answers and source findings. It rejects omission, invention, cross-item or cross-source borrowing and conflicting cited values. The implementation adds no synonym expansion, semantic interpretation, dependency, parser change or second index.

## FR-QA-CROSS-WORKFLOW-RELATION-LOCAL-001 · Answer exact RFI and Submittal relationships locally

优先级：P0；状态：implemented

Strict questions asking which Submittals one exact RFI references or which RFIs one exact Submittal references should read only explicit relationship fields from bounded workflow-scoped primary evidence before model retrieval.

- Bounded English questions name one exact RFI or Submittal and request only the opposite workflow kind. Existing non-vision evidence from at most 32 indexed primary files may return locally only from an explicit line-leading Related RFI(s), RFI Reference(s), Linked RFI(s), Related Submittal(s), Submittal Reference(s) or Linked Submittal(s) field under that exact target locator. Up to eight exact related identifiers return in deterministic order with exact inline citations and zero provider or budget action; pure-numeric RFI zero padding normalizes and compound identifiers remain exact.
- Current Email-body primary workflow scopes may participate. General co-occurrence, workflow-index membership, filenames, classification, Email headers, quoted history, signatures, vision narration, arbitrary prose values, another or multiple target identity, more than 32 primary files, candidate passages or explicit fields, or a ninth related identifier cannot produce an answer. Explicit None, N/A and Not applicable may support none listed; an empty marker combined with a nonempty value fails closed before ordinary retrieval.
- The shared final-answer validator preserves the exact target identity, relationship direction and every related identifier in ordinary answers and source findings. It rejects omission, invention, wrong direction, cross-target or cross-source borrowing and conflicting empty/nonempty citations. The implementation adds no dependency, parser change or second index and does not infer reciprocity, applicability, authority, chronology, precedence or contractual effect.

## FR-QA-ANSWER-PROVENANCE-001 · Show how each project answer was produced

优先级：P0；状态：implemented

Every project-question response should expose and display whether it came from the workflow index, local project evidence, a cited model answer, retrieval-only Mock mode, or no matching evidence.

- Every returned project-question result carries one stable answer_basis value. Existing deterministic paths retain WORKFLOW_INDEX or LOCAL_PROJECT_EVIDENCE; successful provider or recovered-cache answers use MODEL_PROJECT_EVIDENCE; Mock retrieval uses RETRIEVAL_ONLY; and an empty retrieval uses NO_MATCHING_EVIDENCE. A workflow-index conflict stays WORKFLOW_INDEX.
- The browser renders an English answer-path badge before the answer text. Deterministic, retrieval-only and no-evidence badges state that no model call occurred; a fresh model result states that a model call was used; and a recovered settled response states that no new model call occurred by combining MODEL_PROJECT_EVIDENCE with the existing cached flag.
- The provenance label is response metadata only. It adds no retrieval, model dispatch, retry, budget action, parser, dependency or second index; it does not claim that a model answer is correct beyond the existing exact-citation validation.

## FR-MODEL-BUDGET-REMOVAL-001 · Run without a CIRP project budget module

优先级：P0；状态：implemented

CIRP model dispatch is not gated by a project budget, configured price, exchange rate, or cumulative cost total; provider-account spending is managed outside CIRP.

- Project creation, project settings, model settings, run status, analysis results and exports expose no project budget, rate input, price confirmation, monetary cost total or budget update endpoint.
- A legacy budget account or old budget-frozen state cannot block a new eligible model call. Legacy tables and rows remain inert and readable for upgrade compatibility; existing project files, reviews, analysis records and model-call history are not deleted or reset.
- Provider authorization, exact task idempotency, unresolved-call blocking, explicit reconciliation, no automatic retry, request-size limits, serial provider pacing and the 24-hour run boundary remain independent call-safety controls.
