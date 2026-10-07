# 单题固定模型档位：实施契约

状态：实际 schema24 部署及受控命名 `FLASH_NONE` 五题调用已完成；56张旧表原有列指纹不变、启动0调用，五题的来源回执和输入承诺已落库，详见 `reports/named_profile_runtime_canary_2026-10-04.md`。Sol 独立设计审查、命名档位/数字诊断51项专项、输入承诺27项专项，以及冻结全仓1758项Python与24+11项Node门槛也已完成，见 `reports/reference_profile_release_gate_2026-10-04.md`。这些历史检查点不等于 Low/Pro 真实比较、自动升级、补件网页现场验收或手机验收完成；本轮没有生产实施新的 v9 策略。服务当前运行的五题基线与本功能分开保存。用户已批准持续开发，暂不考虑本地模型；输入不以人为字符上限裁剪或验收。

## 固定档位与兼容

| profile_id | 文本模型 | 推理模式 | 固定生成额度 |
|---|---|---|---:|
| FLASH_NONE | deepseek-flash | disabled | 2600 |
| FLASH_LOW | deepseek-flash | thinking-low | 8000 |
| PRO | deepseek-v4-pro | disabled | 2600 |

这些是当前服务端支持的闭集映射，不是供应商模型发现服务。全部为文本路径，沿用已加载的官方 DeepSeek 通道与凭据。Low 的生成额度包含思考开销，比较报告必须披露不同额度，不能声称相同计算预算。

新建/克隆 Reference evaluation 及独立 questions-v3 只接受可选 `profile_id`，不接受任意 provider、model、URL、key 或推理参数。新 evaluation 保存原七字段 profile 加 `profile_version/profile_id/max_output_tokens`，三者一起出现。新结果的 execution_profile 保存路由身份的受控投影，并由同一 canonical mapping 核对。

所有旧七字段任务，包括旧 v8，仍按原全局配置相等守卫执行；不按七字段猜出命名档，不回填，不改变历史缓存身份。命名档只适用于 selector-v8，Gateway 也必须再次验证，不仅信任 API。

## 请求、结算与输入记录认证

每个新命名请求的身份包含 profile version/id、实际模型/推理/生成额度，以及版本化的初始证据指纹；不含 evaluation ID。相同输入同档可复用已结算调用，不同档不得串用。共享全局 Settings、凭据、HTTP client 及结算路径均不修改。

初始证据指纹覆盖选择策略、selection identity、有序证据/文件/定位、原文及布局指纹、来源组和冲突。模型身份不进入这个初始资料指纹，方便比较；它在同一问答各轮保持一致。各轮实际证据另保留 ordered manifest。

另以 `profile_neutral_input_sha256` 绑定每轮实际共同输入：对版本标记、实际system_text、实际user_text及空图片列表做规范化哈希，只保存digest。命名档固定为文本，图片列表必须为空；这里不含模型、推理档或输出额度等实验变量。它覆盖了实际追问历史和冲突说明，避免仅凭相同证据行就错误声称多轮模型输入相同。新命名receipt必须有此字段，并一同受结算承诺保护。

仅在 request_hash 中加入上述内容并不足以认证后来保存的 receipt。迁移024为 model_calls 增加两个 nullable 的 source-text-free commitment 字段，新命名调用才强制：

- Gateway 在得到 call_id 后、发送前生成受控投影：版本、canonical完整profile、receipt除cached外全部字段。
- 递归规范化 JSON 对象后计算 SHA-256；数组顺序仍有意义，cached仅为重放观察值，不参与承诺。
- 成功、契约失败、供应商策略失败均在结算同一事务写入承诺；重复结算必须包含一致承诺。
- 重放、结果保存、旧结果重新认证及评测失败保存都重新计算并与真实调用的落库承诺比较。
- 模型、模式、cap、initial/ordered manifest、布局hash/bytes等任何字段被替换均拒绝；UNKNOWN/RESERVED不能变成可认证终态。
- 旧调用两字段保持NULL，仍使用旧认证；不能把旧调用包装成新命名调用。数据库迁移不得改写既有业务值和人工审核状态。

布局可能由selector从text_map跨块重建，因此不得用另写的简化source-row算法机械重算后误拒有效布局。可信结算承诺绑定实际布局指纹，原文/文件/项目/快照身份仍独立检查。

新增隔离迁移演练使用完整schema23与合成业务、人工历史和四类调用状态，通过真实Database初始化升至24，对全部旧列逐值比较不变。SQLite备份恢复到另一临时文件后仍为23且值一致，外键与完整性检查通过；重复初始化不改旧数据。这是早期隔离演练。其后实际本机 schema23→24 已完成部署：权威备份、56张旧表原有列指纹、旧迁移记录、外键及完整性均已核验，启动新增调用为0；该实际检查点同样不证明真实客户库回退。两者的范围不得互相替代。

## 比较的正确含义

命名对命名比较必须有认证后的初始证据指纹；缺失、跨轮不一致或双方不等均不能标成公平同输入。失败项也应带出其真实终止轮指纹。

