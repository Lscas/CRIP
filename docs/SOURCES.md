# 官方来源与核实边界
核实日期：DeepSeek 2026-09-11；Google Gemini 2026-09-10。以下支持接口事实，不证明模型施工准确率或用户账户已开通能力。第三方中转API需另验证。本文URL用于开发文档引用。

- S1 思考模式：https://api-docs.deepseek.com/guides/thinking_mode/ 。默认开启；Chat接口可明确关闭，支持low effort。低价任务显式关闭。
- S2 视觉：https://api-docs.deepseek.com/guides/vision/ 。视觉候选为deepseek-v4-flash-vision-exp；文本型号不能替代；图像格式/大小以实际接口为准。
- S3 JSON：https://api-docs.deepseek.com/guides/json_mode/ 。JSON模式需相应参数/提示，仍须处理空内容和截断及本地Schema校验。
- S4 缓存：https://api-docs.deepseek.com/guides/kv_cache/ 。按前缀缓存、尽力而为；不当作预算必然折扣。
- S5 模型价格：https://api-docs.deepseek.com/quick_start/pricing/ 。2026-08-16起采用美元峰谷价；`deepseek-v4-flash`峰值每百万token为缓存未命中输入$0.44、缓存命中$0.014、输出$1.32。config快照只记峰值，运行启动器再用固定10 CNY/USD安全倍数换算，不作为报价。
- S6 Responses兼容范围：https://api-docs.deepseek.com/guides/responses_api/ 。并非所有内置工具可用，文件输入与file_search不能按其他供应商功能推定。
- S7 Git分支保护：https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches 。审查/状态检查需要实际仓库设置，本包不会自动启用。
- S8 Gemini模型：https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash 。稳定模型代码为`gemini-3.6-flash`，支持结构化输出和thinking；模型页所称多模态能力没有在本原型接入。
- S9 Gemini OpenAI兼容：https://ai.google.dev/gemini-api/docs/openai 。官方基址为`https://generativelanguage.googleapis.com/v1beta/openai/`；Gemini 3不能关闭推理，3.6 Flash的最低映射为`reasoning_effort=minimal`。
- S10 Gemini价格：https://ai.google.dev/gemini-api/docs/pricing 。Gemini 3.6 Flash Standard截至2026-12-31为输入0.75 USD/百万token、输出3.75 USD/百万token，输出含thinking；免费层是否适用由账户决定。
- S11 Gemini推理与生成上限：https://ai.google.dev/gemini-api/docs/thinking 。`max_output_tokens`包含内部推理与可见输出，计费包含两者；本应用不请求thought summary。
- S12 RapidOCR安装与使用：https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/install/ ；项目仓库及Apache-2.0许可：https://github.com/RapidAI/RapidOCR 。本版使用`rapidocr`+`onnxruntime`本机推理，不向OCR服务上传文件。
- S13 ezdxf单位说明：https://ezdxf.readthedocs.io/en/stable/concepts/units.html 。DXF模型坐标本身无单位，只有`$INSUNITS`等上下文；本版缺单位时不发布长度/面积候选，不做隐式换算。
- S14 python-oxmsg project and MIT license: https://github.com/scanny/python-oxmsg ; pinned package metadata: https://pypi.org/project/python-oxmsg/ . CIRP uses version 0.0.2 to decode bounded local Outlook MSG containers and reuses the existing email evidence path. Public upstream fixtures were validated at commit `d0ee4645d4a8bf6d18517d33bc1f7dcb23e7620b`; the fixtures are not redistributed in this repository.
- S14 GNU LibreDWG：https://www.gnu.org/software/libredwg/ ；`dwg2dxf`命令说明：https://github.com/LibreDWG/libredwg/blob/master/programs/dwg2dxf.1 。本机固定0.14 win64发布包并校验归档SHA-256；GPLv3工具仅作本机独立转换进程，原DWG保持不变。
- S15 ODA File Converter：https://www.opendesign.com/guestfiles 。它是可选免费工具而非开源组件；本机优先GNU LibreDWG，只有用户另行安装时才作为后备。

本包的路由、预算、数据结构和测试为项目实现；OCR/CAD执行复用上述开源运行库而不是重写识别器和CAD格式解析器。依赖许可证按各项目原许可保留，兼容性验证范围以EVIDENCE_VALIDATION为准，不把单个合成样例外推为任意工程文件支持。

## 开发阶段补充核实：2026-09-08
Codex AGENTS与项目配置采用官方说明：https://developers.openai.com/codex/guides/agents-md 、https://developers.openai.com/codex/config-reference 。low仅对支持的模型/客户端有效，项目配置可能受信任和管理员设置限制；本包不固定账户模型，不设置项目级profiles/provider/auth。
