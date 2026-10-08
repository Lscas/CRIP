# 实际实现边界
版本0.2.6。本表说明工程进度，不改变已批准最终目标。

2026-10-07 审查首批修复完成本地全仓验收，见 `AUDIT_REPAIR_VALIDATION.md` / DEV-172：事实保持、独立数量审核、系统基线、供应商异常和setup边界通过audit2冻结2,849项Python（无失败/错误/跳过/重复）、24项语言及11项部署检查，808源文件起止一致，Sol终审无阻塞。原文语言、标点和设备编号保留；无明确范围的同Tag不自动合并，受限旧MG拆分保留历史并重新待审。此段为检查后元数据，不能替换冻结身份或最终提交的远端Actions结果。临时材料Schema修复不等于任意候选隔离；问题9/11/14/15/17、真实工程准确率、部署和手机验收仍未完成。客户数据、运行服务与默认分支不变。

2026-10-07 公开 CI 修复已完成本地完整验证：仅隔离两个 profile 测试的内存凭据后端、保留真实 Windows DPAPI 覆盖，并以 fresh LF checkout 修复清单与 Git 字节不一致。新冻结 r1 的2,762项 Python（0失败/错误/跳过/重复）、24项语言、11项部署入口、160需求/37Schema和清单均通过；797个源码文件起止一致，Python耗时1,646.072秒。旧 run `37673736407` 的平台和换行失败不撤销，也不把此前本机通过冒充远端通过。当前记录写于推送前，最终提交的远端状态须查其 Actions；仅元数据更新与原冻结身份分开，详见 `PUBLIC_SYNC_VALIDATION.md`。无产品逻辑、凭据安全、客户库、服务、付费调用或默认分支变更，不扩大功能完成度。

2026-10-07 公开源码门槛通过：r3的2,762项Python（0失败/错误/跳过/重复）、24项语言、11项部署入口、160需求/37Schema及796文件冻结检查通过；同源Chromium七组连续三次fresh通过。准确身份和归档见 `PUBLIC_SYNC_VALIDATION.md`，本段仅为检查后元数据。旧“双语”措辞不能解释成当前可切换界面：D-25仍固定英文，r5仅验证隐藏兼容路径的拒绝/英文重选且隔离零网络。公开CI不依赖私有现场候选，保留原validator断言。仍不新增projection提问入口、不改客户运行状态、不调用真实模型；现场质量、真机与六项目标未完成。

2026-10-07 增量：已保存投影终态的既有人工任务/双向补件/网页接入已通过Sol独立终审与冻结703项组合，262个受检源码起止一致，见 `../reports/reference_projection_cases_2026-10-07.md`。人工结案不修改机器结论，历史链接证明不冒充当前原件认证。新提问入口、当前全仓、实际浏览器与工程质量仍为独立未完成门槛；下方旧数字不覆盖新代码，六项目标仍进行中。

2026-10-07 人工复核基础已正式接入：动态原文/原件定位接口及网页只读显示、非证据身份验证和 schema28 状态扩展。冻结663项组合、50项Node检查及分范围Sol独立终审通过，256个受检源码起止一致；首轮夹具失败与修复保留。能力分开声明：来源查看可用不表示人工任务消费者或新提问入口已开放。完整来源认证先于显示，选中原文不截断，定位与工程适用性核验保持区分。证据见 `reports/reference_projection_human_review_foundations_2026-10-07.md`；新全仓、客户运行环境、实际浏览器/手机、真实模型质量及完整人工闭环仍未完成。

2026-10-06 正式保存/schema27/旧消费者边界通过新的852文件冻结全仓：2,587项Python、24项语言、11项部署入口、规格160/36全部通过，exit0；起止身份经主代理及Sol独立确认。见 `reports/reference_projection_persistence_full_gate_2026-10-06.md`。本段为检查后元数据，后续phase5人工复核候选不在该验收内。未开放新提问入口或改变客户运行环境；对象/条件语义、真实质量与完整人工现场闭环仍PARTIAL。

2026-10-06 正式store/schema27/消费者类型边界已完成Sol独立160项最终组合，0失败/错误/跳过，30文件起止一致。逐次来源/账本认证、裁决历史漏检修复、真实Mock v9临时克隆26→27跨层兼容及旧fixture隔离均有证据，见 `reports/reference_projection_persistence_validation_2026-10-06.md`。当前全仓仍待完成，网页只显示明确非答案提示，不能把拒绝旧流程当作新版人工复核闭环或公开创建入口完成。实际客户库/服务/真实API不变，工程对象/条件关联仍PARTIAL；下方“仅候选”是前一检查点。

