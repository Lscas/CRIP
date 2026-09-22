# v0.2.6 原句引用与核验：实际验证报告

## 实际交付
在`v0.2.5-ui-language`基础上实现字段原句、独立语义核验接口、修改后失效、引用显示与导出。当前证据包没有Git工作树且未推送远端。2026-09-11已在用户Windows本机分别启动Gemini与DeepSeek live服务；当时密钥由用户通过一次性本机密码页输入，未由诊断命令读取。2026-09-12用户明确要求避免重复输入后，新增Windows DPAPI当前用户加密保存；不写明文`.env`、网址、命令行、日志或Git。

## 本轮结果
| 检查 | 实际结果 |
|---|---|
| Full offline Python regression | 485 passed, 0 failed, 0 errors, 0 skipped |
| Verification module tests | 72, included in the 485 above |
| 本机API安全启动 | 覆盖Gemini兼容及DeepSeek V4 Flash官方基址、模型、非思考配置、保守费率、一次性页面、DPAPI当前用户加密保存、自动加载、更换/删除边界及密钥不入参数/明文文件/日志 |
| 原有显示语言组件 | 23项通过 |
| 原有Cloudflare Worker | 11项通过 |
| 规格、Schema、Prompt与追踪 | 100项需求、15个Schema、10份实际Prompt，检查通过 |
| JavaScript语法、Python编译 | 通过 |
| 无网络浏览器DOM集成 | 15项通过，JavaScript错误0 |
| PDF图面来源预览 | 合成PDF返回200/PNG，定位实际词区域，原件字节不变 |
| OCR/视觉/CAD增量 | RapidOCR/ONNX本机OCR不调用API；指定59页PDF离线得到144个文字层片段和17个OCR片段，53至58页已补出，平均OCR置信度0.925213；绘图运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59个视觉任务，产生220条片段（50个`EXTRACTED`、170个`NEEDS_REVIEW`）。10页有比例标定但只输出整页原始几何审计，`material_quantity`均为空。DXF测试及GNU LibreDWG合成DWG转换通过，量算候选均待人工审核 |
| 真实模型或外部付费调用 | Gemini合成项目1次调用，账本支出¥0.007605；Gemini真实PDF两次局部运行累计12条调用、已结算¥0.526795、2条未决并预留¥0.237238。独立DeepSeek文字项目完整处理144/144文字片段，243条调用全部结算¥3.109899、未决0；独立视觉运行完成599条调用、账本结算¥8.047433，预留/未知/未决均为0 |
| HTTP错误诊断热修复 | 400、401、403、404、408、429、503、网络超时、非法JSON、不可信诊断头及长短Key回显离线覆盖；正文、请求内容和密钥不入诊断 |
| 对账与稳定恢复 | API与真实DOM离线验证必选账单确认、0/实际金额约束、审计事件、项目级全入口门禁、只处理PENDING片段；节流关停不发送；聚合记录身份与审核失效稳定 |
| 长中文输入 | 1,560字符级中文复现改为按2,200 UTF-8字节安全拆分；原文拼接完全一致，各片段连同当前Prompt、Schema和元数据均低于6,000发送门槛 |
| 图纸与规格书合并live终验 | `RUN-7a2f9a8c090d40bdb802fdde17747d56`完成2/2文件、1,964/1,964证据、222/222视觉页和10,926/10,926逐字段核验；8,501次DeepSeek调用全部进入已结算终态，支出¥107.047278，预留/未知/未决均为0 |
| Large saved-run export | Fixed-English reviewer JSON/XLSX: JSON 30,795,937 bytes, XLSX 2,555,273 bytes; five core sheets, 10,926 items, 93/93 complete conflicts, 218 distinct A/B comparison rows, zero duplicate conflict statements, and zero identical A/B rows. Visible Chinese, non-English punctuation, internal IDs, machine-style underscore codes, raw `null`, unsupported private-use glyphs, duplicated quantities, generic visual placeholders, formatting code, formulas, hyperlinks, and whitespace-only strings all validate at zero. All five sheets render readably. |

