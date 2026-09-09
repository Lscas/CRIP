# 施工项目智能审查平台 · 开发规格基线 v0.2.0

面向商业新建项目工程师：上传全项目资料，自动产生材料、检查/测试/报告、设计冲突和独立缺失信息清单。保留全专业目标，支持逐项人工审核。首选DeepSeek V4 Flash；单项目预算暂定300元人民币、24小时目标、一个活跃项目。

**本包是修订规格、配置、实际Prompt文本、JSON契约和可运行的离线参考/测试，不是已经完成的网页原型。没有调用付费API，没有部署应用，没有修改用户Git仓库。** 原v0.1压缩包保持不变。

## 首先阅读
- [产品规格](docs/PRODUCT_SPEC.md)
- [本轮确认决定](docs/DECISIONS.md)
- [模型分级、token与预算](docs/MODEL_COST_POLICY.md)
- [架构与当前实现边界](docs/ARCHITECTURE.md)
- [原型开发计划](docs/DEVELOPMENT_PLAN.md)
- [开发LLM规则](AGENTS.md)
- [全部90项需求](docs/REQUIREMENTS.md)
- [本轮验证报告](docs/VALIDATION_REPORT.md)

## 默认成本策略
程序先做确定性任务，不调用LLM。简单分类/联合抽取用Flash非思考；模糊关系按条件升级到同Flash低强度思考。视觉用真实支持图像的接口；高级模型默认关闭。首读一次联合抽取，使用短JSON、版本化结果缓存和有限重试；不能靠跳过资料省token。

价格快照仅供保守估算，用户API可能为中转，尚未确认实际价格。`live_api_enabled=false`和`.env.example`防止检查时误花费用。后续实现需把这些开关接到真实Gateway；当前包没有外部调用代码。

## 文件内容
`docs/`：产品、架构、数据、开发计划、决策、审查修复、验收与来源。
`spec/`：90项需求、对应追踪与12个Schema。
`config/`：路由、预算、参考价格、Assembly、开发模型策略。
`prompts/`：8个实际任务Prompt和版本清单。
`contracts/`：路由、单进程内存预算、日期选择、证据及选项验证的离线参考。
`tests/`：106个合成契约/策略测试；不是施工精度Gold。
`scripts/`：规格检查、生成需求表、Git变更清单检查。
`.github/`：CI/PR/Issue/CODEOWNERS示例，尚未在真实远端启用。
`examples/`：合成输入输出，不用于工程施工。
`baseline/`：旧需求ID与源归档指纹。
`reports/`：本轮实际离线测试记录。

## 本地检查
在隔离Python环境内执行，命令不会调用LLM：
```bash
python -m pip install -r requirements-dev.txt
python scripts/check_spec_sync.py
python -m pytest
```
修改`spec/requirements.json`后，运行`python scripts/render_requirements.py`更新自动需求表。

若在现有Git分支内做后续变更，使用`python scripts/check_changes.py --base <BASE_COMMIT_SHA>`；在没有Git仓库的解压目录里不要运行该命令。CI模板在PR中自动读取base SHA，不需要把密钥放入CI。

## 接入与治理
用户API地址、密钥及实际价格在后续接入时配置；无需把密钥交给规格包。第一开发任务为mock Gateway/预算/任务与上传网页骨架，然后接解析与联合抽取。完整步骤见开发计划。

需求或业务边界变化必须先获负责人批准。implemented需要真实代码、测试与报告；本包所有产品业务需求仍planned/deferred/superseded。契约参考测试通过不自动变成产品功能完成。