2026-10-06 清单外统一保存/schema27候选完成48项联合、Sol独立26/22项重叠复验及起止哈希检查，限定离线候选无剩余P0/P1。候选复用同一结果表，保持旧v9字节/历史/导出行为，新终态逐次认证并拒绝类型降级与非法审核历史。具体失败/修复/局限见 `reports/local/projection-store-phase4/VALIDATION.md`。正式store、迁移注册与消费者尚未接通；候选不能继承下方2,524项全仓或代表真实质量完成。

2026-10-06 loop2/execution的841文件冻结全仓已通过2,524项Python、24项语言、11项部署入口和160需求/35Schema，exit0；Sol独立确认受检身份一致。见 `reports/reference_projection_loop2_full_gate_2026-10-06.md`。下方同增量待全仓已由此取代；phase4统一保存与迁移候选未纳入，不继承这份验收。尚未改变客户运行环境，仍不能宣称对象/条件语义、真实模型质量或公开问答路径完成。

2026-10-06 loop2原因分离和execution1/2纯只读认证已通过495项冻结组合、Sol独立138项及辅助检查，841文件起止一致；范围内无剩余P0/P1。它关闭新路径模型自报检索耗尽、解析不完整误归资料不足的混淆，不声称工程对象/条件关系已自动核实；旧路径保留。归档见 `reports/reference_projection_loop2_validation_2026-10-06.md`。下一闸为当前源码全仓，统一保存/迁移、公开入口与真实质量仍未完成，没有实际库/服务/付费调用变更。

2026-10-06 阶段2已完成829文件冻结全仓：2,459项Python、24项语言、11项部署入口及规格/清单检查通过，源码身份独立复核一致，见 `reports/reference_projection_loop_full_gate_2026-10-06.md`。阶段3仅为清单外候选；模型自报NO_NEW与本地检索耗尽的原因混淆仍是正式接入前必修项。未开放新入口，不宣称工程关系、真实模型质量或完整项目完成。

2026-10-06 selector10阶段2已接通内部有界补证、来源追加和账本恢复，最终冻结验证见 `reports/reference_projection_loop_2026-10-06.md`。独立stage/proof2/receipt5，旧功能保留；对象/条件关系与答案语义完整性仍未自动证实，结果永远不写人工批准。没有公开HTTP/UI或结果/case消费者切换，尚不是真实准确率、当前全仓或发布验收通过。

2026-10-04 来源投影增量仍不等于对象／条件完整修复：新增可认证原 PDF 字符矩阵／阅读顺序、完整行选择、问题与题目部分身份承诺。引用来源和形式选择覆盖可机械验证，工程关系与答案完整性保持 false，输出始终待审核。无数据库、服务、历史解析或自动升级变更；最新验证和下一接入门槛见 `reports/reference_pdf_projection_2026-10-04.md`。

2026-10-04 对象／条件关联仍为PARTIAL：完成不改变接受规则的精确安全失败诊断，以及无生产入口的摘录合同核心；模型不可自由改写事实或提供计算结果，候选始终要求复核。真实D4来源块检查未通过，未注册v10或修改运行服务／数据库／模型路由，不宣称五题质量提升；见`reports/reference_extractive_repair_2026-10-04.md`。条件升级继续后置。

2026-10-04 已获用户维护授权并完成真实schema24→26升级：56张旧表的原行/结构、5,972条账本记录及仓库外耐久备份独立恢复均通过；标准本地入口v9关闭/开启启动均为0模型调用。真实v9开发五题在`FLASH_NONE`、`FLASH_LOW`、`PRO`各运行一次，共15次新调用、0重放、0未决，三档均值11.0074/24.2856/14.5734秒。独立Sol原文复核确认Flash None 1/5、Low 4/5、Pro 3/5完全可用。没有工程人审写入，五题不是独立20题或发布验收；自动升级、人工补件闭环和真机流程仍未完成。见`reports/reference_v9_runtime_comparison_2026-10-04.md`。

