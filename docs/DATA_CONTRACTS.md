# 数据契约与校验层次
**规格版本：** 0.2.0

## 模型与服务端分层
`material-item`、`inspection-item`、`conflict-item`、`missing-information-item`是模型业务候选。候选不得出现actor、review_status、analysis_run_id、model_id或任意extra字段。服务端在验证后构造record-envelope，附meta、review和quantity_review。

模型可以引用获准的Evidence ID，但它不创建原始Evidence、不认证原文日期、不批准自己的结果。服务端验证Evidence ID存在、项目/租户/输入快照一致，冲突日期与Source记录一致。

## Schema文件
| 文件 | 用途 |
|---|---|
| common.schema.json | 数量、产品选项、字段、元数据和审核状态公共类型 |
| evidence.schema.json | 原文件哈希、来源定位、内部日期、文本/图像 |
| requirement.schema.json | 首读原子要求，含条件、例外、选项和父条款 |
| extraction-result.schema.json | 全量联合抽取的结果及空/缺上下文/截断状态 |
| classification.schema.json | 文档类型、专业、Section、内部Revision |
| material-item.schema.json | 永久/临时材料、ONE_OF、设计净量 |
| inspection-item.schema.json | 检查、测试和报告的独立字段 |
| conflict-item.schema.json | 多来源差异、最新采用值或未解状态 |
| missing-information-item.schema.json | 仅阻断性缺失 |
| verification.schema.json | 候选的支持、矛盾或需补上下文结论 |
| record-envelope.schema.json | 服务端完整追踪外层 |
| change-record.schema.json | Git变更清单 |

## Schema与业务验证的边界
Schema检查字段、类型、枚举、条件和数量证据结构。`validate_candidate`进一步检查证据范围、字段级依据、互斥选项、日期一致性。实际原文是否支持语义需模型核验或人工，不能单靠ID存在证明。

`CONFIRMED`至少一个直接来源；`INFERRED_TO_VERIFY`必须rule id且具体设计属性仍需要原文来源；`CONDITIONAL`必须实际条件及证据或受控规则。不确定量为null，不把TO_BE_VERIFIED字符串放进number。

多个允许产品ONE_OF、selected为空是有效状态。选择某项必须为输入文件明确选型，并保留相应证据。临时材料无需品牌和量；不输出成本或责任判断。

## 数量
quantity.basis固定DESIGN_NET。EXPLICIT_DOCUMENT/SCHEDULE_EXTRACTION/CAD_OBJECT_COUNT可以没有几何比例；VECTOR_TAKEOFF/CAD_MEASUREMENT/IMAGE_CALIBRATED必须有校准、公式、测量输入和来源。公式是受控标识/表达式，不执行模型任意代码。

引用同一实例的多次appearance不重复计量；实例识别本身是后续产品任务，当前参考函数假设调用方已给可靠instance_id。互斥产品数量属于组，不把候选的相同数量相加。

## 审核
wrap_candidate一律初始化PENDING。已审核记录需要review_event和actor，但Schema无法认证真人，生产ReviewService必须验证登录用户、权限及实际事件存在。数量另外核验，不把接受材料名称等同于接受数量。

## 用例
examples内全部为合成数据，不能用作工程答案或真实施工正确率测试。`tests/`覆盖允许与拒绝案例，`reports/`记录本轮运行；外部API、解析、Web和真实数据库仍未实现。
