# OpenAI Responses、DeepSeek 文本/视觉与 Google Gemini 文本适配器

## In-app model settings

Local users can use the dedicated **Settings** section on the main application page, reached from the sidebar or the **Model API settings** header shortcut, to choose Mock, the official OpenAI Responses API, DeepSeek, Gemini, a custom OpenAI-compatible HTTPS API, or a loopback local model. This is not a modal or separate setup server. All model HTTP still uses the existing gateway; this is not a second dispatch path. Official provider choices keep fixed endpoints. OpenAI accepts a user-entered exact model ID; DeepSeek offers the published V4.1 Flash and independent V4 Pro IDs plus an explicit Reference QA thinking level; Gemini retains its fixed model. A custom remote endpoint requires HTTPS and a key; only `localhost` or a literal loopback address may use HTTP and omit the key. Image input, bounded DeepSeek Reference thinking and native Reference JSON Schema output remain explicit capabilities. CIRP does not discover or infer custom-provider support. Custom routes receive no DeepSeek/Gemini-specific reasoning parameter. CIRP has no project budget, price, rate, or monetary dispatch gate.

Applying settings performs no model call. It is blocked by an active analysis, active semantic verification, or any unresolved model call. The local single-user service then restarts on the same port. Keys are never returned to the browser after submission and are persisted only when the user selects Windows current-user DPAPI storage. The non-secret active profile permits the normal launcher to reuse the selection; `--mock` explicitly bypasses it.
**规格版本：** 0.2.6
当前 `app/gateway.py` 在同一安全出口中实现既有OpenAI风格Chat Completions和新增官方OpenAI Responses传输；不包含秘密，也不自动升级模型。DeepSeek设置使用官方`deepseek-flash`（V4.1 Flash）或独立`deepseek-v4-pro`（V4 Pro）。Flash可显式启用图像，Pro保持纯文本；历史`deepseek-v4-flash`文本配置与`deepseek-v4-flash-vision-exp`视觉记录继续可读，不重写旧评估或调用身份。

## DeepSeek V4.1 Flash 与 V4 Pro 精确选择

本机设置页固定DeepSeek官方基址`https://api.deepseek.com`，提供`deepseek-flash`和`deepseek-v4-pro`两个当前官方ID。新Flash配置可由用户显式开启有限选中页面图像；Pro按官方能力保持纯文本并在HTTP前拒绝图像输入。两者都使用Chat Completions和`json_object`；Reference QA思考默认关闭，也可显式选择`low`、`high`或`max`，精确模型ID和推理模式会冻结进Reference评估任务。保存或切换配置不探测供应商、不产生模型调用，也不会修改已存在的结果、调用或评估任务。

官方文档把`deepseek-v4-flash`和`deepseek-v4-flash-vision-exp`列为会路由到V4.1 Flash的旧别名。CIRP接受旧文本别名并在新的活动配置中规范化为`deepseek-flash`；历史数据库行保持原值。当前官方模型列表独立列出`deepseek-v4-pro`，因此新的活动配置允许该精确ID。未知DeepSeek模型、非官方端点和Pro图像输入仍在HTTP前失败。同快照Pro对照仍必须冻结相同run、snapshot、问题顺序和selector，并单独进行正确性裁决。

## 有边界的 Reference QA 思考

`none`保留既有`thinking={"type":"disabled"}`快速路径。选择`low`、`high`或`max`后，只有QA V3证据决策发送`thinking={"type":"enabled"}`和对应`reasoning_effort`；抽取、视觉、核验、QA V2和其他任务继续使用原推理策略。思考决策输出上限为8,000 token，仍受三次决策、证据合同、不自动重试、未决账单和请求大小闸门约束。