本轮最新完整Python与规格日志：`reports/local/all-0.log`、`reports/local/all-1.log`。`reports/evidence/`保留原证据核验交付时的历史快照；其中的计数早于Gemini适配，不作为当前全量结果。耗时是测试耗时，不是施工项目分析性能。合成调用运行ID为`RUN-b65d0ffe27a14381b22282f03eca7108`；状态PARTIAL、未知调用0、预算未冻结。

真实PDF项目ID为`P-3ca409a98fd442d69d67c3384e0aad8f`。原件和上传文档SHA-256均为`7c5c76661c82edb5e9480f8e53b877e19dce7b19f180c9d8b1ae20bb61cc8124`。文件59页、12,489,262字节；本地解析得到144个片段，其中53页有可用文字层，53至58页没有足够文字层。首次运行`RUN-eda2fdf104a64e5e96cd6dbbb2531ff8`在6/144后因供应商请求未完成进入`PAUSED_PROVIDER`。用户明确表示可继续使用API后，新建第二次运行`RUN-6ddb084f02df4f5395917fa04b8fd2b4`；该运行在7/144后再次因`HTTPStatusError`进入`PAUSED_PROVIDER`：1个片段完成抽取、6个片段待审核、137个片段未处理。第二次运行共发布6条`MISSING/PENDING`记录，未发布材料、QA或冲突记录；这只能说明当前处理范围没有产生这些候选，不能推断整份图纸不存在相应要求。PDF项目累计记录12条模型调用、12,754输入token、11,497输出token、已结算¥0.526795、2条未决调用和¥0.237238保留预留，可用¥299.235967，预算未冻结。重试由用户明确授权而非程序自动触发；重复HTTP错误后没有继续新建运行，也没有OCR、CAD或视觉调用。

## 图纸与规格书合并终验
项目`P-fb63b772ea5841baa7b68bc84ee9651a`中的运行`RUN-7a2f9a8c090d40bdb802fdde17747d56`同时处理59页图纸和833页规格书。图纸为12,489,262字节，SHA-256为`7c5c76661c82edb5e9480f8e53b877e19dce7b19f180c9d8b1ae20bb61cc8124`；规格书为7,472,888字节，SHA-256为`acc97e67c21ea5eedd10271812cff54998b9d3afcab7ff1b843d7727f45faa0a`。两份文档均完成本轮处理并保持`PARTIAL`能力边界。保存1,964条证据，其中1,724条文字层、18条OCR、222条视觉；1,618条为`EXTRACTED`，346条为`NEEDS_REVIEW`。222/222视觉页完成；221页保留PDF几何审计，其中10页检测到比例标定。没有产生材料净量候选，不能将整页线段/矩形统计当作施工数量。

运行发布10,926条待人工审核记录：3,224条材料、1,785条检查/测试、93条冲突、5,824条缺失/非文档说明，人工状态全部为`PENDING`。逐记录核验为2,472条`SUPPORTED`、603条`PARTIAL`、1,247条`PENDING`、668条`UNSUPPORTED`、112条`CONTRADICTED`和5,824条`NON_DOCUMENT`；60,040个字段状态为12,558个`SUPPORTED`、6,328个`NEEDS_CONTEXT`、1,080个`UNSUPPORTED`、125个`CONTRADICTED`及39,949个`NON_DOCUMENT`。这些是模型候选的证据一致性状态，不是人工批准或施工正确性结论。

全程8,501次DeepSeek调用：7,942次`SETTLED`、559次`SETTLED_ERROR`；9,973,782输入token、4,784,792输出token，本机账本支出¥107.047278，预留、未知和未决均为0，可用预算¥192.952722。`SETTLED_ERROR`表示供应商响应已产生可信usage并结算，但本地严格契约拒绝该结果；没有把失败调用伪装成成功，也没有因本地恢复重复发送已经完成的任务。

