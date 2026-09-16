# 技术架构与当前落地
**规格版本：** 0.2.6
## 当前可运行实现
浏览器静态Web → FastAPI → SQLite / 本地不可变原文件 → 单运行Runner → 子进程文本Parser → Gateway → Schema/Evidence校验 → 四类记录 → Review/Export。
入口 `app/main.py:create_app`；命令 `python -m app`。

| 责任 | 文件 |
|---|---|
| 配置/真实API门禁 | app/settings.py |
| 事务/费用/缓存 | app/db.py、migrations/001_initial.sql |
| 续传/哈希/不可变存储 | app/uploads.py |
| 解析/失败隔离 | app/parsers.py、app/parser_worker.py |
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

RapidOCR/ONNX、PDF选择性整页视觉任务、DXF对象元数据、GNU LibreDWG受限转换和PDF矢量几何审计已接入本机流程。可识别的Spec/Schedule有边框表格可路由到一个保留整页坐标的高分辨率局部裁剪；其他合格页面仍使用一次整页概览。RFC风格EML用Python标准库在本机解析头信息及可见正文；HTML主动内容不执行，远程资源不请求，附件只登记。RFI问句/答复、Submittal显式状态及Email头/正文写入证据locator，确定性守卫阻止RFI问句或被拒Submittal直接成为当前材料/QA候选。PDF几何只输出页面对象与可能的标定信息，不写入材料设计净量；CAD块/图层计数或已知单位下的长度面积仅输出可追溯、待审核候选。MSG、邮箱同步、附件递归、邮件线程关系、通用图纸区域检测、图纸语义归并、局部裁剪live质量、施工准确率和自动材料净量仍未实现。
引用本地12套JSON契约，只给模型内联当前抽取Schema。缓存包含项目/快照/模型/Prompt/规则等，不跨项目复用。一次首读联合抽取，汇总/导出用程序；输入字节工程估计超限显示待细分，不隐式丢片段。

## 仍为目标架构
PostgreSQL + 对象存储 + 独立队列、局部高分辨率图纸视觉、完整CAD语义、自动设计净量、混合检索和跨专业关系。不要因为本地适配器已经运行就把这些标implemented。实现选择与回滚见ADR-0004，功能边界见IMPLEMENTATION_STATUS。

## Cloudflare测试入口
`浏览器 -> Pages Worker(可选) -> Cloudflare Tunnel -> 127.0.0.1 Python -> SQLite/文件`。
Pages不运行现有长任务或SQLite；本机必须在线。源站PreviewAccess全路径鉴权，Pages以独立secret调用/api。全路径包含规则、Fail closed及固定源站避免绕过或任意代理。静态构建白名单，只有HTML/CSS/JS和Worker。没有把10GB客户资料作为Pages资产。
