# v0.2.5 显示语言实际验证

> Historical validation record. D-25 supersedes the bilingual runtime behavior; current release checks require English-only application presentation.

## 本轮范围
在v0.2.4-local完整Git基线上增加FR-UI-002。工作分支`feature/ui-language`。未修改用户电脑、账户配置或远端仓库，未创建云资源。

## 实际结果
| 检查 | 结果 |
|---|---|
| Python原有回归及新增显示契约测试 | 244项，失败0，错误0，跳过0 |
| Node本地词典与切换组件测试 | 23项通过 |
| 既有Cloudflare Worker测试 | 11项通过 |
| JavaScript语法 | i18n.js、app.js通过 |
| 离线DOM与真实后端TestClient | 13项场景断言通过，JavaScript错误0 |
| 语言切换产生的API请求 | 0 |
| 真实LLM/OCR/CAD调用 | 0 |

测试详情见`reports/i18n/pytest.xml`、`node-i18n.log`、`node-worker.log`、`browser-result.json`与`summary.json`。

## 已检查的不变性
`app/`、`contracts/`、`config/`、`migrations/`、运行依赖、密钥模板与旧版本逐文件Git比对没有变化。模型Prompt正文不变；Prompt manifest仅随发行版本更新元数据。Schema仅将0.2.5加入spec_version允许列表，不改变业务字段。
前端API路径、写请求头、POST内容、审核状态和导出操作保持原协议。切换前后项目/运行/候选/费用、上传状态、按钮可用状态、未保存项目名/候选JSON/说明、筛选、原始证据和定位不变。
同一次运行在两种界面语言下导出的JSON（排除每次导出生成时间）以及XLSX所有`xl/`工作簿部件相同。原文件下载端点字节与合成上传文件一致。

## 浏览器验证边界
当前Chromium访问测试网址返回`ERR_BLOCKED_BY_ADMINISTRATOR`，没有绕过浏览器策略。随后使用无网络DOM＋TestClient内存桥接，真实HTML/CSS/JS在Chromium中执行。localStorage使用测试替身，在新DOM重建时恢复偏好；原生跨刷新localStorage、浏览器下载导航、HTTP页面CSP执行、Windows实机没有验证。
导出端点、按钮绑定和下载响应已通过后端验证，不把它称为真实浏览器下载验收。截图是实际离线渲染，不是设计效果图。中英两种显示均在390px、760px、1024px测试，无页面级横向溢出；窄屏表格在其自身容器内横向滚动。

## 轮询绑定
显示绑定使用WeakMap，仅遍历仍在文档中的标记节点，避免长期运行时持有已被轮询替换的结果行。

## 修复记录
首次集成回归发现新版元数据0.2.5未加入既有Schema枚举，导致发布候选被拒绝。已只追加版本值，保留全部历史版本和业务约束，并新增版本支持测试；上方结果来自修复后的完整重跑，没有删除或降低原测试。

## 未改变的能力限制
不增加OCR、图纸视觉、DWG、Takeoff或施工准确率。原型仍显示PARTIAL。未调用真实模型，不把界面测试作为工程准确率、10GB吞吐或24小时处理能力证明。开发者工具页、操作系统文件选择器和原始技术JSON不属于本次翻译范围。

## 本机复验
```bash
python scripts/check_spec_sync.py
python scripts/run_checks.py --area all
python scripts/browser_i18n_smoke.py
```
网址访问受限环境使用`--offline-dom`并保留上述限制说明。Windows继续使用`start-local.cmd`；旧数据目录和.env不得覆盖或删除。
