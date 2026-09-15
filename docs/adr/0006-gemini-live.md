# ADR-0006：Google Gemini 3.6 Flash 本机 live 适配

用户明确选择Google Gemini 3.6 Flash作为本机真实API。继续复用唯一Gateway和OpenAI兼容chat/completions接口，锁定Google官方基址与`gemini-3.6-flash`，不把Key交给中转地址。Gemini 3无法完全关闭推理，因此L1显式使用其最低`reasoning_effort=minimal`；`max_tokens`限制内部推理与可见输出总生成量，usage以不漏记隐藏输出的较大值结算。

Windows初版增加不落盘启动器；2026-09-12用户明确要求避免重复输入后，入口允许选择Windows DPAPI当前用户加密保存新Key并自动加载，仍不写明文`.env`、网址、命令行、日志或Git。启动本身不调用模型；真实连通、账户计费与施工准确率必须另做受限样例验证。
