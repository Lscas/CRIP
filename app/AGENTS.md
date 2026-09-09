# 应用代码
先读任务包列出的函数／测试，不读整个app。
FastAPI仅本地单用户，SQLite事务和文件锁；不得无保护暴露公网；远程由remote_preview或render_preview入口启用严格Host、随机密码和mock；Render仅公开无业务信息的/_health GET/HEAD。数据库路径不进Git。
所有模型HTTP只在gateway.py。付费请求先预留；未知费用保持预留且暂停；不得自动重试收费。模型输出与人工审核分离。状态/证据/设计量不变量见根AGENTS。
定向：`python scripts/run_checks.py --area api|gateway|parsers`。修改SQLite迁移或资金/状态机需review级别；不得用低价代理自行改业务政策。
