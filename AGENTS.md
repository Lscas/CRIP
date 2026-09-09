# 开发LLM工作规则
**规格版本：** 0.2.0

先读README、docs/DECISIONS.md、PRODUCT_SPEC中相关章节、requirements/traceability中受影响ID、相关契约测试；不要每个子任务把整仓库发送给模型。

## 权限
只实现已批准需求。范围、版本策略、300元预算、API外传、数量口径、验收改变必须先提案等负责人批准。可同步修正文案、实现状态和路径，不能自批Open Question。用户明确说只读时禁止写文件、安装、运行有副作用脚本或提交。

## 成本
能用脚本、类型检查、Schema和测试处理就不用LLM。简单字段改名、模板、小单测和格式整理使用可用的低价模型；给出允许文件、需求ID、输入片段、验收、token预算。没有子模型接口时不声称已路由。高价模型默认关闭，不自主升级或突破预算。

## 同步
行为变化同PR更新需求/说明、Schema或Prompt/配置、测试、Traceability和Change记录。spec_only不能把应用标implemented。实际实现需要真实测试路径与运行报告。历史需求ID不得删除或复用。

## 不变量
模型不产生人工审核状态；确认项必须有字段级证据；最新按内部修订日期而非上传时间；互斥产品不双计；仅设计净量；缺证据不猜；QA不得补外部标准正文；partial不得宣称complete。

## 任务开始输出
Impacted IDs、已批准决定、允许路径、拟修改规格/代码/测试、当前阻断、模型级别和费用影响。任务结束输出实际完成、未完成、测试命令和结果、迁移/回滚；不得用“检查通过”代替施工质量验证。

运行 `python scripts/check_spec_sync.py` 与 `python -m pytest`；有Git基准时再运行 `python scripts/check_changes.py --base <SHA>`。检查失败须处理或明确报告，不删除测试让它变绿。人工审批仍在实际Git平台执行。
