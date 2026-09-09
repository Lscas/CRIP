# 本地网页
当前无构建步骤：index.html + style.css + app.js。不要为小改动引入框架重写。
中文、响应式、用textContent渲染客户文本，不插入不可信HTML。API写入保留同源和X-CIRP-Client校验。显著显示MOCK/PARTIAL/待人工审核。
改完执行 `node --check web/app.js` 和 API契约测试；浏览器测试见 scripts/browser_smoke.py，受限环境未运行必须说明。