- named ↔ legacy：允许历史观察性对照，但标明 `UNKNOWN_LEGACY`，不声称固定输入公平。
- 初始相同且双方完整有序 `(round, profile_neutral_input_sha256)` 链相同：才可标 `FULL_PATH_MATCHED`。`fair_fixed_input`只表示共同输入内容相同，不是相同模型配置或生成预算。
- 初始相同但双方完整共同输入链不同：标 `DYNAMIC_PATH_DIVERGED`，区分动态补资料效果与固定证据推理能力。同证据行但追问历史不同也不能算固定输入相同。
- 任一失败发生在第2/3轮但只保存终止轮receipt：标 `SAME_INITIAL_PATH_UNKNOWN_FAILURE_HISTORY`，`fair_fixed_input=false`；不能编造丢失的前轮链。
- 任一仍待执行的项标 `PENDING_UNEXECUTED`，不是已证实公平的完成对照。比较不自动判断答案正确，更不修改人审。

含命名档的比较使用新comparison版本，身份包含初始指纹、共同输入路径、公平性状态与结果。纯legacy比较保留旧版本/身份。attach、已终态执行重放、compare及scorecard读取命名终态都重新认证，不因已保存就跳过；前置错误无真实receipt不能写成命名终态。

结果自身通过结算认证还不够：所有评测消费路径还要绑定冻结evaluation的项目、run、snapshot、question key及canonical profile。旧档评测不能挂入真实命名档结果，删除结果或评测的命名字段也不能绕过仍存在的账本承诺。比较同时认证成功与失败；人工adjudicate在任何写入前认证整套待返回scorecard，防止损坏的其他题造成“错误响应但审核事件已提交”。缺失或畸形的已保存证明统一返回安全409，不暴露原始校验异常正文。

这里的完整性检查以本地账本和数据库为可信根，不是对掌握整个数据库写权限的攻击者提供防篡改签名。如果同时改写所有来源身份、回执、结果和账本使其与历史无证明数据不可区分，不能声称仍能识别。历史无receipt的legacy结果和失败保持原有观察语义，不回填成命名档。

## 必须具备的离线证据

### named-v9 完整失败链增量（修复后的冻结全仓通过，真实部署与模型比较未完成）

迁移026只为失败表增加可空的`failure_execution_json`，不回填或改写历史失败。新对象只含版本、`COMPLETE_CHAIN`、1–3个安全receipt3、已执行补证轮数和已接受补证请求数；决策数及fresh/cache由回执推导。不保存问题、查询、缺失事实、证据原文、被拒回答或私有思维链。完整非NULL链必须认证，畸形数据不能降级成旧版未知。

每轮复用原有来源、账本与输入承诺检查；前序调用必须为`SETTLED`且其响应在累计历史下仍是合法`NEED_EVIDENCE`，重建检索必须取得新证据才允许下一轮；末轮必须对应现有`SETTLED_ERROR`失败回执。只有非named历史profile允许receipt/chain皆NULL；named缺终轮receipt仍须拒绝。失败与完整链同一次不可变INSERT保存；重复execute认证旧结果并保持零新调用。完整链可用于共同输入路径比较，旧terminal-only多轮失败仍为`SAME_INITIAL_PATH_UNKNOWN_FAILURE_HISTORY`。

顶层可选`failure_execution`仅属于execute响应；旧evaluation failure对象不新增键。runner的新v9报告同时区分模型提出与实际接受的补证请求，并分开保存执行总量和本次新增调用。首轮全仓1项历史迁移fixture失败经测试专用修复、46项回归及Sol复核后，新冻结全仓2,079项及辅助检查全部通过，779文件起止一致；原失败仍归档。以上不代表真实运行库迁移或答案改善；准确范围见`reports/reference_v9_failure_chain_validation_2026-10-04.md`。

闭集及严格输入拒绝；create/readiness/job/scorecard/compare全链schema通过；创建锁；三档真实MockTransport payload；固定cap不受全局额度缩小；全局视觉开启也不发图；同档重放零新增HTTP；跨档与初始指纹隔离；成功和失败结算承诺原子保存；外部receipt篡改拒绝；legacy NULL保留；多轮公平性与缺失败历史降级。定向测试通过不等于全仓通过。

新增专项覆盖文件为`test_reference_profile_comparisons.py`、`test_reference_profile_binding.py`及`test_reference_profile_failures.py`。它们使用真实本地保存/MockTransport调用链覆盖三档同答案独立result_id、相同来源但不同追问历史、二轮失败缺前轮记录、跨档/同档跨题换绑、缺回执/调用、无审核副作用和失败认证；这不是供应商实际执行或工程答案质量验证。

显式测试工具 `scripts/reference_live_smoke.py --profile-id ...` 仍需 `--confirm-live`；输出文件不可覆盖、已终态题不重执行、未决调用停止后续、无自动重试。这个开关不改变服务全局模型。该工具已用于受控 `FLASH_NONE` 五题，不可据此声称 `FLASH_LOW` 或 `PRO` 已执行或比较完成；任何后续真实档位调用仍须在原授权范围、冻结输入与独立终审条件下单独进行。

## 本功能尚不提供

Flash自动升级Pro、工程审批授权、通识库自动入项目、真实手机网络验收。它先提供可审计的单题配置和比较基础，后续升级必须依据实测触发器，而不是模型自报信心。
