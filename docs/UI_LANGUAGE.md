# 中文 / English 界面切换
规格／应用版本：0.2.5。需求：FR-UI-002。

## 使用
页头“界面语言 / Display language”提供“中文”和“English”。选择后立即切换，无需刷新。新建项目弹窗与审核侧栏也有同样的选择器，因此不必关闭未保存的编辑去切换语言。
默认中文，不自动根据浏览器语言改变用户原有界面。偏好存储在当前浏览器、当前站点的`localStorage`键`cirp.ui.language.v1`；刷新或重新访问该站点后恢复。更换浏览器、端口或Cloudflare临时域名属于不同站点，可能需要重新选择。存储被禁用时仍可在当前页面切换，但不能保证下次恢复。

## 只翻译什么
页面标题、导航、按钮、表头、空状态、表单标签/占位提示、既有状态的显示标签、已知的系统警告和运行提示。后台状态仍是`PARTIAL`、`ACCEPTED`等原始代码，例如英文显示`Partial (PARTIAL)`，中文显示`部分完成 (PARTIAL)`。
语言按钮是显示设置，不是分析语言设置。此版本不新增“翻译项目文件/模型结果”的功能。

## 明确保留原样
- 项目名、上传文件名、证据原文、修订日期、型号、品牌、分析候选和用户审核说明。
- 候选编辑器中的JSON、审核历史JSON、Coverage/能力/费用原始诊断JSON。
- 数量、单位、币种、数据格式、请求载荷和后台状态。中文切换为英文不会把米换成英尺，也不把CNY换成美元。
- JSON/Excel导出的字段、表名、内容和来源。按钮名称可翻译，文件仍使用原有导出规则。
- 未知服务端错误原文。固定的已知诊断使用本地词典翻译；未知异常不能猜译或掩盖。

## 行为边界
只更新受控UI节点的`textContent`、标签及`html.lang`，不全局替换DOM文本，不替换或重建表单输入。保留选中项目、运行、结果标签页、筛选字串、上传任务、未保存候选与备注、按钮禁用状态和审核状态。切换不触发refresh、分析或任何API请求。
无需新的运行时依赖，不调用LLM、不产生翻译token。不修改模型Prompt、业务算法、预算、自动Takeoff、权限、审核、上传与导出逻辑；应用版本号正常升级为0.2.5。

## 文件与测试
`web/i18n.js`保存双语词典、偏好与受控动态绑定；`web/index.html`只给UI文字添加翻译标记；`web/app.js`在展示层调用词典。Cloudflare资产白名单增加i18n.js，不开放私密目录。

```bash
python scripts/run_checks.py --area ui
python scripts/run_checks.py --area all
python scripts/browser_i18n_smoke.py
# 浏览器URL受环境策略限制时使用纯离线DOM测试：
python scripts/browser_i18n_smoke.py --offline-dom
```

纯离线DOM模式通过实际后端TestClient处理请求，存储使用测试替身；不代表原生浏览器存储/下载或Windows已验收。查看本轮真实结果：`docs/UI_LANGUAGE_VALIDATION.md`。

## 升级和回滚
本地启动命令不变：Windows双击`start-local.cmd`；macOS/Linux运行`./start-local.sh`。
停止旧服务后更新代码，保留自己的`.local/`、`.env`、上传文件和预算数据库；不需要数据库迁移。只在下载目录打开HTML不能代替运行后端。
Git回滚参考`v0.2.4-local`。回滚不删除数据库或重置预算；语言偏好只有浏览器显示用途，对旧程序无影响。