2026-10-04 smoke3→metrics2适配器冻结全仓已完成：2,160项Python、24项语言、11项部署入口、160需求/28Schema与788文件清单通过，失败/错误/跳过均0，源码前后一致。准确归档见reports/field_metrics_adapter_full_gate_2026-10-04.md，取代下方同增量待全仓记录；本条为检查后元数据。真实维护、v9质量比较、人工闭环与手机验收仍未完成，不以软件检查替代工程质量。

2026-10-04 smoke3→metrics2离线报告适配已实现，最终90项专项、226项组合与Sol独立终审通过；原records模式不变，完整报告和评分文件按原字节哈希/身份绑定，保存执行、本次调用、终轮下界与runner计时窗口独立标注。它不是实时采集、人审认证或现场端到端计时。旧2,135项全仓不覆盖本增量，新全仓及真实部署/质量仍待完成；详见reports/field_metrics_adapter_validation_2026-10-04.md。

2026-10-04 现场统计v2冻结全仓已完成：2,135项Python、24项语言、11项部署入口、160需求/28Schema和785文件清单通过，失败/错误/跳过均0，源码起止一致。准确受检身份见reports/field_metrics_v2_full_gate_2026-10-04.md；本条属于检查后元数据，取代下方同增量待全仓记录。独立补证stage不在此门槛内，真实部署、模型质量、人工与手机验收仍未完成。

2026-10-04 现场评测离线统计v2已实现并通过最终65项专项、137项组合回归及Sol独立终审（无剩余P0/P1）。缺失/null观测不再等同fresh/0，明确观测覆盖和精确总计，legacy补证、requested/accepted及显式执行模式独立报告；混合整数/浮点累加的回归已用旧helper真实失败证明敏感性。输入仍是调用方提供的离线审阅记录，不认证真实遥测、不推导新证据或费用、不接入live runner。新全仓检查尚未完成，准确范围与归档见reports/field_metrics_v2_validation_2026-10-04.md；历史报告、人审、服务和运行库不变。

2026-10-04 通用知识的来源审查单独记录于reports/field_general_source_audit_2026-10-04.json：4个官方网页完成限定正文核实，2个原始PDF因403未完成正文/版本核验。来源审查不等于候选答案审定、产品知识准入或20题封存；原候选文件未改，生产代码、预算和人工状态未变。供工程人员使用的范围/版本/答案支持/拒答/隔离检查清单见同日source_review报告。

2026-10-04 现场题集准备新增实际应用路径的合成通道测试，不新增生产框架：独立附件经上传/解析/冻结run进入named-v9 runner与MockTransport；四类评分/人审标记不泄漏，原有数字契约拒绝特定无依据输出，终态重放保留调用与人工状态。7项专项通过，组合与Sol终审结果见reports/field_evidence_channel_validation_2026-10-04.md。只覆盖新场景初轮，未执行候选B05或未见20题，不能推断真实模型抗注入；生产源码、运行库和付费渠道未改变。

2026-10-04 named-v9完整失败链修复后的冻结全仓已exit0：2,079项Python、24项语言、11项部署入口、160需求/28Schema及779文件清单通过，源码起止一致。首轮1项历史fixture失败及测试专用修复仍独立归档，未改生产迁移。排除于正式清单外的24→26维护候选另通过125项最终合成测试与Sol终审，不等于真实库迁移。当前仅同步全仓后状态元数据；待确认停机方式，实际部署、模型质量、独立题集和现场验收尚未完成。详见reports/reference_v9_failure_chain_validation_2026-10-04.md。

2026-10-04 启动增量的独立全仓检查已exit0：2,011项Python、24项语言、11项部署入口、160需求/28Schema与773文件清单通过，源码起止一致。准确归档见reports/reference_v9_startup_full_gate_2026-10-04.md；下方同增量待检查为历史记录。后续named-v9完整失败链记录与计数口径修正正在实现，须另做专项、独立审查及最终源码全仓检查。实际运行库未迁移，旧schema25隔离helper不能直接用于新增schema26的初始化；需另行准备当前版本迁移核验。

2026-10-04 后续启动增量已通过135项专项和Sol独立终审（无P0/P1）：显式本地 v9 正反开关只改变 feature gate，普通启动继续恢复已保存配置且不读.env，未传开关保留旧行为；配置文件不新增开关字段。此增量不由此前1,978项冻结结果覆盖；新全仓门槛仍须实际结束并核对源码身份，不包含客户运行库升级或真实模型测试。详见reports/reference_v9_startup_validation_2026-10-04.md。

