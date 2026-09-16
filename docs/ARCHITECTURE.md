# 技术架构与当前落地
**规格版本：** 0.2.6
## 当前可运行实现
浏览器静态Web → 本地上传或Autodesk/Procore只读选定文件导入 → FastAPI → SQLite / 本地不可变原文件 → 单运行Runner → 子进程文本Parser → Gateway → Schema/Evidence校验 → 四类记录 → Review/Export。
入口 `app/main.py:create_app`；命令 `python -m app`。

| 责任 | 文件 |
|---|---|
| 配置/真实API门禁 | app/settings.py |
| 事务/费用/缓存 | app/db.py、migrations/001_initial.sql |
| 续传/哈希/不可变存储及导入来源 | app/uploads.py、migrations/005_upload_sources.sql |
| Local inspection/import of selected EML or MSG attachments | app/email_attachments.py |
| Autodesk/Procore只读目录与选定文件导入 | app/connectors.py |
| 解析/失败隔离 | app/parsers.py、app/parser_worker.py |
| RFI/Submittal/Email确定性关系索引 | app/workflows.py |
| 本机OCR、PDF页面渲染与几何审计 | app/visual_pipeline.py |
| DXF对象元数据与受限DWG转换 | app/cad.py |
| 唯一模型入口 | app/gateway.py |
| 片段与运行生命周期 | app/runner.py |
| 四表候选/日期归并 | app/assemble.py |
| 审核API与并发版本检查 | app/main.py |
| 保存数据导出 | app/exporter.py |

当前API统一/api前缀；机器可读接口位于/openapi.json；Swagger默认页的外部资源可能被本地CSP阻止，不作为当前界面验收范围。具体端点以 `app/main.py` 为准。
Run锁定文件ID与内容；原始文件不可变。每个片段有唯一逻辑EV ID，数据库以Run前缀隔离，运行元数据由服务端加入，模型不能写人工审核状态。重跑创建新审核，不继承上一Run修改。

## 模型与成本
默认mock，无HTTP。DeepSeek文本适配器使用chat/completions、短JSON、thinking disabled；用户明确选择Gemini 3.6 Flash时锁定Google官方兼容基址并使用reasoning_effort minimal。DeepSeek启用视觉开关时，只有专用`deepseek-v4-flash-vision-exp`可接收本机生成的受限整页PNG，视觉结果必须通过独立Schema并保持人工待审。用户确认人民币计费上界和数据外传提示之前不发HTTP。并不依赖Codex配置切产品模型。

Local parsing includes RapidOCR/ONNX, selective PDF vision tasks, DXF metadata, bounded GNU LibreDWG conversion, PDF geometry audit, RFC EML, and Outlook MSG decoded by the pinned MIT-licensed `python-oxmsg` package. EML and MSG share one evidence path: current body and quoted history stay separate, HTML is reduced to safe visible text, remote resources are not fetched, exact Message-ID relationships are stored only as local hashes, and ambiguous identifiers are never guessed. RFI/Submittal routing remains deterministic across Subject, body headings, and filename fallback; exact section locators preserve each role or status. Attachments remain inert until one is explicitly imported through existing capacity, SHA-256, deduplication, and provenance controls, without inheriting authority or recursing into nested content. Autodesk/Procore imports remain selected-file and read-only. PDF geometry and CAD counts remain review candidates rather than design quantities. Mailbox synchronization, semantic thread inference, connector OAuth refresh/sync, general drawing-region understanding, construction-accuracy certification, and automatic material takeoff remain unimplemented.
EML MIME选择先移除空白纯文本和HTML候选，再在非空集合间维持plain优先；只有全部plain为空时才走现有安全HTML可见文字解析。

RFI角色聚合复用每份文档已有的角色集合，只把明确Question和Response的共存标为MIXED；UNKNOWN页面保持中性，不调用模型补语义。
Submittal精确引用复用同一关系组并显示LINKED；邮件自引用通过现有哈希键标歧义；附件关系要求父文档类型为EMAIL，避免把任意文件显示为父邮件。
邮件线程对本地精确哈希关系图做确定性拓扑排序，祖先在回复之前、同级按文件名和文档ID排序；检测到循环时保留所有节点并标歧义，不引入正文或日期推断。
Workflow API使用SQLite多路径`json_extract`一次投影九个关系字段，再由Python解码；不跨SQLite边界传输页面、视觉任务、CAD量算或几何数组，同时保留旧版顶层工作流字段。浏览器复用审核结果的有界分页模式：首批最多500组，显示loaded/total，只由审核者触发下一页，并按`group_id`去重追加；不自动拉取整个项目，也不产生模型或费用记录。

引用本地12套JSON契约，只给模型内联当前抽取Schema。缓存包含项目/快照/模型/Prompt/规则等，不跨项目复用。一次首读联合抽取，汇总/导出用程序；输入字节工程估计超限显示待细分，不隐式丢片段。

## 仍为目标架构
PostgreSQL + 对象存储 + 独立队列、局部高分辨率图纸视觉、完整CAD语义、自动设计净量、混合检索和跨专业关系。不要因为本地适配器已经运行就把这些标implemented。实现选择与回滚见ADR-0004，功能边界见IMPLEMENTATION_STATUS。

## Cloudflare测试入口
`浏览器 -> Pages Worker(可选) -> Cloudflare Tunnel -> 127.0.0.1 Python -> SQLite/文件`。
Pages不运行现有长任务或SQLite；本机必须在线。源站PreviewAccess全路径鉴权，Pages以独立secret调用/api。全路径包含规则、Fail closed及固定源站避免绕过或任意代理。静态构建白名单，只有HTML/CSS/JS和Worker。没有把10GB客户资料作为Pages资产。
