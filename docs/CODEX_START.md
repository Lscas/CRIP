# 在 Codex 中继续开发，减少重复上下文
## 1. 打开真正的项目目录
解压源码包后，在本机Codex打开包含 `AGENTS.md`、`app/`、`web/` 的cirp目录。此包不曾修改本机Codex账户、全局配置或用户远端仓库。
代码与模型API是两套配置：`.codex/config.toml` 约定开发推理强度；`.env` 才是产品DeepSeek接口。300元是产品项目预算，不包含Codex订阅或开发费用。

## 2. 默认开发方式
项目配置仅指定 `model_reasoning_effort = "low"`，不硬编码账户可能无权使用的模型。Codex需要允许/信任项目配置，受本机及管理员设置影响。用户在Codex选择其可用的低成本开发模型；高风险改动应显式审查，不静默升级。
根AGENTS保持短小；app/web/tests各自补充规则。不要给每个任务重复发送完整PRD、90项需求、全部Schema或客户文件。完整设计仍在docs，涉及边界时按需读取。

## 3. 给Codex的第一条任务
```text
继续当前CIRP项目。先读AGENTS.md、docs/DEV_STATE.md，运行git status --short，
然后运行python scripts/context_pack.py DEV-002。
只完成任务卡允许范围内的互斥产品映射，不调用付费API，不读取.env或.local。
先定向测试，完成后运行规格同步和全套离线测试；同步Traceability与变更记录。
只回复改动摘要、真实测试结果、尚未完成项，不复述整个规格或粘贴完整文件。
```
DEV-002是已批准材料规则的实现，不授权新业务规则。遇到Schema表达不足先提出具体差异。

## 4. 检查命令
```bash
python scripts/context_pack.py DEV-002
python scripts/run_checks.py --area api
python scripts/run_checks.py --area gateway
python scripts/run_checks.py --area all
```
完整日志留在reports/local（Git忽略），终端只显示摘要和有限失败详情。修改失败时读取相关日志范围，不循环打印全仓库。生成需求表用 `python scripts/render_requirements.py`，不手改生成表。

## 5. 限额的真实含义
任务卡的input/output token数字是开发建议，不是Codex账户硬配额。上下文脚本实际限制字符数并拒绝超限，token随分词器变化。低推理设置不能保证每项任务总token更少；保存进度、减少重复读取和不用并行代理重复审查更重要。
未运行实际Codex或低价子模型，不声称已获得某百分比节省。

## 官方参考（核实日期2026-09-08）
- Codex指令文件：https://developers.openai.com/codex/guides/agents-md
- Codex配置：https://developers.openai.com/codex/config-reference
项目配置不放provider/auth/profiles，避免与用户级设置冲突；不降低沙箱、审批或密钥保护。
