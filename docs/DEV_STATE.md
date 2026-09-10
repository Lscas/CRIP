# 当前开发状态：v0.2.5 / ui-language
已增加中文与English界面切换：页头、弹窗和抽屉可操作，偏好保存在浏览器，动态系统提示随语言切换；原始业务数据、API和导出不变。
本轮只改前端显示、Cloudflare静态资源白名单、测试与规格，未调用模型、未连接用户电脑、未部署远端。验证报告：docs/UI_LANGUAGE_VALIDATION.md。
继续使用start-local.cmd/start-local.sh。升级保留.local、.env；无需数据库迁移。业务下一任务仍DEV-002，不把显示改动当作选项映射功能完成。
定向：python scripts/run_checks.py --area ui；完整回归：--area all。默认不读整个仓库。
