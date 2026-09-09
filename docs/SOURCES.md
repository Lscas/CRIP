# 官方来源与核实边界
核实日期：2026-09-08。以下支持接口事实，不证明模型施工准确率或用户账户已开通能力。第三方中转API需另验证。本文URL用于开发文档引用。

- S1 思考模式：https://api-docs.deepseek.com/guides/thinking_mode/ 。默认开启；Chat接口可明确关闭，支持low effort。低价任务显式关闭。
- S2 视觉：https://api-docs.deepseek.com/guides/vision/ 。视觉候选为deepseek-v4-flash-vision-exp；文本型号不能替代；图像格式/大小以实际接口为准。
- S3 JSON：https://api-docs.deepseek.com/guides/json_mode/ 。JSON模式需相应参数/提示，仍须处理空内容和截断及本地Schema校验。
- S4 缓存：https://api-docs.deepseek.com/guides/kv_cache/ 。按前缀缓存、尽力而为；不当作预算必然折扣。
- S5 模型价格：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/ 。人民币百万token计价、时段不同；config快照只记高峰用于保守估算。
- S6 Responses兼容范围：https://api-docs.deepseek.com/guides/responses_api/ 。并非所有内置工具可用，文件输入与file_search不能按其他供应商功能推定。
- S7 Git分支保护：https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches 。审查/状态检查需要实际仓库设置，本包不会自动启用。

本包没有复用外部源代码；路由、预算、数据结构及测试为项目设计。其他工具栈仅作为实现候选，不在本轮声称已完成版本兼容或商业许可审查。

## 开发阶段补充核实：2026-09-08
Codex AGENTS与项目配置采用官方说明：https://developers.openai.com/codex/guides/agents-md 、https://developers.openai.com/codex/config-reference 。low仅对支持的模型/客户端有效，项目配置可能受信任和管理员设置限制；本包不固定账户模型，不设置项目级profiles/provider/auth。
