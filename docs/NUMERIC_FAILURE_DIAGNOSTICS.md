# 数字依据失败的安全诊断

状态：实现、独立代码审查、相关51项专项及冻结全仓门槛通过；部署与真实失败复测仍待完成。没有重跑旧失败，不改写历史诊断，不代表C2/C4/C5的根因已经确定。全仓证据见reference_profile_release_gate_2026-10-04报告。

## 目的与不变项

只对已有的两类拒绝补充可核对元数据：断言数字未被引用支持，以及计算操作数不在引用来源中。保留原有通过/拒绝条件、Decimal数值等价规则、错误分类、请求身份、无自动重试和正常结算规则。不新增单位或语义校验，不保存被拒答案、数字值、原文、引用、定位哈希或私有思考。

`NumericEvidenceError`仍是`ValueError`；网关的既有`exception`字段仍记录`ValueError`，既有`validator`分类不变。新`semantic_detail`只增加到已存在的失败诊断JSON，无数据库迁移。

## 闭集内容

- `CLAIM_NUMBER_UNSUPPORTED`：`claim_index`与按该断言词法出现顺序计数的`numeric_index`，均从0开始。
- `OPERAND_UNSUPPORTED`：`calculation_index`与`operand_index`，均从0开始。
- 两者共有：TEXT/IMAGE引用计数，以及两个布尔出现标记。禁止混入另一种reason的索引或任意额外字段。

`number_seen_in_cited_source`只扫描引用绑定的具体evidence片段`raw_text`，不扩展到整页、整个源文件、相邻layout或图像。false仅表示未在这些可扫描片段文字中见到等值数字，不证明源图中没有该数值。`number_seen_in_supplied_text`只扫描本轮提供的`prompt_text`，缺该字段时才退回`raw_text`；不扫描未发送的原文尾部、layout、其他轮、整库或外部文件。两者均使用既有Decimal等价规则，均不证明工程语义支持、答案正确或检索完整。

例如“引用文本未见，但本轮其他文本出现”可以帮助定位引用范围问题；它不能证明另一段恰好同值的数字属于本题。两个标记均为false也不能直接判定模型算错、图片无证据或项目文件中不存在依据。

## 安全边界与验证

仅错误分支构建诊断，不增加正常回答的全输入数字扫描。网关只接收内部异常类型的白名单投影；数据库结算前再次检查reason、专属字段、整数类型及范围（不接受bool冒充整数）、布尔类型和1024字节上限。详情只允许用于`CONTRACT_ERROR`的`PROJECT_ANSWER`或`PROJECT_EVIDENCE_DECISION`。新诊断字段显式为null、混合索引、未知reason、自由文本、其他错误类别均应拒绝结算；旧诊断不带该字段则保持原行为。

含详情的顶层诊断必须完整且只含`kind/class/exception/validator/semantic_detail`，其中`exception=ValueError`。`PROJECT_ANSWER`的两类旧validator分别为`citation_scope/calculation`；`PROJECT_EVIDENCE_DECISION`分别为`numeric_support/calculation_operand_support`。错配分类、额外path或缺字段均不能进入账本。网关遇到内部详情与旧分类不一致时省略可选详情，保留旧的安全失败诊断，不让辅助定位信息阻断原本应完成的失败结算。

测试使用隔离数据库与MockTransport，核对错误定位、Decimal等价、未发送文本排除、无原文泄露、非法诊断拒绝的事务边界、已结算失败重放不二次HTTP。真实模型数字题的后续复测仍须单独执行。