供应商返回的`reasoning_content`不会进入答案合同、数据库response、执行收据、报告或浏览器；收据继续固定`chain_of_thought_included=false`。供应商usage中的reasoning token仍作为输出用量记录。授权的20题Flash A/B显示Low thinking把合同有效结果从14/20提高到17/20、跨项目现场题的可用安全非答案从4/5提高到5/5，但平均每题从6.82秒增至13.60秒，输出token增至7.13倍。因此它是面向不确定、多字段、算术或安全关键问题的升级路径，而不是默认全量模式；详见`reports/kapolei_field_worker_cot_flash_ab_2026-10-03.md`。

## OpenAI Responses API（新功能）

设置页中的 **OpenAI Responses API** 是独立、可选的新增provider，不替换DeepSeek、Gemini、自定义接口、Mock或任何旧功能。它把Key固定发送到`https://api.openai.com/v1`，模型ID由用户准确填写；保存设置本身不调用模型。只有用户之后显式运行分析或问题任务才可能发送所选证据并产生供应商费用。

请求固定使用`POST /responses`、`store=false`、`max_output_tokens`和`text.format`，不启用tools、conversation或`previous_response_id`，因此CRIP不会借用供应商侧会话状态。既有文本消息转换为Responses `input`；只有用户显式开启图像能力后，Reference选择器已选中的有限页面图像才从现有`image_url`部件转换为`input_image` data URL。完整项目、未选择页面和本地数据库不会因此自动上传。

Responses返回在本机转换为既有Gateway内部格式：只接收完成响应中的`output_text`，`input_tokens`/`output_tokens`转换为现有用量字段；reasoning项被忽略且不保存。拒答、未完成、意外输出类型、缺少可信usage或未通过本地答案契约的结果都不会发布。若供应商已返回可信usage，调用会以安全诊断结算；不保存拒答正文或隐藏推理，也不自动重试、改模型或退回另一协议。`api_protocol`进入冻结评测profile和新Responses任务身份，而历史Chat Completions身份保持原样。

