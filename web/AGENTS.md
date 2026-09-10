# 本地网页
无需构建：index.html + style.css + i18n.js + app.js，不引入前端框架。
界面中文/English显示切换仅使用本地词典，默认中文，localStorage只存`cirp.ui.language.v1`；不消耗模型token。项目数据、用户输入、JSON、证据和导出保持原样。
动态应用文字用CIRPI18n.bindText/bindMessage/bindStatus；不遍历替换客户文本，不用innerHTML插入不可信数据。
切换语言不要刷新页面、调用refresh/API、重建form/textarea或修改审核值。数据和值保持原样；语言入口在页头、弹窗和抽屉。
定向检查：python scripts/run_checks.py --area ui；浏览器：python scripts/browser_i18n_smoke.py（受限环境用--offline-dom并说明存储和导航限制）。