完整JSON为374,631,843字节，完整XLSX为12,989,227字节。JSON解析确认10,926个唯一记录ID、1,964个唯一Evidence ID、10,926份核验和13,978个共享引用定位；候选Evidence、字段引用、引用目录和精确原文偏移均无断链。XLSX包含10个可见工作表，四类记录、60,040个字段核验、1,964条证据、221页几何审计及13,978条引用的行数与JSON/SQLite一致；84,934个工作表内链接全部能落到有效引用或证据行，无外部链接、宏、公式执行或超过Excel单元格上限的内容。JSON/XLSX导出分别耗时37.150秒和59.523秒；该计时只说明本机大结果可导出，不是分析吞吐承诺。

The earlier 374 MB / 10-sheet file remains as a pre-refactor historical snapshot. The current saved run now has a fixed-English reviewer edition: material or equipment names, quantities, and units are separate; English numeric, parenthesized, minimum, and single-item prefixes do not repeat the quantity. Evidence source and evidence text are on the same row as each item. Conflicts show both values, files, pages, revisions, and source text side by side. Exact duplicate conflict statements are removed before comparison; same-value statements from different sources remain. Standalone Evidence, Field Verification, Citations, and PDF Geometry Audit sheets are removed. With no saved CAD takeoff candidate, the workbook has exactly five sheets: Summary, Materials & Equipment, Inspections & Tests, Conflicts, and Missing Information. Final sizes are 30,795,937 bytes for JSON and 2,555,273 bytes for XLSX; sheet row counts are 19, 3,228, 1,789, 222, and 5,828. All 93 conflicts retain at least two distinct complete statements and expand to 218 A/B rows, with zero identical comparisons. Excel displays up to three numbered evidence excerpts of at most 320 characters each per item; JSON retains complete item-level evidence.

The saved run's 3,308 legacy Chinese business strings were converted through a local `qwen3:8b` offline translation cache, without DeepSeek or another paid service; future prompts directly request English business fields. The compatibility layer rejects translations that lose numbers, generic drawing-page disclaimers, collisions where three or more different sources collapse to one long sentence, and any missing English translation for non-evidence business text. The fixed-English display also humanizes uppercase or mixed-case underscore codes, preserves complete labels such as `Sheet identifier`, removes raw `null`, strips isolated non-English OCR punctuation, and maps verified PDF private-use font glyphs to `°` and `μ` without changing saved evidence. Automated validation confirms zero Chinese display strings, non-English punctuation artifacts, internal IDs/keys, machine-style underscore codes, raw `null`, unsupported private-use glyphs, duplicated quantities, duplicate conflict statements, identical A/B conflict rows, generic visual placeholders, formatting code, formulas, hyperlinks, or whitespace-only strings. Final SHA-256 values are `786CCFB1ED55219902ABA3E42EBA5F89769F07114E5410924701FA7F8052DF63` for JSON and `AA8C789EAB4453C2891DD1EBC9ABFF8276C4D8E05233C547FD1D6D89A4DB8468` for XLSX. All five sheets were rendered and reviewed. The ledger remained at 8,501 calls, ¥107.047278 spent, and zero reserved, unknown, or unresolved cost.

## DeepSeek V4 Flash真实PDF复测
新建隔离项目`P-fb63b772ea5841baa7b68bc84ee9651a`，不修改或自动对账原Gemini项目。原件与重新上传文档均为12,489,262字节，SHA-256均为`7c5c76661c82edb5e9480f8e53b877e19dce7b19f180c9d8b1ae20bb61cc8124`。运行`RUN-8e9284fe238340ca92a92628f84d775f`使用官方`deepseek-v4-flash`、`thinking=disabled`及1秒请求开始间隔；144/144片段全部处理，47个`EXTRACTED`、97个`NEEDS_REVIEW`，最终状态`PARTIAL`是已声明的视觉/CAD/几何能力边界，不是供应商失败。

运行生成290条待人工审核记录：60条材料、16条检查、1条冲突、213条缺失/未处理说明；人工状态全部为`PENDING`。逐字段核验报告为38条`SUPPORTED`、12条`PARTIAL`、4条`UNSUPPORTED`、23条`PENDING`及213条`NON_DOCUMENT`。全程243条DeepSeek调用，323,913输入token、127,620输出token，本机账本全部结算为¥3.109899，未决调用0、最终预留0；这验证真实连通、完整文字片段吞吐、结构化输出、证据核验和本机预算核销，不验证施工准确率、全图纸无漏项或供应商最终账单。