2026-10-04 当前proof-runner冻结全仓检查已正式exit0：1,978项Python、24项语言、11项部署入口、160需求/28Schema及771文件清单通过，起止源码一致。见reports/reference_v9_proof_full_gate_2026-10-04.md；下述待全仓验证是历史状态。文档更新不改变已测试应用代码，但新文档清单不能替代归档身份。实际schema25部署、完整失败轮次统计、真实质量实验和现场验收仍未完成。

2026-10-04 proof-runner已完成Sol最高推理独立终审，无剩余P0/P1；176项后端/工具回归、31项网页契约、19项浏览器检查、24项语言检查通过，末次统计一致性补丁后67项runner复验通过（与176项重叠）。已准备771文件最终源码全仓门槛，未完成不得启用真实测试或宣称发布。真实运行库迁移、模型质量与现场闭环仍未验收。

2026-10-04 新版入口的768文件冻结检查点已通过1,912项Python、24项界面语言和11项部署入口检查。随后新增评测execute的可选付费前proof校验、只重放保护和细粒度能力协商；runner修复v8兼容并将仅有终轮回执的失败总调用数明确为未知。该后续增量须重新终审和全仓验证，不能套用先前通过状态；实际部署、真实比较和现场验收仍未完成。见reports/reference_v9_proof_runner_validation_2026-10-04.md。

2026-10-04 新版入口已收口：默认关闭的named-v9问答、评测创建/克隆、补件与Preview/Ask同源证明现已实现；前后端拒绝过期scope，历史v8及已保存结果重放保留。生产浏览器只完成合成来源/MockTransport验收，不是客户运行库部署、真实回答质量或手机验收。当前全仓门槛仍需独立完成。详见reports/reference_v9_entry_validation_2026-10-04.md。

2026-10-04 最新增量：v9已接入命名文字模型发送、补证、receipt3与缓存身份、成功/失败/缓存认证、结果/补件证明及冻结评测后端；schema25仅完成临时库迁移演练。349项问答/来源与137项契约/工具/预算回归全部通过，独立终审未发现新阻断。普通问答/补件网页和HTTP创建仍默认v8，实际库未迁移、服务未重启、无真实API调用。三入口预览/执行绑定、完整发布门槛、真实质量与真机验收仍未完成。详见reports/reference_layout_v9_backend_validation_2026-10-04.md；历史1760项不覆盖本增量。

2026-10-04 更新：补件生产网页已取得独立冻结全仓证据，1760项Python、24项Node界面语言、11项部署入口及规格/749文件清单检查全部通过，源码前后一致；见reference_case_ui_release_gate_2026-10-04报告。下文“全仓检查仍未开始”为旧检查点，不再是当前状态。此门槛不涵盖后续v9接入、真实模型效果或手机网络验收，六项开发目标继续进行。

2026-10-04 当前命名档位/诊断已实际部署至schema24，56旧表原列指纹、旧迁移记录、备份和完整性核验全部通过，启动没有调用模型。已完成5次命名FLASH_NONE新调用：2个契约有效结果、3个数字引用范围拒绝，全结算且不自动重试；独立助理只读复核C4完全可用，D4不可用并含无依据关联，未写人工审批。补件一键上传/新run/预览/回答/关联已合入正式网页，并保持人工审核状态、不可变结果和既有后端语义；生产UI定向检查24项、生产assets Chromium smoke 29项（20次MockTransport、0外部调用）、4个backend定向共23项和原有browser smoke 7项均已通过。它们不是live模型质量、独立holdout或真机验收；全仓检查仍未开始。Low/Pro、自动升级和手机验收仍未完成，详见named_profile_runtime_canary_2026-10-04报告及field_case_followup_browser_validation_2026-10-04报告。

2026-10-04 命名档位与数字诊断通过冻结全仓检查：1758项Python、24项界面语言和11项部署入口测试全部通过，无跳过；规格与源码清单通过。补件集成后续加强实际档位/承诺和409完整无变化断言后两项复测通过，应用代码未改变。实际迁移、真实模型质量、补件网页和手机验收不由这个离线门槛替代；详见reference_profile_release_gate_2026-10-04报告。

