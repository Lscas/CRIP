# v0.2.0 实际验证报告
**任务日期：** 2026-09-08

## 已执行
- `python scripts/check_spec_sync.py`：通过。检查90项需求/追踪、旧ID保存、12个Schema定义、8个真实Prompt、自动需求表及成本初值。
- `python -m pytest --junitxml=reports/test-results.xml`：**106 passed，0 failed，0 skipped**。
- JSON/文件链接/ZIP完整性另见打包清单。

测试覆盖：确认项缺证据、伪造ID和日期、跨项目引用、条件项缺条件、型号属性无来源、互斥选项误选、设计量与几何校准、模型伪造审核、程序零token任务、Flash显式关闭思考、缺证据不升级、图像不送文本模型、高级模型关闭、输出和调用上限、300元预算预留/核销/未知账单、单进程并发预留、内部日期新旧、实例去重、缓存隔离、需求和变更清单。

## 没有执行或验证
真实DeepSeek API、OCR/CAD付费接口、10GB项目吞吐、24小时完成率、真实300元项目成本、实际图纸/规范准确率、原型Web或数据库、跨进程持久化预算锁、真实Git分支保护及人类审批。

`contracts/runtime_rules.py`是离线参考，BudgetLedger仅单进程内存；生产需数据库事务与供应商对账。检查代码不能自动证明自然语言全部一致或证据语义一定支持结论。角色/Prompt已经定义，不代表本轮曾用低价外部子模型执行生成。

正式历史项目评测按用户决定暂缓。本报告不得作为施工专业正确性或商业上线验收通过证明。

原始结果：`reports/test-output.txt`、`reports/test-results.xml`、`reports/spec-check-output.txt`、`reports/validation-summary.json`。