运行中发现网页每2.5秒重叠请求完整290条记录，造成CPU争用与内存增长；关闭页面后运行正常结束。前端已改为串行轮询，活跃运行不加载完整记录，终态只加载一次，审核或手动核验后显式强制刷新。重启前积压请求在停止服务时产生的`CancelledError`只属于被取消的读取请求，没有新增模型调用、未决费用或运行数据丢失。对完成的绘图视觉运行，摘要接口为424ms、完整记录详情为88ms；JSON导出18,587,796B、1.597s，XLSX导出532,666B、2.778s。这些是本次本机结果读取与导出计时，不是施工分析性能或吞吐承诺。

## 验证过的保护
原句必须在允许片段中精确、唯一存在；偏移使用Unicode码点；拒绝伪造/模糊改写/重复/越界引用、字段漏项与重复项、模型写入review状态。仅词语命中不能自动变为SUPPORTED。数字不匹配会降为需要上下文。相互矛盾证据不得自动标为支持。

GET、引用跳转、中英切换和人工编辑不触发模型请求。单字段修改只失效受影响核验；模型/Prompt/证据变化不复用旧支持标记。核验不改变人工审核。默认mock保留PENDING/NON_DOCUMENT，不伪造模型成功。

离线接口使用MockTransport合成响应验证：DeepSeek非思考、Gemini 3.6 Flash minimal及隐藏输出token保守记账、最多1400生成token、同项目预算预留核销、缓存不重复HTTP且保留账目ID、预算不足不发请求、超时或usage缺失保持费用预留、截断和坏引用不通过、不自动重复收费。已结束Run的手动核验使用持久任务，绑定审核版本及原24h截止，不与分析并行。真实Gemini合成测试另外验证了官方接口连通、usage结算和PENDING人工审核；真实PDF测试验证了上传哈希、文字解析、局部供应商处理和未决费用下的安全暂停。两者都不外推为完整图纸分析或施工准确率。

重复`HTTPStatusError`后新增的离线回归同时覆盖提取和核验：HTTP状态映射为短类别，合法`Retry-After`及请求ID可持久化；带空格、伪造分隔符或超出白名单格式的头被丢弃。供应商错误正文、原请求正文和密钥均不写入`model_calls.error`；未知费用仍保持预留，第二次相同逻辑调用在同一Run中被阻断。人工对账新增确认门禁、金额约束和不可变事件；同项目跨Run未决调用会在网页开始、创建/处理/恢复、Gateway与预算预留各层阻断。对账后原Run只处理PENDING证据，重启时遗留RESERVED转为可核对UNKNOWN。节流期间关停核验不会再预留或发HTTP；恢复后后加入但排序更靠前的证据仍更新同一合并材料，旧人工决定保留在历史并自动失效。Gemini请求开始间隔与长中文有界拆分均以MockTransport验证；DeepSeek隔离项目另外完成上述真实PDF全量文字片段运行。网页轮询修复发生在运行结束后，不改变该运行的模型响应、账本或审核记录。

SQLite迁移保留既有三张核验表，并新增一张`call_reconciliation_events`审计表；重复初始化幂等，没有删除数据或重置预算。旧结果不会自动被宣布已核验，旧未知调用也不会自动假定未收费。Excel保留原有表，增加字段/原句表和内部引用链接；不执行来源文字中的公式。语言切换前后JSON（扣除导出时间）和XLSX工作簿部件一致。

## 界面验证方式
离线界面回归通过TestClient内存桥接执行HTML/CSS/JS，覆盖项目、上传、分析、审核、证据、移动宽度及账单确认/审计/恢复解锁。本次合并终验另外通过实际`http://127.0.0.1:8010`验证首页200、DeepSeek live就绪、四类分页摘要、记录详情、证据、引用定位和PDF预览；页面读取不新增模型调用。原生浏览器逐按钮人工验收仍与这些自动检查区分记录。

