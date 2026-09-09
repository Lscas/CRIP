# API-first原型架构
**规格版本：** 0.2.0

## 1. 服务形态
目标为模块化Web原型，而非大规模多服务平台。Web界面+Python API+后台Worker+PostgreSQL+对象存储；队列可先使用数据库任务表，后续确需再引入Redis/Celery。不需要本地GPU。工具选择是待集成验证的实现建议，不保证版本组合即插即用。

建议Python 3.11/3.12作为首轮依赖探针候选，最终锁文件记录实际可安装版本，不沿用未经验证的3.13要求。Web可用React/Next.js；PDF可用具备许可适用性的坐标解析器，DOCX用OOXML解析，图像按需OCR；DWG用可用的合法转换API。选择商业解析器需价格/许可确认，不列入本次已实施。

## 2. 数据路径
上传对象→Manifest→ParseJob→DocumentComponent/Fragment/Evidence→全量首读→Requirement→Entity/Appearance/Option→VersionSelector→Registers→Review→Export。

每个语义任务读取最小证据包；同专业Agent是配置和规则，不一定是独立模型。全量首读必须覆盖每个片段；跨Section检索补充关联。关联证据不被各Agent分别复制进长上下文。

## 3. ModelGateway
业务层只能用一个Gateway。首个adapter计划使用DeepSeek Chat Completions兼容接口，便于明确thinking开关与JSON模式；不强制采用Responses，二者不混用参数。实际HTTP调用尚未实现。

接口：classify、extract_joint、analyze_relations、analyze_crop、verify_candidate。请求附model role、预算reservation id、输入证据清单、token上下限和版本；返回业务JSON、usage、finish_reason、request id及错误码。返回无usage则账单待核对。模型API不给应用数据库写权限。

实际接口是否支持图片、JSON Schema、usage细分、缓存和思考开关分别探测，不以供应商网页或model名字推定。上下文上限不是建议每次使用长度。[S1][S2][S3][S6]

## 4. 数据库与原子性
关键表：projects、uploads、document_files、document_components、fragments、evidence、design_types、installed_instances、appearances、requirements、option_groups、version_decisions、analysis_runs、tasks、candidate_records、review_events、exports、model_calls、budget_accounts、budget_reservations、billing_events。

Project预算独立于Run，Run带immutable input snapshot和全部策略版本。注册结果唯一键采用(run_id, logical_record_key)，任务键含输入checksum和版本；重复重试不重复插结果。预算预留与任务派发采用事务outbox，防止预留成功但无人执行或重复执行。

模型候选Schema与服务端record-envelope分层，review_event来自登录用户，不从模型读。外键验证证据属于本项目同快照且可见。provider请求meta由服务端附加，模型不能自报已人工批准。

## 5. 版本与关联
比对单位为对象+位置+属性+条件。内置日期标准化保存raw与normalized；日期模糊、同日矛盾、修订倒序输出待核验。关系变化影响当前展示值，不物理删除历史证据。审批状态保留，不将LATEST_APPLIED称为合同批准。

缓存键：tenant/project/snapshot/fragments/crops/parser/prompt/schema/provider/model/known-version/retrieval/assembly/policy/parameters。模型别名可能滚动变化，记录已知版本和能力探针时间，必要时TTL失效，不假装别名固定。

## 6. API轮廓
POST /projects；POST /projects/{id}/uploads；POST /uploads/{id}/complete；GET /projects/{id}/manifest。
POST /projects/{id}/analysis-runs；GET /analysis-runs/{id}；POST /analysis-runs/{id}/pause；POST /analysis-runs/{id}/resume；POST /analysis-runs/{id}/cancel。
GET /analysis-runs/{id}/materials|inspection-items|conflicts|missing-information|coverage|cost。
GET /evidence/{id}；POST /records/{id}/review；POST /analysis-runs/{id}/exports；GET /health/version。

服务端强制项目权限。修改预算用独立管理操作+批准记录，不与普通resume混同。导出不运行LLM。

## 7. 运行状态
CREATED→INVENTORY→PARSING→EXTRACTING→LINKING→RECONCILING→VERIFYING→READY_FOR_REVIEW。
旁路状态：PAUSED_BUDGET、PAUSED_DEADLINE、PAUSED_PROVIDER、PARTIAL、FAILED、CANCELLED。READY不表示已人工审核。资源到限时仅停止新派发，在途调用待结算。

## 8. 安全及原型限制
用户没有要求数据驻留或零保留；仍需密钥、访问权限、文档注入防护、对象下载授权、文件大小/解压资源限制、CAD/OCR隔离与基本备份。禁止执行上传宏、脚本或外部链接；图纸引用检索仅在用户上传集合内。

共享部署费用、API token、图片token、OCR页数、CAD任务数与失败重试分别计量。昂贵模式默认禁用，密钥不随文件包发布。正式施工质量评测暂缓，不因此删除程序测试。

## 9. 当前实现边界
本包 `contracts/` 是离线规则参考；没有数据库、HTTP服务、Web、解析器适配、真实Gateway及生产预算锁。所有业务需求仍planned。应用验收必须在后续开发实际运行，不能使用本包契约测试替代端到端测试。