该实现依据官方[Responses迁移说明](https://developers.openai.com/api/docs/guides/migrate-to-responses?api-mode=responses.html)、[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)和[图像输入说明](https://developers.openai.com/api/docs/guides/images-vision)。当前验证全部使用本地合成MockTransport，没有调用OpenAI，也不证明任何具体模型可用、费用、答案质量或固定15问提升。

## Autodesk and Procore document sources
These are read-only document sources, not model providers. The local Files panel accepts a customer-generated bearer token, lists project folders and imports only a selected file through the existing project-capacity, 4 MiB chunk, SHA-256 and deduplication path. CIRP never returns the token in status responses, optionally stores it with Windows current-user DPAPI, sends it only to the fixed official API host, and removes it before following a cross-host signed download. Forgetting the connection deletes only CIRP's local copy; it does not revoke the token at the provider.

The Autodesk implementation follows the official [Data Management API](https://aps.autodesk.com/data-management-api), Autodesk's [three-legged authentication guidance](https://get-started.aps.autodesk.com/tutorials/hubs-browser/auth/) and the documented [BIM 360/Autodesk Docs storage URN download pattern](https://aps.autodesk.com/blog/best-practice-uploading-and-downloading-files-bim360-docs). The Procore implementation follows the official [OAuth authorization-code documentation](https://procore.github.io/documentation/oauth-auth-grant-flow) and [Project Folders and Files API](https://developers.procore.com/reference/rest/project-folders-and-files?version=1.0). Autodesk publishes official Node/.NET SDKs and OpenAPI definitions, but adding another runtime for two bounded read paths would increase deployment work; this increment therefore reuses the project's installed open-source `httpx` client instead of adding an SDK.

Interactive OAuth, automatic refresh, provider-side revocation, recursive bulk import, change polling, continuous synchronization and live-tenant validation are not implemented. Automated validation uses synthetic `httpx.MockTransport` responses only; it makes no live Autodesk/Procore request and never performs a remote write or delete.

## DeepSeek V4 Flash（当前选择）
Windows运行`start-deepseek-live.ps1`；需要网页输入时运行`scripts/deepseek_local_setup.py`。两种入口都固定官方基址`https://api.deepseek.com`与模型`deepseek-v4-flash`，显式发送`thinking={"type":"disabled"}`。用户选择保存后，Key使用Windows DPAPI当前用户加密文件保存并在后续启动时自动加载；不写明文`.env`、网址、命令行、日志或Git。可用`--replace-key`强制重新输入、`--forget-key`删除。启动本身不调用API，只有用户点击“开始分析”后才可能计费。

DeepSeek入口同时启用精确视觉模型`deepseek-v4-flash-vision-exp`。PDF/图片先在本机转为最长边2048的PNG，通过data URL发送；原文件不改。每页任务和图片哈希进入缓存键，视觉与文本共用未决调用闸门、串行间隔、usage记录及不自动重试规则。视觉输出必须通过`vision-result.schema.json`，且强制待人工审核；只描述可见内容，不执行文档中的指令，不允许模型替代CAD几何量算。

DeepSeek入口使用1秒最小请求开始间隔作为本机稳定性缓冲，仍不自动重试失败请求。CIRP 不保存供应商单价或项目费用上限；实际扣费、赠送余额与税费以用户的 DeepSeek 账户为准。

## Google Gemini 3.6 Flash（推荐用安全启动器）
Windows运行`start-gemini-live.ps1`。首次使用时在仅绑定127.0.0.1的密码页确认数据传输并输入Key；可选择由Windows DPAPI为当前用户加密保存，后续自动加载。Key不写明文`.env`、网址、命令行、日志或Git；启动子进程后设置页关闭。启动服务本身不调用API，只有用户点击“开始分析”或提交项目问题后才可能计费。

当前适配固定Google官方OpenAI兼容基址`https://generativelanguage.googleapis.com/v1beta/openai`和模型`gemini-3.6-flash`，避免把Google密钥发送到其他主机。Gemini 3不能关闭推理，因此请求显式使用`reasoning_effort=minimal`，不请求或保存思维摘要。文本联合抽取`max_tokens<=8000`，其中包含可见输出和内部推理生成量；usage结算在`total_tokens-prompt_tokens`大于`completion_tokens`时采用较大值，避免漏记隐藏推理。

CIRP 不保存 Gemini 单价、汇率或项目费用上限。账户实际是否免费、税费和结算币种以 Google 账单为准。

## DeepSeek手动配置（不推荐保存Key）
在本机复制.env.example为.env，按实际接口填写：
```text
CIRP_PROVIDER=deepseek
CIRP_LIVE_API_ENABLED=true
CIRP_API_BASE_URL=https://api.deepseek.com
CIRP_API_KEY=<只在本机填写>
CIRP_CHEAP_MODEL=deepseek-v4-flash
```
不要把示意占位符直接启用。本版DeepSeek live只接受精确官方基址`https://api.deepseek.com`和模型`deepseek-v4-flash`，Gemini也只接受文档中的精确官方基址与模型；程序自行追加`/chat/completions`。当前不支持中转API，避免把供应商Key发送到其他主机。Key为空或live开关关闭时，启动真实分析会暂停而非偷偷切换服务。

`python scripts/doctor.py`仅离线检查，不调用供应商。用户启动真实分析代表该次请求可能计费；先用小授权样例核实接口，不先上传整个10GB项目。应用没有自动读取供应商账单的能力；未知调用必须由用户先在供应商后台核实，再从本机网页逐条登记。

## 实际请求和保护
DeepSeek发送`thinking=disabled`；Gemini发送`reasoning_effort=minimal`。两者均使用Chat Completions `response_format=json_object`。自定义OpenAI兼容配置默认使用该模式；用户明确选择后，Reference QA V2/V3使用带稳定名称、`strict=true`和完整任务Schema的`response_format=json_schema`。官方OpenAI则通过Responses `text.format`承载同一JSON object或strict JSON Schema合同。CIRP在发送前检查受支持的Schema子集，不兼容即本地失败，不探测供应商、不降级、不自动重试；其他任务仍使用各自协议的JSON object。文本联合抽取最多8,000生成token和32,000 UTF-8输入字节工程估算；页面视觉仍最多2,000生成token，语义核验仍最多1,400，且每类实际值都受运行时Settings更低上限约束。请求体、缓存参数和预算预留使用同一个任务上限；32,000字节不是实际token计数。无自动高级模型路由；文本提取、页面视觉和语义核验的同一任务族均最多3次显式请求，未知账单未清时不重试。人工重排后缀只作为任务族generation记账，模型输入和证据范围验证始终使用原始Evidence ID。抽取可保守去除有界JSON展示包装；根级多余字段、等值标量字符串、重复局部键、悬空父引用及无法完整验证的原子项只允许降级修复，并强制输出`TRUNCATED`进入人工审核。空/错/多对象JSON、未知前后文字、越界或缺失证据、审核结论字段、未知嵌套字段及长度终止仍不发布。DeepSeek意外生成思考内容则暂停。真实请求可能计费，即使响应不合格。

语义核验响应只对两类无事实新增的格式偏差做安全规范化：移除已知的输入回显字段，或把过长理由替换为有界说明。凡经规范化的字段一律降为`NEEDS_CONTEXT`，不能产生`SUPPORTED`或`CONTRADICTED`结论；未知字段、非法路径、证据越界和非逐字引用仍以`MODEL_OUTPUT_REJECTED`失败关闭。发布候选的审核摘要限制为400字符，完整属性仍保存在原证据抽取中。若付费调用已完成而仅本地发布阶段失败，恢复只重新执行本地发布和未完成核验，不重新发送已完成的任务。

发送前原子预留，usage按确认上限费率核销，不重复加reasoning_tokens。提取、页面视觉和语义核验共享同一付费终态协议：可信usage、实际费用、安全供应商请求号、经本地契约验证/规范化的结果和缓存一次提交；不合格结果一次提交费用与安全诊断，不保存原始不合格正文。提交后的进程中断从同一task终态恢复；失败task默认阻断再收费。人工对账后的显式Resume或显式新建核验任务会追加不可变`RECOVERY_GENERATION_AUTHORIZED`审计事件，并使用`<task-family>:manual-requeue:<generation>`新键；gen0至gen2合计最多三次。视觉契约失败的人工重排仍在文档任务中持久化`billing_generation`和旧尝试历史。输入按UTF8字节保守估计并限量，非精确分词器；TXT、DOCX和PDF文字片段最多1,600字符且最多2,200 UTF-8字节，中文长行按完整Unicode字符拆分并保留原文，为Prompt、Schema和定位元数据留出空间。极端定位元数据或未来Prompt增长仍可能触发发送前门禁，此时标待细分而不收费。缓存是版本化应用缓存，缓存命中零HTTP；供应商折扣不预先假定。

HTTP非成功响应不再只保存异常类名：400、401、403、404、408、429和5xx映射为短诊断类别，供应商返回的短机器状态码可保留；仅接受严格格式的`Retry-After`和`x-request-id`/`x-goog-request-id`。不持久化错误响应正文、请求正文、Authorization或密钥。429/5xx在界面中明确为配额/限流或暂时故障，但仍按费用未知处理、保留预留并等待人工确认，不自动重试。400/401/403/404同样暂停，避免用重试掩盖请求、密钥、权限或模型配置问题。

Gemini安全入口把`CIRP_MIN_REQUEST_INTERVAL_SECONDS`设为15，DeepSeek安全入口设为1；Gateway在提取与核验的共同HTTP出口串行控制请求开始时间，等待后才重新检查运行状态和预留预算。该间隔只降低突发请求，不等于供应商允许继续，也不会自动重试429/5xx。可配置范围为0至300秒，非有限值或越界会阻止live配置。

未知调用对账是显式本地操作。网页显示安全诊断和预留金额，但不显示供应商正文。用户必须勾选已查看供应商账单，然后选择“未收费”（实际金额必须为0）或“已收费”（填写大于0的实际人民币金额）。两种结论都保留原`model_call`和原对账事件；未收费释放预留，已收费把实际金额计入累计支出，超过原预留时继续冻结后续付费。保存对账不会自动重试：随后由用户点击Resume（分析内文本/视觉/核验）或新建语义核验任务，服务端才把该对账调用绑定到下一恢复代次。真实`PENDING`证据、`PAUSED_PROVIDER`视觉页或当前运行核验族无法唯一关联、缺少对账事件、代次已存在或任务族已有3次调用时，操作以409/暂停状态失败关闭，不发HTTP。系统不会删除调用或重置项目预算。同一项目仍有任一未决调用时，网页开始按钮、创建/处理/恢复分析、Gateway及预算预留均禁止新增付费工作；清零后恢复原Run，已完成文档与已抽取/待审核片段不会重跑。同一Tag材料采用稳定组键；恢复新增证据改变候选时保留旧审核事件、递增版本并重新待审，不产生第二条合并记录。服务关停把排队/运行核验标为`INTERRUPTED`，等待请求间隔期间也不会在关停后继续预留或发送。

离线HTTP回归仍全部使用MockTransport。另在2026-09-11经用户在本机一次性页面输入Key完成三次有界live验证。合成项目：`gemini-3.6-flash`、`reasoning_effort=minimal`、839输入token、35输出token、账本支出¥0.007605、未知调用0。真实59页PDF项目：上传和文字解析成功，首次运行在6/144后因请求未完成进入`PAUSED_PROVIDER`；用户授权重试在7/144后再次因`HTTPStatusError`暂停。PDF项目累计12条调用记录、已结算¥0.526795、2条未决并保留¥0.237238；程序没有自动重试。聊天中出现的Key没有被诊断命令读取或写入文件；如果一次性页面使用的是同一把已在聊天中暴露的Key，应先撤销并轮换。这些结果均没有施工准确率/完整吞吐保证；图像/DWG仍只记录未处理，不能把图片交给文本模型冒充已读。

同日用户在本机安全页面输入DeepSeek Key后，新建隔离项目运行同一59页PDF，不修改原Gemini项目或未决账本。`deepseek-v4-flash`完成144/144文字片段和逐字段核验：243次调用、323,913输入token、127,620输出token，本机账本结算¥3.109899，未决0、最终预留0；生成60条材料、16条检查、1条冲突和213条缺失/未处理记录，全部保留待人工审核。运行终态`PARTIAL`来自已声明的视觉/CAD/几何能力边界。供应商最终账单、语义准确率和施工适用性仍需另行核对。

随后独立绘图视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`使用受限视觉模型完成59/59页，产生220条片段（50个`EXTRACTED`、170个`NEEDS_REVIEW`）、599次调用和379条待审核记录（79材料、26检查、274缺失）；账本结算¥8.047433，预留、未知和未决调用均为0。逐字段核验为31条`SUPPORTED`、9条`PARTIAL`、13条`UNSUPPORTED`、52条`PENDING`及274条`NON_DOCUMENT`。此结果证明受限页面视觉链路、契约收口与本机记账；`PARTIAL`不代表施工准确率、自动材料净量或供应商最终账单已获确认。

2026-09-13合并运行`RUN-7a2f9a8c090d40bdb802fdde17747d56`在同一DeepSeek项目中完成指定59页图纸与833页规格书：1,964条证据、222/222视觉页、10,926条四类候选和10,926份逐记录核验均已收口。8,501次调用全部进入`SETTLED`或`SETTLED_ERROR`终态，账本支出¥107.047278，预留/未知/未决均为0；`SETTLED_ERROR`只表示已按可信usage结算但被本地严格契约拒绝，不能当作有效模型输出。完整JSON/XLSX导出已经实际读取和交叉核对。所有记录仍待人工审核，PDF几何未形成材料净量候选，`PARTIAL`边界不变。

该运行还暴露旧网页每2.5秒重叠加载完整290条记录的问题，造成CPU与内存放大；现已串行化轮询，活跃运行不加载完整记录，终态只加载一次，审核或手动核验后再强制刷新。该前端修复不发模型请求，也不改变已结算结果。