2026-10-03 命名档位与数字诊断51项、输入承诺含迁移恢复27项专项通过。补件链新增实际应用接口集成覆盖：上传、解析、新快照、经MockTransport重答、已发送输入证明、人工结案和重开。这里只证明隔离环境功能链；全仓、客户库迁移、浏览器编排、真实模型质量及手机网络仍分开验收。

2026-10-03 新命名档位已实现任务内配置和完整保存/重放认证：不会因成功记录本身真实就允许错挂到另一题或另一档位，失败比较也需账本认证，整套结果损坏会在人工审核写入前拒绝。专项与旧路径回归已有通过证据，全仓检查和实际部署仍待完成；没有真实三档质量结论。数字失败增加无原文安全定位，仍不保存被拒答案，不把字面数值出现当作语义支持。该增量不替代六项现场交付目标。

2026-10-03 补充实际验证：v8五题Flash普通实测为2题契约有效但无完全可用答案、3题numeric_support失败；均一次决策且不补资料，平均4.6284秒含失败，不是可用答案SLA。D4错误关系经独立助理复核；三个失败不能区分模型错误或校验误拒。52旧表在迁移20→23瞬间指纹一致，不将后续正常写入说成未变化。平衡20题候选r2和13项结构/附件哈希测试已完成，人工审定和实际执行未完成。单题命名档位及公平比较记录正在审查，不宣称已部署或自动升级已完成。

2026-10-03 当前新Reference任务已切换到v8完整选中来源，以下v7限额/基线描述为历史；冻结20题只读传输审计2,770行、零选中后丢行/行前缀裁剪。v8回执要求非秘密`execution_profile`，并保留跨轮实际发送的`sent_visual_regions`，而最终轮视觉区仍仅提供答案引用权威。新增人工case与基于已结算、重新认证输入回执的后续答案链接；缺profile的receipt-v2/v8旧形状不能新认证或新增链接，历史v3-v7结果/链接、原答案和人工审批不变。新版本全套、真实题正确率、完整补件重新回答和现场验收仍须逐项证明，见FIELD_QA_DEVELOPMENT；定向/Chromium合成数据通过不等于整体完成。

2026-10-03 V3关系包：复用页/顺序/locator，15题平均少6.28KB；零调用，live未验。

2026-10-03 Reference QA v7质量基线与计算契约：同一run/snapshot/15题/v7输入下，Flash为9题契约有效、7题完全可用，V4 Pro为12题、9题；均未达到15题有效、12题完全可用、零不支持断言的发布门槛。34次调用全部结算，未决和重试均为0；readiness schema补齐`QUESTION_ENTITY_MATCH`。Decimal等值比较保留原文操作数与本地复算；Pro三题复测为完全/部分/完全可用，Flash仍不稳定，未继续全量重跑；无live收益的自动补引用已回滚。

2026-09-27: D-30 removes active budget/rate/cost UI and dispatch gates. Budget/CNY rows below are history only; call-safety controls remain.

| 模块 | 当前实际行为 | 未完成 |
|---|---|---|
| Upload | Browser files/folders; Autodesk/Procore selected imports; selected/batched EML/MSG attachments with prevalidation and parent provenance; 4 MiB chunks, capacity, SHA-256 and deduplication | 10 GB stress, cleanup, object storage, virus scan, recursive email imports, OAuth/refresh, polling and live-tenant acceptance |
| 项目/任务 | WAL/事务；暂停/恢复/取消；1/2/4 workers；V2层级/span/ID、FTS/RTree、只读staging | 自动切换/回滚/隔离；CAD/BIM关系、向量检索、真实吞吐 |
| 问答 V2 | 独立preview/answer；显式日期/版本有效来源；Canonical结构块；claim级原文引用与Decimal复算；空间/OCR题最多一页overview+一处crop，图像引用强制复核；旧问答不变 | 授权staging上的固定15问检索/答案门槛、UI切换与旧general retrieval删除 |
| Ref QA | 解析/OCR；新任务冻结选页v8完整选中来源，v3-v7原样重放；显式Building本地范围；v5/v6附加块内精确行视图，v7跨选中块附加有界精确页面列视图；v8移除旧的最终上下文行/原文裁剪而保持版本身份隔离；显式问题列表/并列问句拆成P1..Pn并强制完整映射；固定15问来源页v7为15/15；D2单问Flash完全可用；D4 v7单问从含错误关联的部分可用改善为无不支持声明的部分可用，仍漏数量(3)；短E/V别名；DeepSeek Flash/Pro；实体错配拒绝；strict JSON；≤3图/48MiB；v8结算回执含执行路由、完整输入清单和跨轮实际发送视觉区域，最终轮区域仅供引用；结果/引用/审核/导出/比较；1–50题冻结评测、输入计划、持久化执行、停止/恢复；人工门槛；同快照Flash/Pro实测10/15与13/15合同有效、5/15与8/15完全可用，均未过门禁，零未决/零自动重试 | 保守解决D4前导数量完整性；完整v8输入的真实题答案质量复测；表格导出/可编辑答案；本地小模型同快照对照 |
| Parsing | TXT/DOCX/EML/MSG/PDF；PDF含OCR、流程范围、1/2/4 workers、显式标题栏Sheet、矢量表格单元格定位及带标签五位Section | 邮箱递归、未知封装、无边框/跨页表、多栏顺序、修订批注、图形语义及全部标题栏变体 |