新增界面检查覆盖：字段引用、mock不标语义通过、真实接口未配置时付费按钮禁用、中英文切换不改变原句、390px无根页面溢出、点击引用高亮相同Unicode原文。源码网页截图是离线实际渲染，不是生成的效果图。

## 开发中发现与处理
An earlier synchronous tool timeout was not counted as a test pass. After capturing the complete child-process log and waiting for completion, the full 485-test suite passes. No existing test was removed or weakened.
发现核验缓存需要同时锁定模型/Prompt身份和保留原收费调用ID，已补齐并增加回归。PDF预览辅助样例第一次将mock标记放在标题后，mock如实未生成材料；调整仅合成样例的标记位置后验证预览，没有改变生产解析或让普通文件伪造结果。

## OCR、视觉、CAD与几何增量
RapidOCR 3.9.2、ONNX Runtime 1.30.0及ezdxf 1.4.4安装在项目虚拟环境。GNU LibreDWG 0.14固定从官方release下载，经SHA-256 `1AD7E15344D20B3426C3435B078D82FB84B35062815946B2CCA9C5FC9810FEA8`校验后保存在忽略于源码清单的`.local/tools`，程序不改系统PATH并可用`scripts/install_libredwg.ps1`恢复安装。合成DWG烟测实际完成DWG→DXF→ezdxf，识别1个`VALVE`块和1250 mm线长；转换器同时报告数据兼容告警，因此结果保持PARTIAL/PENDING，不将它当作任意真实DWG兼容证明。

指定真实PDF的离线解析输出位于本机临时验证记录：59页、161个证据片段（144文字层、17 OCR）、59个视觉任务、59个矢量审计页；53至58页原文字层不足的缺口已由OCR补出。OCR置信度平均0.925213、最低0.884081。10页检测到唯一比例并计算整页标定后原始线长；由于包含图框、标注和重复视图且未映射材料，`material_quantity`恒为null。该离线阶段没有付费调用。

视觉请求只允许DeepSeek官方基址和精确模型`deepseek-v4-flash-vision-exp`，整页PNG最长边2048，当前官方说明的每图最多384图像token计入同一用户选择的项目闸门。严格视觉Schema只允许可见文字/观察/图号/限制并强制`needs_review=true`；模型不得自行做几何换算或把图像指令当系统指令。真实59页视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59页，599次调用账本结算¥8.047433、预留/未知/未决均为0；产生379条待审核记录（79材料、26检查、274缺失）与31/9/13/52/274条`SUPPORTED`/`PARTIAL`/`UNSUPPORTED`/`PENDING`/`NON_DOCUMENT`核验结果。终态仍为`PARTIAL`，不外推为施工准确率。

## 仍然没有验证或实现
DeepSeek真实连通、完整文字片段处理、账本计费和本机延迟已经实测，但语义准确率、供应商最终账单和施工适用性尚未评估；同一基础模型的独立Prompt复核仍可能共同犯错。只核查当前记录关联的证据，不提供全项目反证检索或无漏项保证。精确原句引用仅基于文字层/OCR等解析文本；视觉模型输出只标为`MODEL_VISION_OUTPUT`整页上下文，不充当原文支持或反对引用。两者都不证明PDF阅读顺序/OCR绝对正确。

仍未验证任意真实DWG的Xref、字体、自定义对象、布局和单位质量，也未完成图形到具体材料的自动范围归并。PDF整页矢量审计和CAD图层/块计数不等于最终材料净量。旧证据缺少坐标映射时只能片段定位。10GB吞吐、24小时完成和施工准确率不在本次离线测试覆盖范围。

## 升级与回滚
先正常停止旧服务，备份整个`.local`（含SQLite辅助文件）与用户自有`.env`，然后更新代码并按原入口启动。新版本自动执行幂等追加迁移，不删除原文件/预算。回滚应用代码至`v0.2.5-ui-language`可保留新增表，但旧UI不显示核验；正式回滚仍应保留备份，避免覆盖后续新数据。

