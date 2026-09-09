# v0.2.1 实际验证报告

本轮交付可运行的本地原型第一切片。规格版本0.2.1；数据契约结构保持0.2.0兼容，元数据版本允许0.2.1。所有原始v0.2.0文件保留在Git基线。

## 执行结果
| 检查 | 实际结果 |
|---|---|
| Pytest离线测试 | 161项，0失败，0错误，0跳过 |
| Pytest耗时 | 20.418秒，仅测试耗时，不是项目分析性能 |
| 规格/Traceability/Prompt/Schema检查 | 通过，90项需求、12个Schema、8份实际任务Prompt |
| JavaScript语法 | node --check web/app.js 通过 |
| Python编译 | compileall app scripts 通过 |
| 离线浏览器DOM集成 | 项目创建、文件选择上传、模拟分析、数量展示、接受审核、来源查看通过；JavaScript运行错误0 |
| 手机布局 | 390px视口，根页面scrollWidth=390，无根页面横向溢出 |
| 真实模型/OCR/CAD请求 | 0次；没有Key、没有付费调用 |

测试明细：reports/pytest-v0.2.1.xml、reports/test-run-v0.2.1.txt、reports/spec-check-v0.2.1.txt。依赖和平台：reports/environment.json。

## 验证过的保护
分片重放/哈希不一致、上传超限、空项目、单活跃运行、内部修订日期与上传先后无关、输入快照不可变、未知格式不丢失、人工审核版本冲突、证据非法引用、Excel公式注入防护、低价非思考请求、缓存无重复HTTP、未配置不请求、原子预算竞态、超时保留预留、不自动重试、usage缺失、截断/错误JSON/假证据阻止发布、意外思考响应暂停。

## 浏览器方式与限制
执行环境的Chromium访问本地URL返回ERR_BLOCKED_BY_ADMINISTRATOR，没有绕过浏览器策略。随后使用无网络DOM测试：真实HTML/CSS/JS，window.fetch经内存桥接到FastAPI TestClient，使用独立临时数据库和合成文件；所有实际网络请求被禁止。
因此上述结果证明离线DOM与API集成，不证明用户本机浏览器到实际HTTP服务器已验证。原文件下载和JSON/XLSX内容通过API集成测试；真实浏览器下载链路需本机复验。`scripts/browser_smoke.py`可复现离线DOM流程（需要可用Chromium和Playwright）。

## 没有验证的事项
Windows实机安装、干净环境依赖解析、真实API/model能力、供应商真实计费、施工语义准确率、任意10GB吞吐、24h完成目标、复杂图纸、OCR/视觉、DWG、几何Takeoff、生产鉴权/恶意文件扫描、多租户隔离。默认PARTIAL不能作为最终工程审查完成声明。

## 开发工具与费用
上下文脚本已实测输出字符量，见reports/codex-context-size.json；不是实际Codextoken账单，不能宣称节省百分比。没有启动用户Codex，没有改账户模型，没有实际委派其他模型。尝试安装可选formatter受网络DNS限制未完成，不列为通过，也不影响应用运行和上述测试。

## 修复记录
开发中发现并修复文档写入列数、测试包同名导入、版本元数据兼容和浏览器异步等待问题；最终以上测试为修复后重新执行结果。基本测试未被删除以通过检查。需求全专业目标仍planned，只将对应的实际基础组件标implemented。
