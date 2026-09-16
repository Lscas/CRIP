# ADR-0007：DeepSeek V4 Flash 本机 live 复测

用户要求把修复后的真实文件复测从Gemini切换为DeepSeek V4 Flash。继续复用唯一Gateway、OpenAI格式`/chat/completions`、SQLite费用预留和人工审核；文本固定官方基址`https://api.deepseek.com`、模型`deepseek-v4-flash`及`thinking={"type":"disabled"}`。在用户同意受限页面图像外传且显式开启视觉后，页面视觉只允许专用`deepseek-v4-flash-vision-exp`；不自动升级到其他模型或高级路由。

Windows增加`start-deepseek-live.ps1`，网页入口为`scripts/deepseek_local_setup.py`。2026-09-12用户明确要求避免重复输入后，入口允许选择Windows DPAPI当前用户加密保存Key并自动加载；不写明文`.env`、网址、命令行、日志或Git，更换账户或电脑需重新输入。2026-09-11官方峰值价为缓存未命中输入$0.44/百万token、缓存命中$0.014/百万token、输出$1.32/百万token；运行时不假定缓存命中，用固定10 CNY/USD安全倍数预留输入¥4.40、输出¥13.20。该倍数是预算保护，不是报价。

配置和离线测试不调用供应商。本机RapidOCR/ONNX、PDF矢量审计及CAD转换不调用供应商；视觉启用时仅发送派生的受限PNG、必要定位元数据和提示词，不发送密钥或原始PDF。真实文本/视觉连通、实际账单、整份59页PDF输出稳定性与施工准确率必须在用户本机输入Key后分别验证。本ADR最初只交付整页视觉；CR-0021后续增加了有边框Spec/Schedule表格的单个高分辨率局部裁剪，但一般图纸语义归并、live裁剪质量和自动材料净量仍未交付。Gemini历史调用及两条未决费用不因切换供应商被删除或自动结算。
