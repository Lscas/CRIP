# 规格、代码与LLM同步治理
**规格版本：** 0.2.0

## 权威与审批
机器需求JSON是编号、状态和验收条件的权威表；PRODUCT_SPEC阐明行为边界，DECISIONS保存用户决定。冲突时停下并提变更，不择一静默覆盖。产品负责人可以批准改变，不由开发LLM自己写approved冒充批准。

影响范围/规则/数据外传/预算/验收时，先出Change Request→负责人批准→同PR修改对应规格、代码/Prompt/配置、测试和Traceability。bugfix恢复既有语义可不改需求文字，但必须引用ID、回归测试和修复说明。spec_only仅修改规格，相关实现状态仍planned。

## 可执行检查能证明什么
`check_spec_sync.py`检查版本、历史ID保存、所有映射、implemented路径、Prompt正文、Schema定义和自动需求表。`check_changes.py --base SHA`验证PR变更清单与实际diff、需求ID及适用测试关系。pytest测试正负例和离线规则。

这些检查不能证明自然语言语义完全等价，也不能认证人类批准身份。实际仓库必须配置保护main、指定负责人、禁止开发机器人绕过、要求审查与CI；配置存在不等于设置已经生效。[S7]

## 防止“文档和代码一起错”
LLM不得为通过测试降低验收值；不得把planned测试路径当已通过；不得把契约示例当产品实现。traceability区分existing contract_tests与未来planned_product_modules，不强制创建空测试假装完成。

## 模型和Prompt变更
简单模板、局部代码、测试样例可交低价开发代理；任务卡限制上下文。判断需求和高风险业务规则由主审负责。所有Prompt有版本与正文；routing/cost/policy变更同样进入PR，不藏在供应商控制台。

## 仓库初始化
复制新基线到独立分支或新仓库；保留旧包或Git旧Tag。不要求覆盖原v0.1文件夹。先运行离线检查，再提交。若已有仓库，创建spec/v0.2.0-review分支，不直接覆盖main；任何现有代码迁移需另做差异审查。

`CODEOWNERS.example`需要真实账号替换，不能由本包伪造。CI不自动调用真实模型，不向fork PR暴露密钥。本包没有修改任何远端仓库或分支设置。