## 2026-09-15 offline performance and budget controls
Offline regression verifies user-selectable 1/2/4 local document workers, actual overlap between two local parser subprocesses, selective vision-page routing, exact-evidence-scope verification batching, aggregate stage/provider/model timing, processed-page coverage, a stage-weighted percentage with elapsed-time estimated finish, and a user-selected CNY 0.01–1,000,000 project limit. Migration 004 preserves the legacy ledger and adds aggregate run metrics plus budget-limit audit events. A limit below settled plus outstanding cost is rejected; increasing a valid limit does not reset cost. The ETA is explicitly an estimate and remains unavailable until enough progress exists. These are functional checks, not a measured speedup, model-quality comparison, or construction-accuracy claim. No paid API, private document, `.local` data, or credential was used.

## 2026-09-15 measured PDF batching and deterministic quality
The repository benchmark generated one invariant 48-page PDF with 44 text/vector pages and 4 image-only OCR pages. On Windows 11/Python 3.12.2, 1/2/4 workers completed all 48 pages in 10.213/8.929/7.496 seconds (1.00x/1.14x/1.36x). Each run produced 136 fragments, 4 OCR fragments, 4 visual tasks, 44 geometry summaries, and the same content SHA-256. The same fragment locators produce 34 bounded adjacent extraction groups instead of 136 single requests, an estimated 75% count reduction. A four-page control is slower with more workers (0.595/0.837/0.843 seconds), so concurrency remains user-selected rather than automatic. MockTransport verifies that a multi-evidence response may cite only supplied IDs, uses one paid-task reservation/recovery family, and does not parallelize paid HTTP. Deterministic tests reject descriptor material names, wiring-diagram false tests, generic QA objects, duplicate properties, entity-name properties, and properties citing evidence outside their requirement scope. Earlier customer PDFs were absent and the handoff archive contained no PDF, so none of these measurements claim customer-document throughput or semantic accuracy. No paid API, credential, `.local` data, or saved project was accessed.

## 2026-09-21 project-question workflow disposition grounding

Synthetic offline answers now prove that exact citations cannot be paired with the opposite bounded workflow disposition: RFI `CLOSED` versus open, Submittal `REJECTED` versus approved, and Email `PENDING` versus approved all fail. Accepted/approved, resolved/closed, under review/pending and not approved/rejected equivalents pass, while `approved as noted` remains more specific than plain approval. Comparison findings are checked against their own quotations rather than another source's status. A MockTransport response with a valid quote but reversed disposition becomes one settled `PROJECT_ANSWER` error and is not retried. This validation used no customer document, credential, live provider or paid API and does not infer chronology, authority or contractual meaning.

## 2026-09-21 project-question disposition ambiguity

Additional offline fixtures prove that RFI open/closed, Submittal pending/rejected and Email pending/approved evidence cannot be reduced to one answered state, even when the model cites only its preferred passage. The guard inspects only bounded evidence already retrieved for the request, isolates normalized exact workflow identifiers and source findings, and treats parser-recognized composite phrases as one state. Separate `APPROVED`/`APPROVED AS NOTED` and `PENDING`/`SUBMITTED` evidence remains ambiguous. A conflicting MockTransport answer settles one `PROJECT_ANSWER` error and is not retried. No chronology, authority, full-project counterevidence search, customer data, credential, live provider or paid API was used.

## 2026-09-21 deterministic exact workflow-status answers

Synthetic offline boundary cases accept only strict direct status/disposition questions naming one exact RFI or Submittal identifier and reject comparisons, explanations, yes/no predicates, content questions, multiple identifiers and generic Email status. A live-configured MockTransport fixture places one explicit `OPEN` RFI context in an Email-derived workflow index and proves that CIRP returns `ANSWERED`, `answer_basis=WORKFLOW_INDEX`, zero retrieved passages, the original Email file and its exact inline parser quotation without entering evidence retrieval, creating a model-call ledger row or sending HTTP. A route-level fixture asks the full `Request for Information No. 0042` form and proves that the project endpoint loads the same complete cached index before retrieval. Separate fixtures prove that one malformed `OPEN CLOSED` field returns `INSUFFICIENT_EVIDENCE` before retrieval or provider dispatch, while a missing status or inapplicable legacy `APPROVED` RFI value remains on the ordinary evidence path. No customer document, credential, live provider or paid API was used.