EML: one body/related root; Gmail/Yahoo/Proton/Outlook history, signatures, resources and nested messages stay inert. Same-line metadata stays outside IDs; statuses require complete allowlisted values and descriptions stay neutral. Exact scopes do not cross-donate fields/prompts; valid Subject priority remains.
| 图片/DWG | 常见图片本机OCR并建立视觉任务；DXF对象元数据；GNU LibreDWG本机DWG→DXF后按对象解析，转换和对象解析均成功时标记`OBJECT_METADATA`，否则`UNAVAILABLE`；原文件不改 | DWG Xref/自定义对象/字体完整恢复、复杂Layout；真实用户DWG仍需样本验收 |
| Model | Mock/OpenAI Responses/DeepSeek/Gemini/custom Chat Completions settings；OpenAI Responses固定官方基址、`store=false`、typed output/usage本地归一化；DeepSeek/OpenAI/custom可显式开关V3选中页图像；OpenAI/custom可显式选择strict Reference JSON Schema；安全重启；有界抽取/核验；Flash/Pro同快照实测完成 | OpenAI live验收、供应商发现、LAN、区域检测、L2 |
| 材料/QA | 原子要求、Tag属性、字段证据、CSI locator；发布前排除泛称、非执行QA、重复/错挂证据、RFI问句、被拒Submittal、邮件头/历史，保留混合来源中的有效依据；全部有效RFI答复/Submittal/Email正文决定条件性和人工审核 | 完整选项、实体归并、结构化数量、复杂条件/广义语义评估；审批权限和语义关系裁决 |
| 冲突 | 同Tag/属性/单位的不同值；按可用内部日期采用新值并保留差异 | 单位等值、广义跨专业冲突和设计状态解释 |
| 缺失 | 未解析/未分析/关联上下文缺失明确列出 | 自动判断所有设计缺失的完整性 |
| 审核/导出 | 来源原文/定位及页面图；接受/编辑/拒绝、CAS冲突检测、历史；固定英文JSON/XLSX工程审核视图，名称/数量/单位分列、证据随项、冲突双方原文来源并排；已保存CAD量算按需显示 | 量算候选的专用接受/驳回事件、全套专业交互、跨Run编辑继承（已暂缓） |
| 成本 | 原子预留/核销、未知费用暂停、超额冻结；项目上限¥0.01–1,000,000，默认¥300且不低于承诺；已记录DeepSeek/Gemini账本；DPAPI保存live密钥 | 自动供应商账单、外部OCR/CAD费用；DPAPI密文不可迁移 |
| Codex | 小AGENTS、分目录规则、任务包、定向测试、有界摘要 | 不控制账户实际模型价格/总token，无实际子模型委派 |

RFI角色只由明确Question/Response形成，UNKNOWN保持中性。Submittal正文与精确引用为LINKED；邮件自引用为AMBIGUOUS；附件关系只来自EMAIL父文档。
Email线程只哈希有界`<local@domain>`头部token；其他值不建关系。祖先优先；循环/多Message-ID为AMBIGUOUS且不猜正文/日期。无有效ID仍独立显示。
Workflow：SQLite只投影9字段；网页按需去重加载≤500组。终态Run复用8项进程内索引，活动Run不缓存，分类修正清缓存。RFI/Submittal/Other修正有审计并保留附件来源，无解析、模型或费用。共享缓存和其他分类仍未完成。

默认演示模式只分析明确的DEMO标记；普通真实资料不会伪造材料清单。真实API需用户自行配置密钥、确认价格和开启开关，所有输出仍需审核。当前结果全部标为PARTIAL，不能声称全项目已经完整审查。

Render免费测试适配：代码/Blueprint/隔离设置/健康检查已实现；真实服务创建、平台构建、外网HTTPS验收未执行。没有可用公网网址。本机路径与业务能力不变。详见RENDER_VALIDATION.md。

## v0.2.4 本机部署
FR-LOCAL-001新增启动和隔离配置：可运行Linux HTTP验证，Windows提供CMD入口但未实机验证；不提升施工识图/提取能力，不改变mock边界。详见LOCAL_VALIDATION.md。

## v0.2.5：只增加显示语言
This section is historical. D-25 supersedes the bilingual runtime behavior with English-only application presentation.
FR-UI-002已实现：中英菜单/按钮/已知系统提示/状态标签、无刷新切换、浏览器偏好、弹窗与编辑器输入保留。后端业务代码、模型Prompt正文、预算与供应商配置、数据库迁移、导出字段和数据均未修改。视觉/OCR/DWG/完整Takeoff等原有限制不变。

## v0.2.6：字段原句与独立核验
新增字段引用、精确原文切片、PDF词坐标映射及受限原图预览；低成本非思考语义复核接口与持久化任务；人工编辑后失效与局部复用；JSON/XLSX原句表。Gemini live验证覆盖基础连接、记账和1份真实PDF的两次局部运行；随后DeepSeek V4 Flash隔离项目完整处理144/144文字片段并完成字段核验，243次调用全部结算¥3.109899、未决0。独立视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59页，生成220条证据片段（其中59条来自视觉；整体50个`EXTRACTED`、170个`NEEDS_REVIEW`）、379条待审核记录和599次已结算调用（¥8.047433，预留/未知/未决0）；逐字段核验为31条`SUPPORTED`、9条`PARTIAL`、13条`UNSUPPORTED`、52条`PENDING`和274条`NON_DOCUMENT`。仍未评估施工准确性。此版本之后已接入本机OCR、选择性整页视觉、Spec/Schedule有边框表格局部裁剪、DWG/DXF对象元数据和PDF几何审计；完整选项映射、跨专业关系、一般图纸区域裁剪、自动材料净量及施工准确率仍未完成。旧数据无词图时只能片段级图面定位；新解析才能产生词级坐标。

当前合并终验`RUN-7a2f9a8c090d40bdb802fdde17747d56`已处理指定59页图纸与833页规格书，产生1,964条证据和10,926条待人工审核记录（3,224材料、1,785检查、93冲突、5,824缺失/非文档说明），完成10,926份逐记录核验。8,501次调用全部结算¥107.047278，预留/未知/未决均为0；完整JSON/XLSX已通过结构、引用、内部跳转及公式注入安全验收。PDF几何审计221页但仅10页有比例标定，未形成材料净量候选，继续保持`PARTIAL`边界。

The fixed-English reviewer export regenerated a 30,795,937-byte JSON file and a 2,555,273-byte XLSX file from the same saved run. The workbook has five core sheets containing 3,224 material/equipment items, 1,785 inspection items, 93 conflicts, and 5,824 missing-information items. Exact duplicate conflict statements are removed before expansion; all 93 conflicts remain complete and produce 218 distinct side-by-side comparison rows with zero identical A/B rows. English numeric, parenthesized, minimum, and single-item prefixes are separated from quantity columns. Verified PDF private-use font glyphs are mapped to readable degree and micro symbols. Chinese display text, non-English punctuation, internal IDs, machine-style underscore codes, raw `null`, unsupported private-use glyphs, duplicated quantities, generic visual placeholders, raw JSON/formatting code, formulas, hyperlinks, and whitespace-only strings all validate at zero. Each of the five sheets was rendered and inspected. Excel shows up to three 320-character evidence excerpts per item; JSON and the app retain complete evidence. PDF paper-space geometry is absent from the output, and no Quantity Takeoffs sheet appears because this saved run has no CAD takeoff candidate. The legacy English compatibility cache was generated locally with Qwen and is guarded against number loss, generic disclaimers, translation collisions, and missing translations; no DeepSeek call was made. Saved evidence, human reviews, and budget state were not modified. The application, key page, and local launcher now default to English-only user/developer presentation.