## 2026-09-21 full-run workflow-status preflight

Synthetic offline fixtures place one RFI 42 or Submittal 23-01 status in the retrieved evidence and a conflicting status only in the complete run workflow index. The exact status question returns `INSUFFICIENT_EVIDENCE` before a configured MockTransport can receive HTTP and before a model-call row or budget reservation exists. An API fixture proves that RFI contexts parsed from two Email summaries use the same cached terminal index path. A separate fixture proves that duplicate Question sources with one status, a conflict under another exact identifier, and a generic Email-status question do not trigger the preflight. The implementation reuses the existing workflow index and adds no evidence scan, dependency, provider call, chronology or authority rule. No customer document, credential, live provider or paid API was used.

## 2026-09-21 inline workflow-conflict provenance

The same RFI and Submittal fixtures now require the deterministic response to contain both explicit status values, both original Email file names and their detected classification origin. The API returns structured workflow type, exact identifier, status, source-file and detected/manual origin records; the browser contract renders each status, source link and origin directly under the answer using text nodes and `noopener` file links, never response HTML or a displayed internal document ID. All index sources remain available in the structured list, while answer prose shows at most three file names per status and counts any remainder. The provenance structure reads only members already present in the cached workflow index; the later exact-citation step adds one bounded local evidence read but no model request or budget action. No customer document, credential, live provider or paid API was used.

## 2026-09-21 exact workflow-conflict citations

Parameterized offline fixtures prove that detected RFI open, Submittal pending and Submittal approved-as-noted statuses receive the exact immutable source sentence and locator inside their inline conflict source. The same response leaves a manual opposing status uncited. API fixtures prove that an Email-derived detected status receives its exact quote while a detected workflow summary with no matching original phrase remains file-only, and that a `CLOSED` phrase for RFI 43 is not borrowed as support for RFI 42. The matcher reuses the longest non-overlapping disposition parser, so plain approval cannot satisfy an approved-as-noted status. One local query is limited to the first 32 distinct detected conflict documents and excludes model-vision narration. HTTP request count, model-call ledger and budget reservation remain zero. No customer document, credential, live provider or paid API was used.

## 2026-09-21 deterministic workflow inventory questions

Offline boundary cases accept complete unfiltered count and list forms for plural RFIs, Submittals and Emails and reject status-filtered, document/source, identifier-specific and content-filtered lookalikes. A synthetic complete workflow index returns two RFI identifiers, one Submittal identifier and two unique Email files, including an `.eml` member whose effective document type was manually changed. The matching list returns those ordered values. A 55-RFI fixture retains the complete total, exposes exactly the first 50 identifiers and marks the result truncated. The tests replace evidence retrieval with a hard failure and use a live-configured `httpx.MockTransport`; both answers succeed with `answer_basis=WORKFLOW_INDEX`, zero retrieved passages, no HTTP request and no model-call ledger row. A route-level fixture separately proves that the project question endpoint loads the complete cached terminal-run index for count and list intent. No customer document, credential, live provider or paid API was used.

## 2026-09-21 explicit-status workflow inventory questions

Offline grammar cases accept status-before-category and category-before-status count/list forms for RFI and Submittal while rejecting Email status, unknown modifiers, identifier-specific requests and inapplicable approved-RFI or closed-Submittal combinations. A synthetic full index proves that an Email-derived `OPEN` RFI and `OPEN FOR REVIEW` RFI enter the confirmed broad-open result, while separate `OPEN`/`CLOSED` sources and one malformed legacy `OPEN CLOSED` field are excluded and reported as ambiguous. `APPROVED AS NOTED` and plain `APPROVED` both support a broad approval count, their `APPROVED`/`REJECTED` conflict remains ambiguous, and the qualified approved-as-noted list accepts only the qualified source. `PENDING` plus `SUBMITTED` follow the existing bounded pending equivalence. A 55-open-RFI list retains the complete total and first 50 identifiers. Evidence retrieval is replaced with a hard failure, live-configured HTTP request count stays zero, the model ledger stays empty, and a route fixture proves the cached complete index is loaded. No customer document, credential, live provider or paid API was used.