无输出热修复（当时）：本机PDF解析上限由120秒调整为300秒，解析子进程周期原子保存已完成页面；超时Run保留已有片段并明确列出剩余未处理范围。只有缺失/未处理项时，网页首次自动显示该类别。当时未增加OCR，也未改变mock或付费API边界；后续OCR与整页视觉增量以上表和当前说明为准。

Gemini live适配：用户明确选择Google Gemini 3.6 Flash时，Key只允许发往Google官方OpenAI兼容基址；3.6不能关闭thinking，因此固定最低`reasoning_effort=minimal`并保守计入隐藏输出token。启动前要求确认人民币预算预留上界；2026-09-12用户明确授权后，可选择Windows DPAPI当前用户加密保存并自动加载Key，不写明文`.env`、网址、命令行、日志或Git。2026-09-11合成文本完成1次真实调用：839输入token、35输出token、账本支出¥0.007605、未知调用0。随后59页真实PDF首次运行在6/144后进入`PAUSED_PROVIDER`；用户授权重试在7/144后再次因`HTTPStatusError`暂停。PDF项目累计12条调用记录、已结算¥0.526795、2条未决并保留¥0.237238；程序没有自动重试。结果只证明账户连通、真实PDF上传/文本解析、局部响应结算和失败关闭保护，不证明施工准确率或完整大文件吞吐。

DeepSeek V4 Flash切换：用户指定live复测使用`deepseek-v4-flash`。本机127.0.0.1网页入口固定DeepSeek官方基址及非思考JSON模式；用户选择后由Windows DPAPI为当前用户加密保存并在后续自动加载Key，`--replace-key`更换、`--forget-key`删除。按2026-09-11官方峰值美元价和10 CNY/USD安全倍数预留输入¥4.40/百万、输出¥13.20/百万，并设置1秒请求开始间隔。文字运行在独立项目完成144/144文字片段：生成60条材料、16条检查、1条冲突和213条缺失/未处理记录，所有290条均待人工审核。随后绘图视觉运行`RUN-8091a26c122b40eeab3a5b4a87a6a5c7`完成59/59页并产生379条待审核记录（79材料、26检查、274缺失）；视觉与文字运行均验证连接、结构化输出、字段核验和本机记账，不证明施工准确率或自动材料净量。

网页轮询稳定性修复：真实DeepSeek运行生成290条记录后，旧网页每2.5秒重叠加载完整记录集，导致CPU争用和内存增长。现改为刷新串行、活跃运行不请求完整记录、终态只加载一次；审核或手动核验后仍强制刷新。新增有界摘要分页接口后，10,926条合并结果按类别和偏移读取；该修复不改变模型响应、账本、审核或导出内容。

HTTP诊断与恢复修复：提取和语义核验使用同一安全错误入口。非成功响应按400参数、401鉴权、403权限/账户前置条件、404模型/接口、408超时、429配额/限流及5xx暂时故障分类；只保存状态、短机器码、格式受限的`Retry-After`与请求ID，不保存供应商错误正文或请求内容。网络超时、传输失败和成功状态下的非法JSON也分别记录。所有此类请求仍保留费用预留并暂停，不自动重试。网页现要求用户确认已查供应商账单后逐条登记未收费或实际人民币费用，并保留审计事件；项目未决调用清零前，开始、创建、处理、恢复和付费请求出口全部关闭。恢复只处理未完成片段；同一Tag聚合身份稳定，新证据改变候选时保留审核历史并重新待审。Gemini本机环境默认15秒请求开始间隔；服务关停可打断节流等待并禁止随后预留/发送。仍不自动读取Google账单，也不改变300CNY预算。

大规格书发布与核验修复：候选审核摘要在发布前限制为Schema允许的400字符，完整属性仍保留在证据抽取中；只有本地发布阶段失败且不存在待抽取证据的运行可原地恢复，已经完成的付费任务不重发。逐字段核验按记录引用ID定向加载证据，分析发布时不再重复预生成核验。模型只允许安全去除回显的`claim`、`label`、`evidence_ids`、`context`或压缩过长理由；凡经修复均降为`NEEDS_CONTEXT`，未知字段、错误路径、越界或不精确引用仍以`MODEL_OUTPUT_REJECTED`失败关闭。
